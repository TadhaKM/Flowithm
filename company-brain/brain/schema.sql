-- Company Brain — Supabase schema
-- Run this once in the Supabase SQL editor (or via `psql`) to provision the database.

-- ---------------------------------------------------------------------------
-- Extensions
-- ---------------------------------------------------------------------------
-- pgvector provides the `vector` type and similarity operators (<->, <=>, <#>).
-- Supabase ships pgvector but it is not enabled by default in new projects.
create extension if not exists vector;


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
    id            uuid          primary key default gen_random_uuid(),

    -- Short slug-like name, e.g. "onboard-new-hire". Used as the skill
    -- filename when the API writes it to disk.
    process_name  text          not null,

    -- One-line summary shown in skill listings and in the skill frontmatter.
    description   text          not null,

    -- Ordered list of steps. JSONB (not text) so we can render structured
    -- step lists in the UI without re-parsing Markdown.
    steps         jsonb         not null default '[]'::jsonb,

    -- Array of chunk ids the skill was distilled from. Lets the UI link
    -- each skill back to its underlying evidence.
    sources       jsonb         not null default '[]'::jsonb,

    generated_at  timestamptz   not null default now()
);


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
