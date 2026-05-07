"""Supabase store interface — chunks (RAG) + skills (generated workflows)."""
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


def get_client() -> Client:
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_KEY"]
    return create_client(url, key)


# ---------------------------------------------------------------------------
# chunks (RAG corpus)
# ---------------------------------------------------------------------------

def upsert_chunks(chunks: list[dict[str, Any]]) -> None:
    client = get_client()
    client.table(TABLE).insert(chunks).execute()


def similarity_search(query_embedding: list[float], k: int = 5) -> list[dict[str, Any]]:
    client = get_client()
    result = client.rpc(
        MATCH_FN,
        {"query_embedding": query_embedding, "match_count": k},
    ).execute()
    return result.data or []


def count_chunks() -> int:
    """Number of rows in the chunks table."""
    client = get_client()
    result = client.table(TABLE).select("*", count="exact", head=True).execute()
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
    }


def save_workflow(
    workflow: dict[str, Any],
    source: str | None = None,
    source_metadata: dict[str, Any] | None = None,
    raw_text: str | None = None,
) -> str:
    """Persist a generated workflow; return the new row's UUID as a string.

    Optional `source` ("slack" / "manual" / etc.) and `source_metadata`
    (free-form JSON describing channel, thread, triggering user, etc.)
    travel with the workflow for provenance.

    `raw_text` is the original input the workflow was distilled from; the
    /brain/[id] detail page reads it for the "Re-extract" feature.
    """
    client = get_client()
    row = {
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
    }
    result = client.table(SKILLS_TABLE).insert(row).execute()
    inserted = (result.data or [{}])[0]
    return str(inserted.get("id") or "")


def list_workflows(limit: int = 5) -> list[dict[str, Any]]:
    """Most recent non-archived workflows, newest first."""
    client = get_client()
    result = (
        client.table(SKILLS_TABLE)
        .select("*")
        .eq("archived", False)
        .order("generated_at", desc=True)
        .limit(limit)
        .execute()
    )
    return [_row_to_workflow(r) for r in (result.data or [])]


def get_workflow(workflow_id: str) -> dict[str, Any] | None:
    """Fetch a single workflow by ID. Returns None if not found."""
    client = get_client()
    result = (
        client.table(SKILLS_TABLE)
        .select("*")
        .eq("id", workflow_id)
        .limit(1)
        .execute()
    )
    rows = result.data or []
    if not rows:
        return None
    return _row_to_workflow(rows[0])


def archive_workflow(workflow_id: str) -> bool:
    """Mark a workflow archived. Returns True if a row was updated.

    archived_at gets the current UTC timestamp. We set it Python-side rather
    than via a DB trigger because the trigger would also fire on bulk
    archival operations we may run later — keeping the timestamp logic next
    to the call site is more obvious.
    """
    client = get_client()
    now = datetime.now(timezone.utc).isoformat()
    result = (
        client.table(SKILLS_TABLE)
        .update({"archived": True, "archived_at": now})
        .eq("id", workflow_id)
        .execute()
    )
    return bool(result.data)


def find_similar_workflow(
    name: str,
    threshold: float = 0.4,
    exclude_id: str | None = None,
) -> dict[str, Any] | None:
    """Find a non-archived workflow whose process_name is fuzzily similar.

    Backed by the find_similar_workflow Postgres function (pg_trgm). Returns
    None if nothing crosses `threshold`, or if the RPC fails — callers
    treat absence as "no match" and continue.
    """
    client = get_client()
    try:
        result = client.rpc(
            SIMILAR_FN,
            {
                "q_name": name,
                "min_sim": threshold,
                "exclude_id": exclude_id or "",
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
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """Lightweight index for GET /api/v1/skills — no raw_text, no full steps.

    Returns (rows, total_count). `total_count` reflects the post-filter set,
    not the page; the agent SDK can paginate by incrementing offset.
    """
    client = get_client()
    base = (
        client.table(SKILLS_TABLE)
        .select(
            "id,process_name,process_trigger,steps,source,generated_at",
            count="exact",
        )
        .eq("archived", False)
    )
    if source:
        base = base.eq("source", source)
    if updated_after:
        base = base.gte("generated_at", updated_after)
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
        })
    return rows, int(result.count or 0)


