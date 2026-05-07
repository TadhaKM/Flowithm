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

-- Idempotent migrations for chunks. Re-runnable; each ALTER is a no-op once
-- the column exists. content_hash + the unique index back the dedup logic
-- in brain/embedder.store_chunk: re-ingesting identical content updates
-- source_name + metadata + updated_at instead of creating a duplicate row.
alter table chunks add column if not exists content_hash text;
alter table chunks add column if not exists updated_at   timestamptz default now();
create unique index if not exists chunks_content_hash_idx on chunks (content_hash);


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

-- Versioning: when a drift "accept" lands, the new row gets version = old + 1
-- and previous_version_id = the archived row's id.
alter table skills add column if not exists version              integer default 1;
alter table skills add column if not exists previous_version_id  uuid references skills(id);

-- GIN trigram index makes similarity(process_name, ?) searches cheap.
-- Used by the find_similar_workflow RPC below (Slack bot's "Update existing"
-- detection). Optional — without it queries still work, just slower at scale.
create index if not exists skills_process_name_trgm_idx
    on skills
    using gin (process_name gin_trgm_ops);

create index if not exists skills_archived_idx on skills (archived);


-- ---------------------------------------------------------------------------
-- conflicts (drift detection)
-- ---------------------------------------------------------------------------
-- Drift records produced by brain/drift.check_for_drift after a new workflow
-- is generated. Each row is a contradiction/update/expansion/deprecation
-- that Claude detected against an existing non-archived skill. The /conflicts
-- API + UI panel surface 'unresolved' rows; resolve_conflict('accept') bumps
-- the existing skill's version and archives the previous record.
create table if not exists conflicts (
    id                       uuid           primary key default gen_random_uuid(),
    existing_skill_id        uuid           references skills(id),
    new_skill_id             uuid           references skills(id),
    existing_process_name    text           not null,
    conflict_type            text           not null check (conflict_type in (
        'contradiction', 'update', 'expansion', 'deprecation'
    )),
    conflict_description     text           not null,
    existing_rule            text           not null,
    new_evidence             text           not null,
    suggested_update         text           not null,
    severity                 text           not null check (severity in ('high', 'medium', 'low')),
    status                   text           not null default 'unresolved' check (status in (
        'unresolved', 'accepted', 'dismissed', 'snoozed'
    )),
    snoozed_until            timestamptz,
    resolved_by              text,
    resolved_at              timestamptz,
    created_at               timestamptz    not null default now()
);

create index if not exists conflicts_status_idx on conflicts (status);
create index if not exists conflicts_skill_idx  on conflicts (existing_skill_id);


