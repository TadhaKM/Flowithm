-- Flowithm — Supabase schema
-- Run this once in the Supabase SQL editor (or via `psql`) to provision the
-- database. Re-runnable: every CREATE / ALTER is idempotent.

-- ---------------------------------------------------------------------------
-- organisations (multi-tenancy root)
-- ---------------------------------------------------------------------------
-- Every domain row is owned by exactly one organisation. RLS is enabled on
-- the major tables but no policies are defined yet — the FastAPI service
-- role bypasses RLS and we enforce org_id filtering in application code.
-- TODO before public launch: add RLS policies that scope reads/writes to
-- a session-set org_id and switch FastAPI to use anon + JWT instead of the
-- service role key.
create table if not exists organisations (
    id          uuid          primary key default gen_random_uuid(),
    name        text          not null,
    slug        text          not null unique,
    plan        text          not null default 'free' check (plan in ('free', 'pro', 'enterprise')),
    created_at  timestamptz   not null default now()
);

-- Bootstrap a default organisation with a fixed UUID. Single-tenant /
-- self-hosted deploys point ORG_ID at this row in their .env, and existing
-- pre-multi-tenancy rows get backfilled to it below.
insert into organisations (id, name, slug, plan)
values ('00000000-0000-0000-0000-000000000001'::uuid, 'Default', 'default', 'free')
on conflict (id) do nothing;

-- ---------------------------------------------------------------------------
-- users (Supabase Auth → organisation link)
-- ---------------------------------------------------------------------------
-- Maps Supabase Auth users to organisations. The primary key references
-- auth.users(id) so deleting an auth user cascades here. org_id is the
-- tenant this user belongs to — used by the dashboard to resolve which
-- organisation's data to show.
create table if not exists users (
    id            uuid          primary key references auth.users(id) on delete cascade,
    org_id        uuid          not null references organisations(id),
    display_name  text          not null default '',
    email         text          not null,
    created_at    timestamptz   not null default now()
);
create index if not exists users_org_idx on users (org_id);
alter table users enable row level security;


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
-- org_id is also (re-)added below in the multi-tenancy block; we add it here
-- early so the unique index on (org_id, content_hash) can reference it.
alter table chunks add column if not exists org_id       uuid references organisations(id);
-- C-1: the old global index let Org B's upsert overwrite Org A's row if
-- they ingested the same content. Scope dedup to the tenant.
drop index if exists chunks_content_hash_idx;
create unique index if not exists chunks_org_content_hash_idx on chunks (org_id, content_hash);


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

-- Staleness: brain/staleness.run_staleness_check flips needs_review when a
-- skill has no reviewed_at and is older than $STALE_THRESHOLD_DAYS, or when
-- the most-recent review is older than the threshold. Cleared by the same
-- function (and by mark_as_reviewed) so reviewing a flagged skill doesn't
-- need a separate "unflag" step.
alter table skills add column if not exists needs_review        boolean default false;
alter table skills add column if not exists needs_review_reason text;
alter table skills add column if not exists stale_flagged_at    timestamptz;
create index if not exists skills_needs_review_idx
    on skills (needs_review)
    where needs_review = true;

-- GIN trigram index makes similarity(process_name, ?) searches cheap.
-- Used by the find_similar_workflow RPC below (Slack bot's "Update existing"
-- detection). Optional — without it queries still work, just slower at scale.
create index if not exists skills_process_name_trgm_idx
    on skills
    using gin (process_name gin_trgm_ops);

create index if not exists skills_archived_idx on skills (archived);

-- org_id is also (re-)added below in the multi-tenancy block; we add it here
-- early so the unique index on (org_id, lower(process_name)) can reference it.
alter table skills add column if not exists org_id uuid references organisations(id);

-- Heal pre-existing accept-conflict partial failures before the H-4 unique
-- index gets created: if more than one non-archived skill shares
-- (org_id, lower(process_name)), keep the newest (by generated_at, then id
-- as tiebreaker) and archive the rest. Idempotent — a no-op once the
-- constraint below is in place and enforcing uniqueness on new writes.
update skills
   set archived    = true,
       archived_at = coalesce(archived_at, now())
 where archived = false
   and id in (
       select id from (
           select id,
                  row_number() over (
                      partition by org_id, lower(process_name)
                      order by generated_at desc, id desc
                  ) as rn
             from skills
            where archived = false
       ) ranked
       where rn > 1
   );