def get_skill_by_name_fuzzy(
    name: str,
    threshold: float = 0.4,
    closest_threshold: float = 0.2,
) -> tuple[dict[str, Any] | None, str | None]:
    """Exact-match first; if no exact, fuzzy-match via pg_trgm similarity.

    Returns (skill, closest_match_name).
      - exact hit:        (skill, None)
      - fuzzy hit:        (skill, None)
      - no hit but close: (None, "<closest process_name>")
      - no hit at all:    (None, None)
    """
    client = get_client()
    exact = (
        client.table(SKILLS_TABLE)
        .select("*")
        .eq("archived", False)
        .ilike("process_name", name)
        .limit(1)
        .execute()
    )
    rows = exact.data or []
    if rows:
        return _row_to_workflow(rows[0]), None

    # Fall back to find_similar_workflow (pg_trgm). Returns the best match
    # at or above `threshold`, or None.
    similar = find_similar_workflow(name=name, threshold=threshold)
    if similar:
        # Re-fetch the full row so we get raw_text + every column.
        wf = get_workflow(similar["id"])
        if wf:
            return wf, None

    closest = find_similar_workflow(name=name, threshold=closest_threshold)
    return None, (closest["process"] if closest else None)


def update_skill_summary_embedding(skill_id: str, embedding: list[float]) -> None:
    """Persist the summary_embedding for an existing skill row (idempotent)."""
    if not skill_id:
        return
    client = get_client()
    client.table(SKILLS_TABLE).update({"summary_embedding": embedding}).eq("id", skill_id).execute()


def match_skills_by_embedding(
    query_embedding: list[float], k: int = 3
) -> list[dict[str, Any]]:
    """RPC into the match_skills() Postgres function. Returns rows with
    similarity float in (0, 1] — higher is better (1 - cosine distance)."""
    client = get_client()
    result = client.rpc(
        "match_skills",
        {"query_embedding": query_embedding, "match_count": k},
    ).execute()
    return result.data or []


# ---------------------------------------------------------------------------
# api_keys, api_requests, executions
# ---------------------------------------------------------------------------

def insert_api_key(name: str, key_prefix: str, key_hash: str) -> dict[str, Any]:
    client = get_client()
    result = (
        client.table("api_keys")
        .insert({"name": name, "key_prefix": key_prefix, "key_hash": key_hash})
        .execute()
    )
    return (result.data or [{}])[0]


def list_api_keys() -> list[dict[str, Any]]:
    client = get_client()
    result = (
        client.table("api_keys")
        .select("id,name,key_prefix,created_at,last_used_at,request_count,is_active")
        .order("created_at", desc=True)
        .execute()
    )
    return result.data or []


def deactivate_api_key(key_id: str) -> bool:
    client = get_client()
    result = (
        client.table("api_keys")
        .update({"is_active": False})
        .eq("id", key_id)
        .execute()
    )
    return bool(result.data)


def find_api_keys_by_prefix(prefix: str) -> list[dict[str, Any]]:
    """Candidates for bcrypt-verify — narrows to ~1 row in the common case."""
    client = get_client()
    result = (
        client.table("api_keys")
        .select("id,name,key_hash,key_prefix,is_active")
        .eq("key_prefix", prefix)
        .execute()
    )
    return result.data or []


def increment_api_key_usage(key_id: str) -> None:
    """Atomic last_used_at + request_count bump via the SQL function."""
    client = get_client()
    client.rpc("increment_api_key_usage", {"key_id": key_id}).execute()


def insert_api_request(
    api_key_id: str | None,
    endpoint: str,
    response_time_ms: int,
    query_text: str | None = None,
    matched_skill_id: str | None = None,
) -> None:
    client = get_client()
    client.table("api_requests").insert({
        "api_key_id": api_key_id,
        "endpoint": endpoint,
        "query_text": query_text,
        "matched_skill_id": matched_skill_id,
        "response_time_ms": response_time_ms,
    }).execute()


# ---------------------------------------------------------------------------
# Continuous ingestion: connected_sources + ingest_runs
# ---------------------------------------------------------------------------

