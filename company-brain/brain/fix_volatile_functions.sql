-- Fix: match_skills and match_chunks declared STABLE but use SET LOCAL.
-- PostgreSQL only permits SET in VOLATILE functions.
-- Run this in the Supabase SQL editor (or psql) once.

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
