-- Company Brain — Supabase schema
-- Run this once in the Supabase SQL editor (or via `psql`) to provision the database.

-- ---------------------------------------------------------------------------
-- Extensions
-- ---------------------------------------------------------------------------
-- pgvector provides the `vector` type and similarity operators (<->, <=>, <#>).
-- Supabase ships pgvector but it is not enabled by default in new projects.
create extension if not exists vector;

-- pg_trgm powers the fuzzy `similarity()` function used by the Slack bot when
-- it asks "do we already have a workflow for something close to this name?".
create extension if not exists pg_trgm;


-- ---------------------------------------------------------------------------
-- chunks
-- ---------------------------------------------------------------------------
-- One row per embedded text chunk. The ingest pipeline writes here; the API
-- reads via match_chunks() at query time.
create table if not exists chunks (
    -- Stable identifier we can reference from skills.sources and from logs.
    id           uuid           primary key default gen_random_uuid(),

    -- Where the chunk came from. Free-form text rather than an enum so we can
    -- add new source types (github, jira, gdrive…) without a migration.
    source_type  text           not null,

    -- Human-readable origin: Slack channel name, Notion page title, PDF
    -- filename, etc. Used for citation rendering in the UI.
    source_name  text           not null,

    -- The chunked text that was embedded. Kept verbatim so we can show it
    -- back to the user as a source snippet.
    content      text           not null,

    -- 1024-dim embedding. Matches Voyage voyage-3 and voyage-3-large
    -- (default output_dimension). Change the dimension here AND in the
    -- match_chunks signature below if you swap models.
    embedding    vector(1024),

    -- Anything else worth keeping: author, timestamp from the source system,
    -- URL, page number, message ts, etc. Queryable via JSONB operators.
    metadata     jsonb          not null default '{}'::jsonb,

    -- Ingest time (not source time — that lives in metadata).
    created_at   timestamptz    not null default now()
);


-- ---------------------------------------------------------------------------
-- skills
-- ---------------------------------------------------------------------------
-- Generated process documents. Each row is a "how we do X" skill that the
-- API distilled from chunks. Surfaced to the user as Markdown skill files.
create table if not exists skills (
    id              uuid          primary key default gen_random_uuid(),

    -- Short slug-like name, e.g. "onboard-new-hire".
    process_name    text          not null,

    -- One-line summary surfaced in listings and frontmatter.
    description     text          not null default '',

    -- What kicks off this process. Named process_trigger because TRIGGER is
    -- a Postgres reserved word and the unquoted form would refuse to parse.
    -- The /workflows/generate JSON exposes this field as `trigger` — the
    -- store layer maps between the two.
    process_trigger text          not null default '',

    -- Ordered list of {step, action, owner, notes}. JSONB so we can render
    -- structured steps in the UI without re-parsing Markdown.
    steps           jsonb         not null default '[]'::jsonb,

    -- if-this-then-that statements grounded in the source material.
    decision_rules  jsonb         not null default '[]'::jsonb,

    -- Authorization gates ("CFO must sign off on credits over 1 cycle").
    approvals       jsonb         not null default '[]'::jsonb,

    -- Scenarios where the default process does not apply.
    exceptions      jsonb         not null default '[]'::jsonb,

    -- Source labels (e.g. "slack:engineering-incidents", "notion:Refund Policy").
    sources         jsonb         not null default '[]'::jsonb,

    -- Where the workflow was created from: "manual" (user pasted), "slack"
    -- (extracted from a thread by the Slack bot), etc. Free-form text.
    source          text          not null default 'manual',

    -- Provenance details — channel/thread/triggering user for Slack-sourced
    -- workflows; empty object for manual ones. Queryable via JSONB operators.
    source_metadata jsonb         not null default '{}'::jsonb,

    -- "Update existing" archives the previous version rather than deleting,
    -- so we keep a history of how a process changed over time.
    archived        boolean       not null default false,
    archived_at     timestamptz,

    -- Original input text the workflow was distilled from. Preserved so the
    -- /brain/[id] page can offer a "Re-extract" button that re-runs the
    -- generation against the same source material.
    raw_text        text          not null default '',

    -- Set by the /brain UI's "Mark as reviewed" button. Null = unreviewed.
    reviewed_at     timestamptz,

    generated_at    timestamptz   not null default now()
);

-- Idempotent migrations for installations created before the workflow fields
-- existed. Safe to re-run; each ALTER is a no-op once the column is in place.
alter table skills add column if not exists process_trigger text not null default '';
alter table skills add column if not exists decision_rules  jsonb not null default '[]'::jsonb;
alter table skills add column if not exists approvals       jsonb not null default '[]'::jsonb;
alter table skills add column if not exists exceptions      jsonb not null default '[]'::jsonb;
alter table skills add column if not exists source          text  not null default 'manual';
alter table skills add column if not exists source_metadata jsonb not null default '{}'::jsonb;
alter table skills add column if not exists archived        boolean not null default false;
alter table skills add column if not exists archived_at     timestamptz;
alter table skills add column if not exists raw_text        text not null default '';
alter table skills add column if not exists reviewed_at     timestamptz;
alter table skills alter column description set default '';

-- GIN trigram index makes similarity(process_name, ?) searches cheap.
-- Used by the find_similar_workflow RPC below (Slack bot's "Update existing"
-- detection). Optional — without it queries still work, just slower at scale.
create index if not exists skills_process_name_trgm_idx
    on skills
    using gin (process_name gin_trgm_ops);

create index if not exists skills_archived_idx on skills (archived);


-- ---------------------------------------------------------------------------
-- Indexes
-- ---------------------------------------------------------------------------
-- IVFFlat index for approximate-nearest-neighbour search on cosine distance.
--
-- `vector_cosine_ops` makes the index serve the `<=>` (cosine distance)
-- operator that match_chunks uses below. Use vector_l2_ops or vector_ip_ops
-- if you switch to Euclidean / inner-product instead.
--
-- `lists = 100` is a reasonable starting point for tens-of-thousands of rows.
-- Rule of thumb: lists ≈ sqrt(rows). Bump it as the corpus grows and rebuild
-- the index. IVFFlat must be built AFTER you have data for best results — if
-- you build on an empty table the centroids are meaningless. Re-create with
-- `reindex index chunks_embedding_idx;` after a large initial ingest.
create index if not exists chunks_embedding_idx
    on chunks
    using ivfflat (embedding vector_cosine_ops)
    with (lists = 100);

-- Cheap btree indexes for the filters the API is likely to add later
-- (e.g. "only search Notion", "only chunks from this channel").
create index if not exists chunks_source_type_idx on chunks (source_type);
create index if not exists chunks_source_name_idx on chunks (source_name);


-- ---------------------------------------------------------------------------
-- match_chunks(query_embedding, match_count)
-- ---------------------------------------------------------------------------
-- Returns the top `match_count` chunks ranked by cosine similarity to
-- `query_embedding`. Called from the API via Supabase RPC:
--
--   client.rpc("match_chunks", {
--     "query_embedding": [...],
--     "match_count": 5,
--   })
--
-- Notes:
--   * `<=>` is cosine *distance* (0 = identical, 2 = opposite). We return
--     `1 - distance` so callers can treat higher = better.
--   * `set ivfflat.probes` trades recall for latency. Higher = better recall,
--     slower query. 10 is a sensible default for lists = 100.
create or replace function match_chunks(
    query_embedding vector(1024),
    match_count     int
)
returns table (
    id          uuid,
    source_type text,
    source_name text,
    content     text,
    metadata    jsonb,
    similarity  float
)
language sql
stable
as $$
    select
        c.id,
        c.source_type,
        c.source_name,
        c.content,
        c.metadata,
        1 - (c.embedding <=> query_embedding) as similarity
    from chunks c
    where c.embedding is not null
    order by c.embedding <=> query_embedding
    limit match_count;
$$;


-- ---------------------------------------------------------------------------
-- find_similar_workflow(q_name, min_sim, exclude_id)
-- ---------------------------------------------------------------------------
-- Returns the most-similar non-archived workflow whose process_name has
-- trigram similarity >= min_sim with q_name. Used by the Slack bot to detect
-- when a freshly-extracted workflow likely supersedes an existing one
-- ("Update existing" button).
--
-- min_sim of 0.4 catches "Customer refund handling" matching "Refund flow"
-- without overmatching unrelated phrases. Tune empirically.
create or replace function find_similar_workflow(
    q_name      text,
    min_sim     float default 0.4,
    exclude_id  text  default ''
)
returns table (
    id              uuid,
    process_name    text,
    description     text,
    process_trigger text,
    steps           jsonb,
    decision_rules  jsonb,
    approvals       jsonb,
    exceptions      jsonb,
    sources         jsonb,
    source          text,
    source_metadata jsonb,
    archived        boolean,
    archived_at     timestamptz,
    generated_at    timestamptz,
    similarity      float
)
language sql
stable
as $$
    select
        s.id,
        s.process_name,
        s.description,
        s.process_trigger,
        s.steps,
        s.decision_rules,
        s.approvals,
        s.exceptions,
        s.sources,
        s.source,
        s.source_metadata,
        s.archived,
        s.archived_at,
        s.generated_at,
        similarity(s.process_name, q_name) as similarity
    from skills s
    where s.archived = false
      and (exclude_id = '' or s.id::text <> exclude_id)
      and similarity(s.process_name, q_name) >= min_sim
    order by similarity(s.process_name, q_name) desc
    limit 1;
$$;
