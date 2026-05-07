"""FastAPI backend for Company Brain.

Endpoints:
  POST   /query                       — RAG Q&A
  POST   /skills                      — RAG-based skill file
  POST   /workflows/generate          — text-based workflow generation (paste material)
  GET    /workflows/similar           — fuzzy-name lookup for "update existing" detection
  GET    /workflows/{id}              — fetch a single workflow by id (UI deeplink + Slack)
  POST   /workflows/{id}/archive      — mark a workflow archived
  GET    /history                     — last N generated workflows
  DELETE /history                     — wipe all generated workflows
  GET    /demo/{slug}                 — serve a demo source-material file from /demo-data
  GET    /health                      — status + indexed chunk count
"""
# When uvicorn loads this as `main:app` (via run.sh, with /api as the working
# directory) instead of `api.main:app` from the project root, the `brain.*`
# imports below need the project root on sys.path.
if __package__ in (None, ""):
    import sys
    from pathlib import Path as _Path

    sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import time
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from brain.drift import (
    get_conflict_history,
    get_unresolved_conflicts,
    resolve_conflict,
)
from brain.query import (
    generate_skills_file,
    generate_workflow_from_text,
    query_brain,
)
from brain.store import (
    archive_workflow,
    clear_workflows,
    count_chunks,
    deactivate_connected_source,
    find_similar_workflow,
    get_connected_source,
    get_latest_ingest_run,
    get_workflow,
    insert_connected_source,
    list_connected_sources,
    list_workflows,
    update_connected_source,
)

load_dotenv()

CHUNK_COUNT_TTL_SECONDS = 30.0
_chunk_count_cache: dict[str, float | int | None] = {"value": None, "expires_at": 0.0}

DEMO_DIR = Path(__file__).resolve().parent.parent / "demo-data"


def _cached_chunk_count() -> int:
    """count_chunks() result, refreshed at most once per CHUNK_COUNT_TTL_SECONDS."""
    now = time.monotonic()
    if _chunk_count_cache["value"] is None or now >= _chunk_count_cache["expires_at"]:
        _chunk_count_cache["value"] = count_chunks()
        _chunk_count_cache["expires_at"] = now + CHUNK_COUNT_TTL_SECONDS
    return _chunk_count_cache["value"]  # type: ignore[return-value]


class QueryRequest(BaseModel):
    question: str
    top_k: int = 6


class SkillsRequest(BaseModel):
    process_name: str


class WorkflowRequest(BaseModel):
    name: str
    content: str
    # Optional provenance — used by the Slack bot to record channel/thread.
    source: str | None = None
    source_metadata: dict | None = None


class ConflictResolveRequest(BaseModel):
    action: str  # 'accept' | 'dismiss' | 'snooze'
    resolved_by: str


# ---------------------------------------------------------------------------
# Response model for /skills — must mirror SKILL_SCHEMA in brain/query.py.
# (Distinct from the /workflows/generate shape, which has description +
# sources array. /skills uses per-step `logic` and a single `sources_summary`.)
# ---------------------------------------------------------------------------
class SkillStep(BaseModel):
    step: int
    action: str
    logic: str | None
    owner: str
    notes: str | None


class SkillFileResponse(BaseModel):
    process: str
    trigger: str
    steps: list[SkillStep]
    decision_rules: list[str]
    approvals: list[str]
    exceptions: list[str]
    sources_summary: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    count = _cached_chunk_count()
    print(f"[Flowithm API] startup — {count} chunks indexed", flush=True)
    # Best-effort scheduler boot. If APScheduler / Supabase aren't available
    # we still serve traffic — the manual /ingest/trigger path will report
    # the error too if the user tries to use it.
    try:
        from brain.scheduler import scheduler
        scheduler.start()
    except Exception as exc:
        print(f"[Flowithm API] scheduler failed to start: {exc}", flush=True)
    try:
        yield
    finally:
        try:
            from brain.scheduler import scheduler
            scheduler.stop()
        except Exception:
            pass