-- H-4: backstop constraint — prevents two non-archived skills with the
-- same process_name in one org (detects accept-path partial failures).
create unique index if not exists skills_org_process_active_idx
    on skills (org_id, lower(process_name))
    where archived = false;


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
-- C-2: direct org column for defense-in-depth (no JOIN needed to scope).
alter table api_requests add column if not exists org_id uuid references organisations(id);
-- M-11: FK so dangling pointers are cleaned up on skill deletion.
do $$ begin
    alter table api_requests drop constraint if exists api_requests_matched_skill_id_fkey;
    alter table api_requests add constraint api_requests_matched_skill_id_fkey
        foreign key (matched_skill_id) references skills(id) on delete set null;
end $$;

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

-- H-6: FK cascade fixes — prevents clear_workflows from breaking on
-- existing conflicts/executions. Idempotent: drop if exists, re-add.
do $$ begin
    -- executions.skill_id → ON DELETE CASCADE
    alter table executions drop constraint if exists executions_skill_id_fkey;
    alter table executions add constraint executions_skill_id_fkey
        foreign key (skill_id) references skills(id) on delete cascade;
    -- conflicts.existing_skill_id → ON DELETE CASCADE
    alter table conflicts drop constraint if exists conflicts_existing_skill_id_fkey;
    alter table conflicts add constraint conflicts_existing_skill_id_fkey
        foreign key (existing_skill_id) references skills(id) on delete cascade;
    -- conflicts.new_skill_id → ON DELETE SET NULL
    alter table conflicts drop constraint if exists conflicts_new_skill_id_fkey;
    alter table conflicts add constraint conflicts_new_skill_id_fkey
        foreign key (new_skill_id) references skills(id) on delete set null;
    -- skills.previous_version_id → ON DELETE SET NULL
    alter table skills drop constraint if exists skills_previous_version_id_fkey;
    alter table skills add constraint skills_previous_version_id_fkey
        foreign key (previous_version_id) references skills(id) on delete set null;
end $$;


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

-- Per-cycle staleness counters; populated by brain/staleness.run_staleness_check
-- which the scheduler calls at the end of every ingest cycle.
alter table ingest_runs add column if not exists stale_flagged integer not null default 0;
alter table ingest_runs add column if not exists stale_cleared integer not null default 0;
-- M-15: true when errors[] is non-empty, surfaced by /health.last_ingest.
alter table ingest_runs add column if not exists errored boolean not null default false;


-- ---------------------------------------------------------------------------
-- ingest_lock — multi-worker mutex
-- ---------------------------------------------------------------------------
-- Originally intended as pg_try_advisory_lock(12345) but Supabase's default
-- pgbouncer runs in transaction-pool mode, which doesn't preserve
-- session-scoped advisory locks across the two RPC calls (acquire + release)
-- that supabase-py has to make. The lock would auto-release when the
-- acquire RPC's connection returned to the pool, defeating the purpose.
--
-- Same semantics achieved with a singleton row-mutex: only one worker holds
-- it at a time, and a 15-minute timeout reclaims the lock if a worker dies
-- mid-cycle so the system self-heals.
create table if not exists ingest_lock (
    id         integer       primary key default 1,
    locked_at  timestamptz,
    locked_by  text,
    constraint ingest_lock_singleton check (id = 1)
);
insert into ingest_lock (id) values (1) on conflict (id) do nothing;

-- Returns true iff this caller acquired the lock. Atomic — UPDATE WHERE
-- handles the race directly. A stale lock older than 15 minutes is treated
-- as free.
create or replace function try_acquire_ingest_lock(holder text) returns boolean
language plpgsql
as $$
begin
    update ingest_lock
    set locked_at = now(), locked_by = holder
    where id = 1
      and (locked_at is null or locked_at < now() - interval '15 minutes');
    return found;
end;
$$;