-- ---------------------------------------------------------------------------
-- Public agent API: api_keys, api_requests, executions
-- ---------------------------------------------------------------------------
-- api_keys backs the Bearer-token auth for /api/v1/*. We never store the
-- plaintext key; key_hash is the bcrypt hash, key_prefix is the first 12
-- chars (also indexed) so verify_api_key() can short-list candidates
-- without scanning the whole table before bcrypt-comparing.
create table if not exists api_keys (
    id            uuid          primary key default gen_random_uuid(),
    key_hash      text          not null unique,
    key_prefix    text          not null,
    name          text          not null,
    created_at    timestamptz   not null default now(),
    last_used_at  timestamptz,
    request_count integer       not null default 0,
    is_active     boolean       not null default true
);
create index if not exists api_keys_prefix_idx on api_keys (key_prefix);

-- api_requests is the per-call audit log. Written from a BackgroundTask
-- after the response so request handlers don't pay the round-trip cost.
create table if not exists api_requests (
    id                uuid       primary key default gen_random_uuid(),
    api_key_id        uuid       references api_keys(id),
    endpoint          text       not null,
    query_text        text,
    matched_skill_id  uuid,
    response_time_ms  integer,
    created_at        timestamptz not null default now()
);
create index if not exists api_requests_key_idx     on api_requests (api_key_id);
create index if not exists api_requests_created_idx on api_requests (created_at);

-- executions: agent feedback when a workflow step runs. exception_note
-- triggers a drift check (via brain/drift) when Claude judges it as a
-- genuinely-new edge case not already covered.
create table if not exists executions (
    id               uuid       primary key default gen_random_uuid(),
    skill_id         uuid       references skills(id),
    step_number      integer,
    outcome          text       not null check (outcome in (
        'completed', 'escalated', 'exception_triggered'
    )),
    exception_note   text,
    duration_seconds integer,
    created_at       timestamptz not null default now()
);
create index if not exists executions_skill_idx on executions (skill_id);


-- ---------------------------------------------------------------------------
-- Continuous ingestion: connected_sources + ingest_runs
-- ---------------------------------------------------------------------------
-- connected_sources stores per-tenant credentials for live fetches. config
-- is opaque jsonb (per source_type) — Slack: { bot_token, channel_ids[] };
-- Notion: { integration_token, page_ids[] }; etc. Tokens are stored in
-- plaintext server-side (Supabase RLS keeps them out of the browser); the
-- /sources API redacts the config field for any caller before returning.
create table if not exists connected_sources (
    id              uuid          primary key default gen_random_uuid(),
    source_type     text          not null check (source_type in (
        'slack', 'notion', 'github', 'gmail', 'intercom'
    )),
    display_name    text          not null,
    config          jsonb         not null,
    last_synced_at  timestamptz,
    next_sync_at    timestamptz,
    is_active       boolean       not null default true,
    created_at      timestamptz   not null default now()
);
create index if not exists connected_sources_active_idx on connected_sources (is_active);

-- ingest_runs is the per-cycle audit log — same shape as the dict the
-- scheduler builds in run_ingest_cycle(), one row per scheduled or manual
-- trigger. errors is a jsonb array of "{source}: {message}" strings.
create table if not exists ingest_runs (
    id                uuid          primary key default gen_random_uuid(),
    started_at        timestamptz   not null,
    duration_seconds  integer,
    sources_checked   integer       not null default 0,
    new_chunks        integer       not null default 0,
    skipped_chunks    integer       not null default 0,
    new_conflicts     integer       not null default 0,
    errors            jsonb         not null default '[]'::jsonb,
    created_at        timestamptz   not null default now()
);
create index if not exists ingest_runs_started_idx on ingest_runs (started_at desc);


-- ---------------------------------------------------------------------------
-- Atomic counter bump for api_keys.request_count + last_used_at.
-- Called from the per-request BackgroundTask in api/auth.py. Doing this
-- as a single SQL statement avoids the read-modify-write race on counter.
-- ---------------------------------------------------------------------------
create or replace function increment_api_key_usage(key_id uuid)
returns void
language sql
as $$
    update api_keys
    set request_count = request_count + 1, last_used_at = now()
    where id = key_id;
$$;


-- ---------------------------------------------------------------------------
-- Skills summary embedding (powers /api/v1/skills/match)
-- ---------------------------------------------------------------------------
-- 1024 dims to match voyage-3 (and the existing chunks.embedding column).
-- Spec said 1536 but that's OpenAI ada-002 — would crash on insert here.
alter table skills add column if not exists summary_embedding vector(1024);

create index if not exists skills_summary_embedding_idx
    on skills using ivfflat (summary_embedding vector_cosine_ops)
    with (lists = 100);


-- ---------------------------------------------------------------------------
-- match_skills(query_embedding, match_count)
-- ---------------------------------------------------------------------------
-- Cosine ANN over skills.summary_embedding. Mirrors match_chunks but on
-- the skills corpus and only over non-archived rows. Used by the agent
-- API's /api/v1/skills/match endpoint.
create or replace function match_skills(
    query_embedding vector(1024),
    match_count     int
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
    version         integer,
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
        s.version,
        s.generated_at,
        1 - (s.summary_embedding <=> query_embedding) as similarity
    from skills s
    where s.archived = false
      and s.summary_embedding is not null
    order by s.summary_embedding <=> query_embedding
    limit match_count;
$$;


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