app = FastAPI(title="Company Brain API", lifespan=lifespan)

# Permissive CORS for local dev. Tighten allow_origins before deploying.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount the public Agent API sub-app at /api/v1. Sub-app gets its own
# /api/v1/openapi.json + /api/v1/docs (Swagger UI).
from api.agent import agent_app  # noqa: E402

app.mount("/api/v1", agent_app)


@app.post("/query")
def query(req: QueryRequest) -> dict:
    return query_brain(req.question, top_k=req.top_k)


@app.post("/skills", response_model=SkillFileResponse)
def skills(req: SkillsRequest):
    return generate_skills_file(req.process_name)


@app.post("/workflows/generate")
def generate_workflow(req: WorkflowRequest) -> dict:
    return generate_workflow_from_text(
        req.name,
        req.content,
        source=req.source,
        source_metadata=req.source_metadata,
    )


# IMPORTANT: declare /workflows/similar BEFORE /workflows/{id}, otherwise
# FastAPI greedily matches "similar" as the {id} path segment.
@app.get("/workflows/similar")
def workflow_similar(
    name: str,
    threshold: float = 0.4,
    exclude_id: str = "",
) -> dict | None:
    """Fuzzy-match an existing non-archived workflow by name. Returns null if none."""
    return find_similar_workflow(
        name=name,
        threshold=threshold,
        exclude_id=exclude_id or None,
    )


@app.get("/workflows/{workflow_id}")
def get_workflow_endpoint(workflow_id: str) -> dict:
    """Fetch a single workflow row by id — UI deeplink and Slack-bot re-fetch."""
    wf = get_workflow(workflow_id)
    if not wf:
        raise HTTPException(404, f"workflow not found: {workflow_id}")
    return wf


@app.post("/workflows/{workflow_id}/archive")
def archive_workflow_endpoint(workflow_id: str) -> dict:
    """Mark a workflow archived. Used by the Slack bot's Update existing flow."""
    ok = archive_workflow(workflow_id)
    if not ok:
        raise HTTPException(404, f"workflow not found: {workflow_id}")
    return {"status": "ok", "archived": workflow_id}


@app.get("/history")
def history(limit: int = 5) -> list[dict]:
    return list_workflows(limit=limit)


@app.delete("/history")
def clear_history() -> dict:
    """Wipe all rows from the skills table — backs the UI's Clear all button."""
    cleared = clear_workflows()
    return {"status": "ok", "cleared": cleared}


@app.get("/demo/{slug}", response_class=PlainTextResponse)
def get_demo(slug: str) -> str:
    """Serve a demo source-material .txt from /demo-data."""
    if "/" in slug or "\\" in slug or ".." in slug or not slug.strip():
        raise HTTPException(400, "invalid slug")
    path = DEMO_DIR / f"{slug}.txt"
    if not path.is_file():
        raise HTTPException(404, f"demo not found: {slug}")
    return path.read_text(encoding="utf-8")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "chunks_indexed": _cached_chunk_count()}


# ---------------------------------------------------------------------------
# Drift / conflicts
# ---------------------------------------------------------------------------

@app.get("/conflicts")
def conflicts(include_snoozed: bool = False) -> list[dict]:
    """Unresolved drift conflicts. Pass ?include_snoozed=true to also list snoozed ones."""
    return get_unresolved_conflicts(include_snoozed=include_snoozed)


@app.post("/conflicts/{conflict_id}/resolve")
def resolve_conflict_endpoint(conflict_id: str, req: ConflictResolveRequest) -> dict:
    """Apply 'accept' | 'dismiss' | 'snooze' to a conflict."""
    if req.action not in {"accept", "dismiss", "snooze"}:
        raise HTTPException(400, f"unknown action: {req.action!r}")
    try:
        return resolve_conflict(conflict_id, req.action, req.resolved_by)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.get("/skills/{skill_id}/conflicts")