-- B-5: holder-predicated release — prevents a late-finishing original
-- holder from wiping a new holder's lock after a stale-reclaim.
create or replace function release_ingest_lock(holder text) returns void
language sql
as $$
    update ingest_lock set locked_at = null, locked_by = null
    where id = 1 and locked_by = holder;
$$;


-- ---------------------------------------------------------------------------
-- Combined audit: bump counter + insert request row in one round-trip.
-- ---------------------------------------------------------------------------
create or replace function log_api_request(
    p_key_id         uuid,
    p_endpoint       text,
    p_response_ms    integer,
    p_query_text     text     default null,
    p_matched_skill  uuid     default null,
    p_org_id         uuid     default null
) returns void
language sql
as $$
    update api_keys
       set request_count = request_count + 1, last_used_at = now()
     where id = p_key_id;
    insert into api_requests (api_key_id, endpoint, response_time_ms, query_text, matched_skill_id, org_id)
    values (p_key_id, p_endpoint, p_response_ms, p_query_text, p_matched_skill, p_org_id);
$$;


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
    match_count     int,
    target_org_id   uuid default null
)
returns table (
    id                  uuid,
    process_name        text,
    description         text,
    process_trigger     text,
    steps               jsonb,
    decision_rules      jsonb,
    approvals           jsonb,
    exceptions          jsonb,
    sources             jsonb,
    source              text,
    version             integer,
    generated_at        timestamptz,
    needs_review        boolean,
    needs_review_reason text,
    similarity          float
)
language plpgsql
volatile
as $$
begin
    -- P-6: bump probes from default 1 → 10 so IVFFlat scans ~10% of
    -- centroids instead of ~1%. Recall jumps from ~60% to ~95% at 100K rows.
    set local ivfflat.probes = 10;
    return query select
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
        coalesce(s.needs_review, false) as needs_review,
        s.needs_review_reason,
        1 - (s.summary_embedding <=> query_embedding) as similarity
    from skills s
    where s.archived = false
      and s.summary_embedding is not null
      and (target_org_id is null or s.org_id = target_org_id)
    order by s.summary_embedding <=> query_embedding
    limit match_count;
end;
$$;


-- ---------------------------------------------------------------------------
-- accept_conflict — transactional drift-accept (B-1, revised)
-- ---------------------------------------------------------------------------
-- Archives the old skill FIRST (so the unique-active-name constraint is
-- satisfied), then INSERTs the new skill, then cascades siblings and marks
-- the conflict accepted — all in one transaction. Returns the new skill's
-- UUID so the caller can build its response.
create or replace function accept_conflict(
    p_old_skill_id        uuid,
    p_conflict_id         uuid,
    p_new_version         integer,
    p_resolved_by         text,
    p_org_id              uuid,
    -- new skill fields
    p_process_name        text,
    p_description         text,
    p_process_trigger     text,
    p_steps               jsonb,
    p_decision_rules      jsonb,
    p_approvals           jsonb,
    p_exceptions          jsonb,
    p_sources             jsonb,
    p_source              text,
    p_source_metadata     jsonb,
    p_raw_text            text,
    p_summary_embedding   vector(1024) default null
) returns uuid
language plpgsql
as $$
declare
    v_new_id uuid;
begin
    -- 1. Archive the old skill FIRST to satisfy the unique-active-name index
    update skills
       set archived = true,
           archived_at = now()
     where id = p_old_skill_id;

    -- 2. INSERT the new skill with version + lineage already set
    insert into skills (
        process_name, description, process_trigger, steps, decision_rules,
        approvals, exceptions, sources, source, source_metadata, raw_text,
        version, previous_version_id, org_id, summary_embedding
    ) values (
        p_process_name, p_description, p_process_trigger, p_steps,
        p_decision_rules, p_approvals, p_exceptions, p_sources, p_source,
        p_source_metadata, p_raw_text, p_new_version, p_old_skill_id,
        p_org_id, p_summary_embedding
    ) returning id into v_new_id;

    -- 3. Cascade: re-target unresolved sibling conflicts to the new version
    update conflicts
       set existing_skill_id = v_new_id
     where existing_skill_id = p_old_skill_id
       and status = 'unresolved'
       and org_id = p_org_id
       and id != p_conflict_id;

    -- 4. Mark this conflict accepted
    update conflicts
       set status       = 'accepted',
           new_skill_id = v_new_id,
           resolved_by  = p_resolved_by,
           resolved_at  = now()
     where id = p_conflict_id;

    return v_new_id;
