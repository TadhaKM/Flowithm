"""Supabase store interface — chunks (RAG) + skills (generated workflows).

Multi-tenancy: every helper that touches an org-scoped table accepts an
optional `org_id: str | None` parameter. When omitted, `_default_org_id()`
falls back to the `ORG_ID` env var (the seed default org for self-hosted
single-tenant deploys). Callers that handle multiple tenants — agent-API
endpoints driven by a Bearer token, the dashboard with a session cookie,
the scheduler walking organisations — pass org_id explicitly.

We intentionally do NOT raise when `org_id` is None and `ORG_ID` is unset:
that would make tests need explicit env stubs everywhere. Instead we fall
back to a literal "00000000-0000-0000-0000-000000000001" so a missing env
matches the seed UUID by coincidence; mis-configured prod deploys will see
queries return empty and notice immediately.
"""
import os
from datetime import datetime, timezone
from typing import Any

from dotenv import load_dotenv
from supabase import Client, create_client

load_dotenv()

TABLE = "chunks"
MATCH_FN = "match_chunks"
SKILLS_TABLE = "skills"
SIMILAR_FN = "find_similar_workflow"

DEFAULT_ORG_ID = "00000000-0000-0000-0000-000000000001"


def _default_org_id() -> str:
    """Resolve the active org for callers that didn't pass one explicitly."""
    return os.environ.get("ORG_ID", DEFAULT_ORG_ID)


_client: Client | None = None


def get_client() -> Client:
    """Return a module-level singleton Supabase client with a 30s timeout.

    Avoids creating a fresh httpx session on every call (H-10). Timeout
    prevents a stuck Supabase call from hanging the worker forever.
    """
    global _client
    if _client is None:
        import httpx

        url = os.environ["SUPABASE_URL"]
        key = os.environ["SUPABASE_SERVICE_KEY"]
        _client = create_client(url, key)
        _client.postgrest.session.timeout = httpx.Timeout(30.0)
    return _client


# ---------------------------------------------------------------------------
# organisations
# ---------------------------------------------------------------------------

def list_organisations() -> list[dict[str, Any]]:
    client = get_client()
    result = client.table("organisations").select("*").order("created_at", desc=True).execute()
    return result.data or []


def get_organisation(org_id: str) -> dict[str, Any] | None:
    client = get_client()
    result = client.table("organisations").select("*").eq("id", org_id).limit(1).execute()
    rows = result.data or []
    return rows[0] if rows else None


def get_organisation_by_slug(slug: str) -> dict[str, Any] | None:
    client = get_client()
    result = client.table("organisations").select("*").eq("slug", slug).limit(1).execute()
    rows = result.data or []
    return rows[0] if rows else None


def create_organisation(name: str, slug: str, plan: str = "free") -> dict[str, Any]:
    client = get_client()
    result = (
        client.table("organisations")
        .insert({"name": name, "slug": slug, "plan": plan})
        .execute()
    )
    return (result.data or [{}])[0]


# ---------------------------------------------------------------------------
# chunks (RAG corpus)
# ---------------------------------------------------------------------------

def upsert_chunks(chunks: list[dict[str, Any]], org_id: str | None = None) -> None:
    org = org_id or _default_org_id()
    client = get_client()
    rows = [{**c, "org_id": org} for c in chunks]
    client.table(TABLE).insert(rows).execute()


def similarity_search(
    query_embedding: list[float], k: int = 5, org_id: str | None = None
) -> list[dict[str, Any]]:
    client = get_client()
    result = client.rpc(
        MATCH_FN,
        {
            "query_embedding": query_embedding,
            "match_count": k,
            "target_org_id": org_id or _default_org_id(),
        },
    ).execute()
    return result.data or []


def count_chunks(org_id: str | None = None) -> int:
    """Number of rows in the chunks table for the current org."""
    client = get_client()
    result = (
        client.table(TABLE)
        .select("*", count="planned", head=True)
        .eq("org_id", org_id or _default_org_id())
        .execute()
    )
    return result.count or 0