def skill_conflicts(skill_id: str) -> list[dict]:
    """Full conflict history for a single skill — any status."""
    return get_conflict_history(skill_id)


@app.post("/skills/{skill_id}/review")
def skill_mark_reviewed(skill_id: str) -> dict:
    """Mark a skill as freshly reviewed — clears needs_review + bumps reviewed_at."""
    from brain.staleness import mark_as_reviewed

    row = mark_as_reviewed(skill_id)
    if not row:
        raise HTTPException(404, f"skill not found: {skill_id}")
    return row


# ---------------------------------------------------------------------------
# Continuous ingestion: status, manual trigger, source CRUD (admin-only)
# ---------------------------------------------------------------------------
import os as _os  # noqa: E402

from api.auth import verify_admin_token  # noqa: E402
from fastapi import Depends as _Depends  # noqa: E402

_AdminDep = _Depends(verify_admin_token)


class SourceCreate(BaseModel):
    source_type: str
    display_name: str
    config: dict


class SourceUpdate(BaseModel):
    display_name: str | None = None
    config: dict | None = None
    is_active: bool | None = None


_REQUIRED_CONFIG_KEYS = {
    "slack":  {"bot_token", "channel_ids"},
    "notion": {"integration_token", "page_ids"},
}


def _validate_source_config(source_type: str, config: dict) -> None:
    required = _REQUIRED_CONFIG_KEYS.get(source_type)
    if required is None:
        return  # github / gmail / intercom — accept any shape for now
    missing = [k for k in required if k not in config or config[k] in (None, "", [])]
    if missing:
        raise HTTPException(400, f"missing required config keys for {source_type}: {missing}")


@app.get("/ingest/status")
def ingest_status() -> dict:
    """Last run summary + next scheduled run + cadence."""
    from brain.scheduler import scheduler

    last_db = get_latest_ingest_run()
    last = scheduler.last_run_summary or last_db
    return {
        "last_run": last,
        "next_run_at": scheduler.next_run_at_iso(),
        "schedule_hours": scheduler.schedule_hours(),
    }


@app.post("/ingest/trigger")
def ingest_trigger(_admin=_AdminDep) -> dict:
    """Kick off run_ingest_cycle in a daemon thread; admin-only."""
    from brain.scheduler import scheduler

    scheduler.trigger_now()
    return {"triggered": True, "message": "Ingest started in background"}


@app.get("/sources")
def sources_list() -> list[dict]:
    """Every connected source. Tokens redacted in the config field."""
    return list_connected_sources(redact=True)


@app.post("/sources")
def sources_create(req: SourceCreate, _admin=_AdminDep) -> dict:
    if req.source_type not in {"slack", "notion", "github", "gmail", "intercom"}:
        raise HTTPException(400, f"unsupported source_type: {req.source_type}")
    _validate_source_config(req.source_type, req.config)
    return insert_connected_source(req.source_type, req.display_name, req.config)


@app.patch("/sources/{source_id}")
def sources_update(source_id: str, req: SourceUpdate, _admin=_AdminDep) -> dict:
    existing = get_connected_source(source_id)
    if not existing:
        raise HTTPException(404, f"source not found: {source_id}")
    patch: dict = {}
    if req.display_name is not None:
        patch["display_name"] = req.display_name
    if req.config is not None:
        _validate_source_config(existing["source_type"], req.config)
        patch["config"] = req.config
    if req.is_active is not None:
        patch["is_active"] = req.is_active
    if not patch:
        raise HTTPException(400, "no fields to update")
    updated = update_connected_source(source_id, patch)
    if updated is None:
        raise HTTPException(404, f"source not found: {source_id}")
    return updated


@app.delete("/sources/{source_id}")
def sources_delete(source_id: str, _admin=_AdminDep) -> dict:
    """Soft-delete (is_active=false). Stops the scheduler picking it up."""
    ok = deactivate_connected_source(source_id)
    if not ok:
        raise HTTPException(404, f"source not found: {source_id}")
    return {"status": "ok", "deactivated": source_id}