end;
$$;


-- ---------------------------------------------------------------------------
-- Multi-tenancy: org_id columns + backfill + indexes + RLS enable
-- ---------------------------------------------------------------------------
-- Every domain row gets an org_id. Nullable here so the ALTERs are safe to
-- re-run; the backfill below points every existing row at the default org.
alter table skills            add column if not exists org_id uuid references organisations(id);
alter table chunks            add column if not exists org_id uuid references organisations(id);
alter table conflicts         add column if not exists org_id uuid references organisations(id);
alter table connected_sources add column if not exists org_id uuid references organisations(id);
alter table api_keys          add column if not exists org_id uuid references organisations(id);
alter table ingest_runs       add column if not exists org_id uuid references organisations(id);
alter table executions        add column if not exists org_id uuid references organisations(id);

-- Backfill any pre-multi-tenancy rows to the default org so the org-aware
-- application code keeps surfacing them.
update skills            set org_id = '00000000-0000-0000-0000-000000000001'::uuid where org_id is null;
update chunks            set org_id = '00000000-0000-0000-0000-000000000001'::uuid where org_id is null;
update conflicts         set org_id = '00000000-0000-0000-0000-000000000001'::uuid where org_id is null;
update connected_sources set org_id = '00000000-0000-0000-0000-000000000001'::uuid where org_id is null;
update api_keys          set org_id = '00000000-0000-0000-0000-000000000001'::uuid where org_id is null;
update ingest_runs       set org_id = '00000000-0000-0000-0000-000000000001'::uuid where org_id is null;
update executions        set org_id = '00000000-0000-0000-0000-000000000001'::uuid where org_id is null;

-- L-15: org_id FKs → ON DELETE CASCADE so deleting an org cascades cleanly.
do $$ begin
    alter table skills            drop constraint if exists skills_org_id_fkey;
    alter table skills            add constraint skills_org_id_fkey
        foreign key (org_id) references organisations(id) on delete cascade;
    alter table chunks            drop constraint if exists chunks_org_id_fkey;
    alter table chunks            add constraint chunks_org_id_fkey
        foreign key (org_id) references organisations(id) on delete cascade;
    alter table conflicts         drop constraint if exists conflicts_org_id_fkey;
    alter table conflicts         add constraint conflicts_org_id_fkey
        foreign key (org_id) references organisations(id) on delete cascade;
    alter table connected_sources drop constraint if exists connected_sources_org_id_fkey;
    alter table connected_sources add constraint connected_sources_org_id_fkey
        foreign key (org_id) references organisations(id) on delete cascade;
    alter table api_keys          drop constraint if exists api_keys_org_id_fkey;
    alter table api_keys          add constraint api_keys_org_id_fkey
        foreign key (org_id) references organisations(id) on delete set null;
    alter table ingest_runs       drop constraint if exists ingest_runs_org_id_fkey;
    alter table ingest_runs       add constraint ingest_runs_org_id_fkey
        foreign key (org_id) references organisations(id) on delete cascade;
    alter table executions        drop constraint if exists executions_org_id_fkey;
    alter table executions        add constraint executions_org_id_fkey
        foreign key (org_id) references organisations(id) on delete cascade;
end $$;

create index if not exists skills_org_idx            on skills (org_id);
create index if not exists chunks_org_idx            on chunks (org_id);
create index if not exists conflicts_org_idx         on conflicts (org_id);
create index if not exists connected_sources_org_idx on connected_sources (org_id);
-- ---------------------------------------------------------------------------
-- run_staleness_pass — push the threshold comparison into SQL so Python
-- doesn't need to load every skill row into memory.
-- ---------------------------------------------------------------------------
create or replace function run_staleness_pass(
    p_org_id          uuid,
    p_threshold_days  integer
) returns table (flagged_count bigint, cleared_count bigint)
language plpgsql
as $$
declare
    v_flagged bigint;
    v_cleared bigint;