REDACTED = "***"
_REDACT_KEYS = {"bot_token", "integration_token", "token", "api_key", "secret"}


def _redact_config(config: dict[str, Any] | None) -> dict[str, Any]:
    """Replace any token-like value in a connected_source.config blob with ***."""
    if not isinstance(config, dict):
        return {}
    out: dict[str, Any] = {}
    for k, v in config.items():
        if k in _REDACT_KEYS and v:
            out[k] = REDACTED
        else:
            out[k] = v
    return out


def list_connected_sources(redact: bool = True) -> list[dict[str, Any]]:
    client = get_client()
    result = (
        client.table("connected_sources")
        .select("*")
        .order("created_at", desc=True)
        .execute()
    )
    rows = result.data or []
    if redact:
        for r in rows:
            r["config"] = _redact_config(r.get("config"))
    return rows


def list_active_connected_sources() -> list[dict[str, Any]]:
    """Used by the scheduler — returns full (un-redacted) config so the
    ingestor can read its tokens. Never expose this to the dashboard."""
    client = get_client()
    result = (
        client.table("connected_sources")
        .select("*")
        .eq("is_active", True)
        .execute()
    )
    return result.data or []


def get_connected_source(source_id: str) -> dict[str, Any] | None:
    client = get_client()
    result = (
        client.table("connected_sources")
        .select("*")
        .eq("id", source_id)
        .limit(1)
        .execute()
    )
    rows = result.data or []
    return rows[0] if rows else None


def insert_connected_source(
    source_type: str,
    display_name: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    client = get_client()
    result = (
        client.table("connected_sources")
        .insert({
            "source_type": source_type,
            "display_name": display_name,
            "config": config,
            "is_active": True,
        })
        .execute()
    )
    row = (result.data or [{}])[0]
    row["config"] = _redact_config(row.get("config"))
    return row


def update_connected_source(source_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
    client = get_client()
    result = (
        client.table("connected_sources")
        .update(patch)
        .eq("id", source_id)
        .execute()
    )
    rows = result.data or []
    if not rows:
        return None
    rows[0]["config"] = _redact_config(rows[0].get("config"))
    return rows[0]


def deactivate_connected_source(source_id: str) -> bool:
    client = get_client()
    result = (
        client.table("connected_sources")
        .update({"is_active": False})
        .eq("id", source_id)
        .execute()
    )
    return bool(result.data)


def update_source_last_synced(source_id: str, when_iso: str) -> None:
    client = get_client()
    client.table("connected_sources").update({"last_synced_at": when_iso}).eq("id", source_id).execute()


def insert_ingest_run(summary: dict[str, Any]) -> str:
    """Persist one scheduled or manual run. errors is stored as jsonb."""
    client = get_client()
    payload = {
        "started_at":       summary.get("started_at"),
        "duration_seconds": int(summary.get("duration_seconds", 0)),
        "sources_checked":  int(summary.get("sources_checked", 0)),
        "new_chunks":       int(summary.get("new_chunks", 0)),
        "skipped_chunks":   int(summary.get("skipped_chunks", 0)),
        "new_conflicts":    int(summary.get("new_conflicts", 0)),
        "errors":           summary.get("errors") or [],
    }
    result = client.table("ingest_runs").insert(payload).execute()
    return str((result.data or [{}])[0].get("id") or "")


def get_latest_ingest_run() -> dict[str, Any] | None:
    client = get_client()
    result = (
        client.table("ingest_runs")
        .select("*")
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
) -> str:
    client = get_client()
    result = client.table("executions").insert({
        "skill_id": skill_id,
        "step_number": step_number,
        "outcome": outcome,
        "exception_note": exception_note,
        "duration_seconds": duration_seconds,
    }).execute()
    return str((result.data or [{}])[0].get("id") or "")


def clear_workflows() -> int:
    """Delete every row in the skills table. Returns the count cleared.

    supabase-py's `.delete()` refuses to run without a WHERE clause as a
    safety; `generated_at >= epoch` matches every row that has ever existed.
    """
    client = get_client()
    result = (
        client.table(SKILLS_TABLE)
        .delete()
        .gte("generated_at", "1970-01-01")
        .execute()
    )
    return len(result.data or [])
