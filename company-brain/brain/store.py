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