begin
    -- Flag: never reviewed AND old, or last review older than threshold.
    with flagged as (
        update skills
           set needs_review = true,
               needs_review_reason = 'Hasn''t been reviewed recently',
               stale_flagged_at = now()
         where archived = false
           and org_id = p_org_id
           and needs_review = false
           and (
               (reviewed_at is null and generated_at < now() - (p_threshold_days || ' days')::interval)
               or reviewed_at < now() - (p_threshold_days || ' days')::interval
           )
        returning id
    )
    select count(*) into v_flagged from flagged;

    -- Clear: reviewed recently but still flagged.
    with cleared as (
        update skills
           set needs_review = false,
               needs_review_reason = null,
               stale_flagged_at = null
         where archived = false
           and org_id = p_org_id
           and needs_review = true
           and (
               (reviewed_at is not null and reviewed_at >= now() - (p_threshold_days || ' days')::interval)
           )
        returning id
    )
    select count(*) into v_cleared from cleared;

    return query select v_flagged, v_cleared;
end;
$$;

-- M-13: composite for the scheduler's list_active_connected_sources query.
create index if not exists connected_sources_org_active_idx on connected_sources (org_id, is_active);
create index if not exists api_keys_org_idx          on api_keys (org_id);
create index if not exists ingest_runs_org_idx       on ingest_runs (org_id);

-- M-12: composite indexes for common multi-tenant queries.
create index if not exists conflicts_org_status_created_idx
    on conflicts (org_id, status, created_at desc);
create index if not exists skills_org_generated_active_idx
    on skills (org_id, generated_at desc)
    where archived = false;

-- L-14: prevent version chain from forking (two active skills pointing
-- at the same predecessor).
create unique index if not exists skills_previous_version_unique_idx
    on skills (previous_version_id)
    where previous_version_id is not null and archived = false;

-- RLS enabled with no policies — service role bypasses, anon role gets
-- nothing. Policies coming pre-launch (see TODO at top of file).
alter table skills            enable row level security;
alter table chunks            enable row level security;
alter table conflicts         enable row level security;
alter table connected_sources enable row level security;
alter table api_keys          enable row level security;


-- ---------------------------------------------------------------------------
-- Indexes
-- ---------------------------------------------------------------------------
-- IVFFlat index for approximate-nearest-neighbour search on cosine distance.
--
-- `vector_cosine_ops` makes the index serve the `<=>` (cosine distance)
-- operator that match_chunks uses below. Use vector_l2_ops or vector_ip_ops
-- if you switch to Euclidean / inner-product instead.
--
-- `lists = 100` is a reasonable starting point up to ~10K rows.
-- Rule of thumb: lists ≈ sqrt(rows). At 50K+ chunks, reindex with a
-- higher lists count or migrate to HNSW for better recall without tuning:
--   create index chunks_embedding_hnsw_idx on chunks
--       using hnsw (embedding vector_cosine_ops)
--       with (m = 16, ef_construction = 64);
-- IVFFlat must be built AFTER you have data — centroids on an empty
-- table are meaningless. Reindex after a large initial ingest:
--   reindex index chunks_embedding_idx;
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
    match_count     int,
    target_org_id   uuid default null
)
returns table (
    id          uuid,
    source_type text,
    source_name text,
    content     text,
    metadata    jsonb,
    similarity  float
)
language plpgsql
volatile
as $$
begin
    set local ivfflat.probes = 10;
    return query select
        c.id,
        c.source_type,
        c.source_name,
        c.content,
        c.metadata,
        1 - (c.embedding <=> query_embedding) as similarity
    from chunks c
    where c.embedding is not null
      and (target_org_id is null or c.org_id = target_org_id)
    order by c.embedding <=> query_embedding
    limit match_count;
end;
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
    q_name        text,
    min_sim       float default 0.4,
    exclude_id    text  default '',
    target_org_id uuid  default null
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
      and (target_org_id is null or s.org_id = target_org_id)
      and similarity(s.process_name, q_name) >= min_sim
    order by similarity(s.process_name, q_name) desc
    limit 1;
$$;
