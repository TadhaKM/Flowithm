"""Supabase vector store interface."""
import os
from typing import Any

from dotenv import load_dotenv
from supabase import Client, create_client

load_dotenv()

TABLE = "chunks"
MATCH_FN = "match_chunks"


def get_client() -> Client:
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_KEY"]
    return create_client(url, key)


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
    """Number of rows in the chunks table.

    `head=True` skips returning the rows; `count="exact"` requests an exact
    server-side count via the Prefer header.
    """
    client = get_client()
    result = client.table(TABLE).select("*", count="exact", head=True).execute()
    return result.count or 0


# ---------------------------------------------------------------------------
# skills table — generated workflows (back-end for /history)
# ---------------------------------------------------------------------------

SKILLS_TABLE = "skills"


def save_workflow(workflow: dict[str, Any]) -> None:
    """Persist a generated workflow to the skills table.

    The DB column for `trigger` is `process_trigger` because TRIGGER is a
    Postgres reserved word — see brain/schema.sql for the rationale.
    """
    client = get_client()
    client.table(SKILLS_TABLE).insert({
        "process_name": workflow["process"],
        "description": workflow.get("description") or "",
        "process_trigger": workflow.get("trigger") or "",
        "steps": workflow.get("steps") or [],
        "decision_rules": workflow.get("decision_rules") or [],
        "approvals": workflow.get("approvals") or [],
        "exceptions": workflow.get("exceptions") or [],
        "sources": workflow.get("sources") or [],
    }).execute()


def list_workflows(limit: int = 5) -> list[dict[str, Any]]:
    """Most recent workflows from the skills table, newest first.

    Result is normalised to the same shape produced by
    generate_workflow_from_text, plus a `generated_at` ISO timestamp so
    the UI can render relative times.
    """
    client = get_client()
    result = (
        client.table(SKILLS_TABLE)
        .select("*")
        .order("generated_at", desc=True)
        .limit(limit)
        .execute()
    )
    rows = result.data or []
    return [
        {
            "process": r["process_name"],
            "description": r.get("description") or "",
            "trigger": r.get("process_trigger") or "",
            "steps": r.get("steps") or [],
            "decision_rules": r.get("decision_rules") or [],
            "approvals": r.get("approvals") or [],
            "exceptions": r.get("exceptions") or [],
            "sources": r.get("sources") or [],
            "generated_at": r.get("generated_at"),
        }
        for r in rows
    ]
