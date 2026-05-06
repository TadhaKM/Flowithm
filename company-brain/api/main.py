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

from brain.query import (
    generate_skills_file,
    generate_workflow_from_text,
    query_brain,
)
from brain.store import (
    archive_workflow,
    clear_workflows,
    count_chunks,
    find_similar_workflow,
    get_workflow,
    list_workflows,
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
    print(f"[Company Brain] startup — {count} chunks indexed", flush=True)
    yield


app = FastAPI(title="Company Brain API", lifespan=lifespan)

# Permissive CORS for local dev. Tighten allow_origins before deploying.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


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