# ---------------------------------------------------------------------------
# skills (generated workflows / skill files — back-end for /history, deeplinks,
# Slack bot's persisted output)
# ---------------------------------------------------------------------------


def _row_to_workflow(r: dict[str, Any]) -> dict[str, Any]:
    """Map a skills-table row into the workflow JSON shape the API serves.

    Notes:
    - DB column is `process_trigger` (Postgres reserves TRIGGER); JSON shape
      uses `trigger`.
    - `id` is included so the UI can construct deeplinks and the Slack bot
      can refer back to a row across button clicks.
    """
    return {
        "id": r.get("id"),
        "process": r["process_name"],
        "description": r.get("description") or "",
        "trigger": r.get("process_trigger") or "",
        "steps": r.get("steps") or [],
        "decision_rules": r.get("decision_rules") or [],
        "approvals": r.get("approvals") or [],
        "exceptions": r.get("exceptions") or [],
        "sources": r.get("sources") or [],
        "source": r.get("source") or "manual",
        "source_metadata": r.get("source_metadata") or {},
        "raw_text": r.get("raw_text") or "",
        "archived": bool(r.get("archived")),
        "archived_at": r.get("archived_at"),
        "reviewed_at": r.get("reviewed_at"),
        "generated_at": r.get("generated_at"),
        "needs_review": bool(r.get("needs_review")),
        "needs_review_reason": r.get("needs_review_reason"),
        "version": int(r.get("version") or 1) if r.get("version") is not None else 1,
    }


def save_workflow(
    workflow: dict[str, Any],
    source: str | None = None,
    source_metadata: dict[str, Any] | None = None,
    raw_text: str | None = None,
    org_id: str | None = None,
    summary_embedding: list[float] | None = None,
) -> str:
    """Persist a generated workflow; return the new row's UUID as a string.

    If summary_embedding is provided, it's written in the same INSERT so
    the skill is immediately visible to /api/v1/skills/match (H-7).
    """
    client = get_client()
    row: dict[str, Any] = {
        "process_name": workflow["process"],
        "description": workflow.get("description") or "",
        "process_trigger": workflow.get("trigger") or "",
        "steps": workflow.get("steps") or [],
        "decision_rules": workflow.get("decision_rules") or [],
        "approvals": workflow.get("approvals") or [],
        "exceptions": workflow.get("exceptions") or [],
        "sources": workflow.get("sources") or [],
        "source": source or "manual",
        "source_metadata": source_metadata or {},
        "raw_text": raw_text or "",
        "org_id": org_id or _default_org_id(),
    }
    if summary_embedding is not None:
        row["summary_embedding"] = summary_embedding
    result = client.table(SKILLS_TABLE).insert(row).execute()
    inserted = (result.data or [{}])[0]
    return str(inserted.get("id") or "")


def list_workflows(limit: int = 5, org_id: str | None = None) -> list[dict[str, Any]]:
    """Most recent non-archived workflows for the current org, newest first."""
    client = get_client()
    result = (
        client.table(SKILLS_TABLE)
        .select("*")
        .eq("archived", False)
        .eq("org_id", org_id or _default_org_id())
        .order("generated_at", desc=True)
        .limit(limit)
        .execute()
    )
    return [_row_to_workflow(r) for r in (result.data or [])]


def get_workflow(workflow_id: str, org_id: str | None = None) -> dict[str, Any] | None:
    """Fetch a single workflow by ID, scoped to the current org. None if not found."""
    client = get_client()
    result = (
        client.table(SKILLS_TABLE)
        .select("*")
        .eq("id", workflow_id)
        .eq("org_id", org_id or _default_org_id())
        .limit(1)
        .execute()
    )
    rows = result.data or []
    if not rows:
        return None
    return _row_to_workflow(rows[0])


def archive_workflow(workflow_id: str, org_id: str | None = None) -> bool:
    """Mark a workflow archived. Scoped to org so an attacker with a stolen
    UUID from one tenant can't archive a different tenant's row."""
    client = get_client()
    now = datetime.now(timezone.utc).isoformat()
    result = (
        client.table(SKILLS_TABLE)
        .update({"archived": True, "archived_at": now})
        .eq("id", workflow_id)
        .eq("org_id", org_id or _default_org_id())
        .execute()
    )
    return bool(result.data)


def find_similar_workflow(
    name: str,
    threshold: float = 0.4,
    exclude_id: str | None = None,
    org_id: str | None = None,
) -> dict[str, Any] | None:
    """Fuzzy-match an existing non-archived workflow within the current org."""
    client = get_client()
    try:
        result = client.rpc(
            SIMILAR_FN,
            {
                "q_name": name,
                "min_sim": threshold,
                "exclude_id": exclude_id or "",
                "target_org_id": org_id or _default_org_id(),
            },
        ).execute()
    except Exception as exc:
        print(f"[find_similar_workflow] rpc failed: {exc}", flush=True)
        return None

    rows = result.data or []
    if not rows:
        return None
    return _row_to_workflow(rows[0])


# ---------------------------------------------------------------------------
# Public agent API: skills index + name lookup + summary_embedding ops
# ---------------------------------------------------------------------------

def list_skills_index(
    source: str | None = None,
    updated_after: str | None = None,
    needs_review: bool | None = None,
    limit: int = 50,
    offset: int = 0,
    org_id: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Lightweight index for GET /api/v1/skills — no raw_text, no full steps."""
    client = get_client()
    base = (
        client.table(SKILLS_TABLE)
        .select(
            "id,process_name,process_trigger,steps,source,generated_at,needs_review,needs_review_reason",
            count="planned",
        )
        .eq("archived", False)
        .eq("org_id", org_id or _default_org_id())
    )
    if source:
        base = base.eq("source", source)
    if updated_after:
        base = base.gte("generated_at", updated_after)
    if needs_review is True:
        base = base.eq("needs_review", True)
    elif needs_review is False:
        base = base.eq("needs_review", False)
    result = base.order("generated_at", desc=True).range(offset, offset + max(limit, 1) - 1).execute()
    rows = []
    for r in result.data or []:
        steps = r.get("steps") or []
        rows.append({
            "id": r.get("id"),
            "process": r.get("process_name") or "",
            "trigger": r.get("process_trigger") or "",
            "step_count": len(steps),
            "last_updated": r.get("generated_at"),
            "source": r.get("source") or "manual",
            "needs_review": bool(r.get("needs_review")),
            "needs_review_reason": r.get("needs_review_reason"),
        })
    return rows, int(result.count or 0)


def get_skill_by_name_fuzzy(
    name: str,
    threshold: float = 0.4,
    closest_threshold: float = 0.2,
    org_id: str | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Exact-match first; if no exact, fuzzy-match via pg_trgm similarity.
    Both passes are scoped to the current org."""
    org = org_id or _default_org_id()
    client = get_client()
    exact = (
        client.table(SKILLS_TABLE)
        .select("*")
        .eq("archived", False)
        .eq("org_id", org)
        .ilike("process_name", name)
        .limit(1)
        .execute()
    )
    rows = exact.data or []
    if rows:
        return _row_to_workflow(rows[0]), None

    similar = find_similar_workflow(name=name, threshold=threshold, org_id=org)
    if similar:
        wf = get_workflow(similar["id"], org_id=org)
        if wf:
            return wf, None

    closest = find_similar_workflow(name=name, threshold=closest_threshold, org_id=org)
    return None, (closest["process"] if closest else None)


def update_skill_summary_embedding(
    skill_id: str, embedding: list[float], org_id: str | None = None
) -> None:
    """Persist summary_embedding for an existing skill (scoped to org)."""
    if not skill_id:
        return
    client = get_client()
    (
        client.table(SKILLS_TABLE)
        .update({"summary_embedding": embedding})
        .eq("id", skill_id)
        .eq("org_id", org_id or _default_org_id())
        .execute()
    )


def match_skills_by_embedding(
    query_embedding: list[float], k: int = 3, org_id: str | None = None
) -> list[dict[str, Any]]:
    """RPC into match_skills(), filtered to the current org."""
    client = get_client()
    result = client.rpc(
        "match_skills",
        {
            "query_embedding": query_embedding,
            "match_count": k,
            "target_org_id": org_id or _default_org_id(),
        },
    ).execute()
    return result.data or []


def get_skill_reviewed_at(skill_id: str, org_id: str | None = None) -> str | None:
    """Single-column read — the match_skills RPC returns generated_at but not
    reviewed_at, and the /skills/match freshness envelope needs both. Cheap
    indexed lookup by primary key."""
    if not skill_id:
        return None
    client = get_client()
    result = (
        client.table(SKILLS_TABLE)
        .select("reviewed_at")
        .eq("id", skill_id)
        .eq("org_id", org_id or _default_org_id())
        .limit(1)
        .execute()
    )
    rows = result.data or []
    if not rows:
        return None
    return rows[0].get("reviewed_at")


def mark_needs_review(skill_id: str, reason: str, org_id: str | None = None) -> None:
    """Idempotently flag a skill for human review. Sets needs_review=true,
    needs_review_reason, and stale_flagged_at. Safe to call on an already-
    flagged row (overwrites reason + timestamp). Background-task friendly:
    swallows exceptions so a logging side-effect never crashes a response."""
    if not skill_id:
        return
    try:
        from datetime import datetime as _dt
        from datetime import timezone as _tz

        client = get_client()
        (
            client.table(SKILLS_TABLE)
            .update({
                "needs_review": True,
                "needs_review_reason": reason,
                "stale_flagged_at": _dt.now(_tz.utc).isoformat(),
            })
            .eq("id", skill_id)
            .eq("org_id", org_id or _default_org_id())
            .execute()
        )
    except Exception:
        # Logged separately if needed; never escalate from a background task.
        pass


# ---------------------------------------------------------------------------
# api_keys, api_requests, executions
# ---------------------------------------------------------------------------

def insert_api_key(
    name: str, key_prefix: str, key_hash: str, org_id: str | None = None
) -> dict[str, Any]:
    client = get_client()
    result = (
        client.table("api_keys")
        .insert({
            "name": name,
            "key_prefix": key_prefix,
            "key_hash": key_hash,
            "org_id": org_id or _default_org_id(),
        })
        .execute()
    )
    return (result.data or [{}])[0]


def list_api_keys(org_id: str | None = None) -> list[dict[str, Any]]:
    client = get_client()
    result = (
        client.table("api_keys")
        .select("id,name,key_prefix,created_at,last_used_at,request_count,is_active,org_id")
        .eq("org_id", org_id or _default_org_id())
        .order("created_at", desc=True)
        .execute()
    )
    return result.data or []


def deactivate_api_key(key_id: str, org_id: str | None = None) -> bool:
    client = get_client()
    result = (
        client.table("api_keys")
        .update({"is_active": False})
        .eq("id", key_id)
        .eq("org_id", org_id or _default_org_id())
        .execute()
    )
    return bool(result.data)


def find_api_keys_by_prefix(prefix: str) -> list[dict[str, Any]]:
    """Candidates for bcrypt-verify. Intentionally cross-org — auth uses the
    matched key's org_id to scope every downstream query, so this is the
    one place we look across tenants. Hash collisions across orgs are a
    1-in-2^256 event we tolerate."""
    client = get_client()
    result = (
        client.table("api_keys")
        .select("id,name,key_hash,key_prefix,is_active,org_id")
        .eq("key_prefix", prefix)
        .eq("is_active", True)
        .execute()
    )
    return result.data or []


def increment_api_key_usage(key_id: str) -> None:
    """Atomic last_used_at + request_count bump via the SQL function. We
    have key_id from auth (already org-validated) so no extra filter."""
    client = get_client()
    client.rpc("increment_api_key_usage", {"key_id": key_id}).execute()


def insert_api_request(
    api_key_id: str | None,
    endpoint: str,
    response_time_ms: int,
    query_text: str | None = None,
    matched_skill_id: str | None = None,
    org_id: str | None = None,
) -> None:
    """Audit log. org_id is populated directly (C-2 defense-in-depth) so
    the usage dashboard doesn't need a JOIN to scope by tenant."""
    client = get_client()
    row: dict[str, Any] = {
        "api_key_id": api_key_id,
        "endpoint": endpoint,
        "query_text": query_text,
        "matched_skill_id": matched_skill_id,
        "response_time_ms": response_time_ms,
    }
    if org_id:
        row["org_id"] = org_id
    client.table("api_requests").insert(row).execute()


# ---------------------------------------------------------------------------
# Continuous ingestion: connected_sources + ingest_runs
# ---------------------------------------------------------------------------

from brain.crypto import decrypt_config, encrypt_config

REDACTED = "***"
_REDACT_KEYS = {"bot_token", "integration_token", "token", "api_key", "secret"}


def _redact_config(config: dict[str, Any] | None) -> dict[str, Any]:
    """Decrypt (if needed) then replace token-like values with ***."""
    decrypted = decrypt_config(config)
    if not isinstance(decrypted, dict):
        return {}
    out: dict[str, Any] = {}
    for k, v in decrypted.items():
        if k in _REDACT_KEYS and v:
            out[k] = REDACTED
        else:
            out[k] = v
    return out


def list_connected_sources(redact: bool = True, org_id: str | None = None) -> list[dict[str, Any]]:
    client = get_client()
    result = (
        client.table("connected_sources")
        .select("*")
        .eq("org_id", org_id or _default_org_id())
        .order("created_at", desc=True)
        .execute()
    )
    rows = result.data or []
    if redact:
        for r in rows:
            r["config"] = _redact_config(r.get("config"))
    return rows


def list_active_connected_sources(org_id: str | None = None) -> list[dict[str, Any]]:
    """Used by the scheduler. With org_id=None, returns ACTIVE sources across
    every org (so the scheduler can group by org). With a specific org_id,
    returns just that tenant's sources. Decrypted in both cases — the
    scheduler needs the raw tokens; never expose this to the dashboard."""
    client = get_client()
    base = client.table("connected_sources").select("*").eq("is_active", True)
    if org_id is not None:
        base = base.eq("org_id", org_id)
    result = base.execute()
    rows = result.data or []
    for r in rows:
        r["config"] = decrypt_config(r.get("config"))
    return rows


def get_connected_source(source_id: str, org_id: str | None = None) -> dict[str, Any] | None:
    client = get_client()
    result = (
        client.table("connected_sources")
        .select("*")
        .eq("id", source_id)
        .eq("org_id", org_id or _default_org_id())
        .limit(1)
        .execute()
    )
    rows = result.data or []
    if not rows:
        return None
    rows[0]["config"] = decrypt_config(rows[0].get("config"))
    return rows[0]


def insert_connected_source(
    source_type: str,
    display_name: str,
    config: dict[str, Any],
    org_id: str | None = None,
) -> dict[str, Any]:
    client = get_client()
    result = (
        client.table("connected_sources")
        .insert({
            "source_type": source_type,
            "display_name": display_name,
            "config": encrypt_config(config),
            "is_active": True,
            "org_id": org_id or _default_org_id(),
        })
        .execute()
    )
    row = (result.data or [{}])[0]
    row["config"] = _redact_config(row.get("config"))
    return row


def update_connected_source(
    source_id: str, patch: dict[str, Any], org_id: str | None = None
) -> dict[str, Any] | None:
    if "config" in patch:
        patch = {**patch, "config": encrypt_config(patch["config"])}
    client = get_client()
    result = (
        client.table("connected_sources")
        .update(patch)
        .eq("id", source_id)
        .eq("org_id", org_id or _default_org_id())
        .execute()
    )
    rows = result.data or []
    if not rows:
        return None
    rows[0]["config"] = _redact_config(rows[0].get("config"))
    return rows[0]


def deactivate_connected_source(source_id: str, org_id: str | None = None) -> bool:
    client = get_client()
    result = (
        client.table("connected_sources")
        .update({"is_active": False})
        .eq("id", source_id)
        .eq("org_id", org_id or _default_org_id())
        .execute()
    )
    return bool(result.data)


def delete_connected_source(source_id: str, org_id: str | None = None) -> bool:
    """Hard-delete the row. No other table references connected_sources.id
    (see schema.sql), so deletion is safe — there's nothing to cascade."""
    client = get_client()
    result = (
        client.table("connected_sources")
        .delete()
        .eq("id", source_id)
        .eq("org_id", org_id or _default_org_id())
        .execute()
    )
    return bool(result.data)


def update_source_last_synced(source_id: str, when_iso: str, org_id: str | None = None) -> None:
    client = get_client()
    q = client.table("connected_sources").update({"last_synced_at": when_iso}).eq("id", source_id)
    if org_id:
        q = q.eq("org_id", org_id)
    q.execute()


def update_source_validation(
    source_id: str,
    status: str,
    error: str | None,
    when_iso: str,
    org_id: str | None = None,
) -> None:
    """Persist the result of a "test connection" check. Touches only the
    validation columns — never the encrypted config."""
    client = get_client()
    q = (
        client.table("connected_sources")
        .update({
            "last_validated_at": when_iso,
            "last_validation_status": status,
            "last_validation_error": error,
        })
        .eq("id", source_id)
    )
    if org_id:
        q = q.eq("org_id", org_id)
    q.execute()


def insert_ingest_run(summary: dict[str, Any], org_id: str | None = None) -> str:
    """Persist one scheduled or manual run. errors is stored as jsonb."""
    client = get_client()
    payload = {
        "started_at":       summary.get("started_at"),
        "duration_seconds": int(summary.get("duration_seconds", 0)),
        "sources_checked":  int(summary.get("sources_checked", 0)),
        "new_chunks":       int(summary.get("new_chunks", 0)),
        "skipped_chunks":   int(summary.get("skipped_chunks", 0)),
        "new_conflicts":    int(summary.get("new_conflicts", 0)),
        "stale_flagged":    int(summary.get("stale_flagged", 0)),
        "stale_cleared":    int(summary.get("stale_cleared", 0)),
        "errors":           summary.get("errors") or [],
        "errored":          bool(summary.get("errored")),
        "org_id":           org_id or _default_org_id(),
    }
    result = client.table("ingest_runs").insert(payload).execute()
    return str((result.data or [{}])[0].get("id") or "")


def get_latest_ingest_run(org_id: str | None = None) -> dict[str, Any] | None:
    client = get_client()
    result = (
        client.table("ingest_runs")
        .select("*")
        .eq("org_id", org_id or _default_org_id())
        .order("started_at", desc=True)
        .limit(1)
        .execute()
    )
    rows = result.data or []
    return rows[0] if rows else None


def insert_execution(
    skill_id: str,
    step_number: int | None,
    outcome: str,
    exception_note: str | None,
    duration_seconds: int | None,
    org_id: str | None = None,
) -> str:
    client = get_client()
    result = client.table("executions").insert({
        "skill_id": skill_id,
        "step_number": step_number,
        "outcome": outcome,
        "exception_note": exception_note,
        "duration_seconds": duration_seconds,
        "org_id": org_id or _default_org_id(),
    }).execute()
    return str((result.data or [{}])[0].get("id") or "")


def clear_workflows(org_id: str | None = None) -> int:
    """Delete every workflow row in the current org. Returns the count cleared."""
    client = get_client()
    result = (
        client.table(SKILLS_TABLE)
        .delete()
        .eq("org_id", org_id or _default_org_id())
        .gte("generated_at", "1970-01-01")
        .execute()
    )
    return len(result.data or [])
