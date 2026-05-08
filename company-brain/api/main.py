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
  GET    /health                      — fast liveness probe (used by Railway healthcheck)
  GET    /health/detailed              — full dependency probe (Supabase/Anthropic/Voyage/scheduler)
"""
# When uvicorn loads this as `main:app` (via run.sh, with /api as the working
# directory) instead of `api.main:app` from the project root, the `brain.*`
# imports below need the project root on sys.path.
if __package__ in (None, ""):
    import sys
    from pathlib import Path as _Path

    sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel

from brain.logger import get_logger
from brain.store import DEFAULT_ORG_ID

log = get_logger("flowithm.api")
DOCS_URL = "https://flowithm.io/docs"

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
    create_organisation,
    deactivate_connected_source,
    find_similar_workflow,
    get_connected_source,
    get_latest_ingest_run,
    get_organisation_by_slug,
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


def get_org_id(request: Request) -> str:
    """Resolve the request's tenant. Header → env → seeded default UUID.

    The dashboard's Next.js proxies inject `X-Org-ID` from a cookie; the
    Slack bot injects from `ORG_ID` env. Single-tenant deploys with
    neither set fall through to the seeded default org so the system
    still functions out of the box."""
    header_val = request.headers.get("x-org-id") or request.headers.get("X-Org-ID")
    if header_val and header_val.strip():
        return header_val.strip()
    return os.environ.get("ORG_ID", DEFAULT_ORG_ID)


_OrgDep = Depends(get_org_id)

# Admin gate for every internal org-scoped endpoint. The dashboard's
# Next.js proxies inject ADMIN_TOKEN server-side; the Slack bot does
# the same. The public internet → no admin token → 401.
from api.auth import verify_admin_token  # noqa: E402

_AdminDep = Depends(verify_admin_token)


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
    # Bump the sync-route threadpool from the default ~40 to 200 so
    # concurrent agent requests don't queue behind slow Anthropic calls.
    try:
        from anyio import to_thread
        to_thread.current_default_thread_limiter().total_tokens = 200
    except Exception:
        pass
    count = _cached_chunk_count()
    log.info("api startup", extra={"chunks_indexed": count})
    # Best-effort scheduler boot. If APScheduler / Supabase aren't available
    # we still serve traffic — the manual /ingest/trigger path will report
    # the error too if the user tries to use it.
    try:
        from brain.scheduler import scheduler
        scheduler.start()
    except Exception as exc:
        log.error("scheduler failed to start", exc_info=True, extra={"error": str(exc)})
    try:
        yield
    finally:
        try:
            from brain.scheduler import scheduler
            scheduler.stop()
        except Exception:
            pass
        try:
            from brain.drift import _DRIFT_POOL
            _DRIFT_POOL.shutdown(wait=False)
        except Exception:
            pass


app = FastAPI(title="Flowithm API", lifespan=lifespan)

# CORS. Always allow localhost:3000 for local dev; production deploys add
# their dashboard origin via FRONTEND_URL. allow_credentials=True needs an
# explicit origin list (the wildcard "*" is invalid with credentials), so
# we filter empties.
_origins = [
    "http://localhost:3000",
    os.environ.get("FRONTEND_URL", "").strip(),
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o for o in _origins if o],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["authorization", "content-type", "x-org-id", "x-admin-sig"],
)

# Mount the public Agent API sub-app at /api/v1. Sub-app gets its own
# /api/v1/openapi.json + /api/v1/docs (Swagger UI).
from api.agent import agent_app  # noqa: E402

app.mount("/api/v1", agent_app)


# ---------------------------------------------------------------------------
# Global exception handlers — every error returns the standard
# {error, code, docs} envelope. Tracebacks land in the structured logger
# (never in the response body).
# ---------------------------------------------------------------------------

@app.exception_handler(RequestValidationError)
async def _validation_error_handler(request: Request, exc: RequestValidationError):
    log.warning("request validation failed", extra={
        "endpoint": request.url.path,
        "errors": exc.errors(),
    })
    return JSONResponse(
        status_code=422,
        content={
            "error": "Invalid request",
            "code": "INVALID_REQUEST",
            "docs": DOCS_URL,
            "details": exc.errors(),
        },
    )


@app.exception_handler(HTTPException)
async def _http_exception_handler(request: Request, exc: HTTPException):
    """Force every HTTPException into the {error, code, docs} envelope.
    HTTPException raises with detail as either a string (legacy callers
    using `raise HTTPException(404, "msg")`) or a dict (the structured
    {error, code, docs} envelope). Unwrap both consistently."""
    detail = exc.detail
    if isinstance(detail, dict) and "error" in detail and "code" in detail:
        body = detail
    else:
        body = {
            "error": str(detail) if detail else "Error",
            "code": _code_for_status(exc.status_code),
            "docs": DOCS_URL,
        }
    return JSONResponse(status_code=exc.status_code, content=body, headers=getattr(exc, "headers", None))


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception):
    """Last resort — anything that escapes the routing layer ends up here.
    Map a few well-known Supabase / Postgres errors to clearer responses
    so the client gets a recognisable code rather than a generic 500."""
    mapped = _map_supabase_error(exc)
    if mapped is not None:
        status, code, message = mapped
        log.warning("supabase error mapped", extra={
            "endpoint": request.url.path, "code": code, "status": status,
            "supabase_error": str(exc),
        })
        return JSONResponse(
            status_code=status,
            content={"error": message, "code": code, "docs": DOCS_URL},
        )

    log.error("unhandled exception", exc_info=True, extra={
        "endpoint": request.url.path, "method": request.method,
    })
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal server error",
            "code": "INTERNAL_ERROR",
            "docs": DOCS_URL,
        },
    )


def _code_for_status(status: int) -> str:
    return {
        400: "INVALID_REQUEST",
        401: "MISSING_API_KEY",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        422: "INVALID_REQUEST",
        429: "RATE_LIMIT_EXCEEDED",
        500: "INTERNAL_ERROR",
    }.get(status, "INTERNAL_ERROR")


def _map_supabase_error(exc: Exception) -> tuple[int, str, str] | None:
    """Recognise common postgrest.APIError shapes and map to (status, code, message).
    Returns None for unknown errors (caller falls through to a generic 500)."""
    code = getattr(exc, "code", None) or ""
    message = getattr(exc, "message", None) or str(exc)
    # SQLSTATE codes — values supabase-py surfaces directly on APIError.code
    if code == "23505":
        return 409, "ALREADY_EXISTS", "A row with this value already exists."
    if code == "42P01":
        return 500, "SCHEMA_OUT_OF_DATE", (
            "Schema may need migration — re-run brain/schema.sql."
        )
    if code == "42501":
        return 403, "FORBIDDEN", "Database access denied (RLS policy)."
    if code == "23503":
        return 400, "INVALID_REQUEST", "Foreign key violation: referenced row missing."
    # Network-y errors raised by httpx during Supabase calls
    name = type(exc).__name__
    if name in {"ConnectError", "ReadTimeout", "ConnectTimeout", "TimeoutException"}:
        return 503, "DATABASE_UNAVAILABLE", "Database temporarily unavailable; retry shortly."
    return None


# Every endpoint below is org-scoped AND admin-gated. The dashboard's
# Next.js proxy routes inject ADMIN_TOKEN server-side; the Slack bot
# does the same. Public internet → no admin token → 401. The X-Org-ID
# header is now only honoured for principals that already authenticated
# with the admin token, closing the cross-tenant access path that
# previously let any caller hit any org by header alone.

@app.post("/query")
def query(req: QueryRequest, org_id: str = _OrgDep, _admin=_AdminDep) -> dict:
    return query_brain(req.question, top_k=req.top_k, org_id=org_id)


@app.post("/skills", response_model=SkillFileResponse)
def skills(req: SkillsRequest, org_id: str = _OrgDep, _admin=_AdminDep):
    return generate_skills_file(req.process_name, org_id=org_id)


@app.post("/workflows/generate")
def generate_workflow(req: WorkflowRequest, org_id: str = _OrgDep, _admin=_AdminDep) -> dict:
    return generate_workflow_from_text(
        req.name,
        req.content,
        source=req.source,
        source_metadata=req.source_metadata,
        org_id=org_id,
    )


# IMPORTANT: declare /workflows/similar BEFORE /workflows/{id}, otherwise
# FastAPI greedily matches "similar" as the {id} path segment.
@app.get("/workflows/similar")
def workflow_similar(
    name: str,
    threshold: float = 0.4,
    exclude_id: str = "",
    org_id: str = _OrgDep,
    _admin=_AdminDep,
) -> dict | None:
    """Fuzzy-match an existing non-archived workflow by name. Returns null if none."""
    return find_similar_workflow(
        name=name,
        threshold=threshold,
        exclude_id=exclude_id or None,
        org_id=org_id,
    )


@app.get("/workflows/{workflow_id}")
def get_workflow_endpoint(workflow_id: str, org_id: str = _OrgDep, _admin=_AdminDep) -> dict:
    """Fetch a single workflow row by id — UI deeplink and Slack-bot re-fetch."""
    wf = get_workflow(workflow_id, org_id=org_id)
    if not wf:
        raise HTTPException(404, f"workflow not found: {workflow_id}")
    return wf


@app.post("/workflows/{workflow_id}/archive")
def archive_workflow_endpoint(workflow_id: str, org_id: str = _OrgDep, _admin=_AdminDep) -> dict:
    """Mark a workflow archived. Used by the Slack bot's Update existing flow."""
    ok = archive_workflow(workflow_id, org_id=org_id)
    if not ok:
        raise HTTPException(404, f"workflow not found: {workflow_id}")
    return {"status": "ok", "archived": workflow_id}


@app.get("/history")
def history(
    limit: int = Query(5, ge=1, le=200),
    org_id: str = _OrgDep,
    _admin=_AdminDep,
) -> list[dict]:
    return list_workflows(limit=limit, org_id=org_id)


@app.delete("/history")
def clear_history(org_id: str = _OrgDep, _admin=_AdminDep) -> dict:
    """Wipe all rows from the skills table — backs the UI's Clear all button."""
    cleared = clear_workflows(org_id=org_id)
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
    """Lightweight liveness probe for Railway/Vercel/etc. Must return in
    well under the platform's healthcheck window — Railway's is 30s — so
    no external calls here. The detailed dependency probe lives at
    /health/detailed."""
    return {
        "status": "ok",
        "version": os.environ.get("APP_VERSION", "dev"),
    }


@app.get("/health/detailed")
def health_detailed(request: Request) -> dict:
    """Real health probe — exercises every external dependency just enough
    to know whether it's reachable, then returns a per-component status
    map. Existing `chunks_indexed` field preserved for backward compat
    with anything probing the old shape."""
    checks: dict[str, str | int | dict | None] = {}

    # Supabase: a tiny count-only query against an indexed column.
    try:
        from brain.store import get_client
        result = (
            get_client()
            .table("skills")
            .select("id", count="planned")
            .limit(1)
            .execute()
        )
        checks["supabase"] = "ok"
        checks["skills_count"] = int(result.count or 0)
    except Exception as exc:
        checks["supabase"] = f"error: {exc}"

    # Anthropic: format-check the key only — we don't burn a real call on
    # every health probe.
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        checks["anthropic"] = "error: ANTHROPIC_API_KEY missing"
    elif not key.startswith("sk-ant-"):
        checks["anthropic"] = "warning: key format unexpected"
    else:
        checks["anthropic"] = "ok"

    # Voyage: format-check the key.
    voyage_key = os.environ.get("VOYAGE_API_KEY", "")
    checks["voyage"] = "ok" if voyage_key else "error: VOYAGE_API_KEY missing"

    # Scheduler: APScheduler running flag + last cycle summary.
    try:
        from brain.scheduler import scheduler
        checks["scheduler"] = "ok" if scheduler.scheduler.running else "stopped"
        checks["last_ingest"] = scheduler.last_run_summary
    except Exception as exc:
        checks["scheduler"] = f"error: {exc}"
        checks["last_ingest"] = None

    # Anthropic circuit breaker state — visible because it's the most
    # likely source of degraded responses in normal operation.
    try:
        from brain.anthropic_client import _circuit_status
        is_open, remaining = _circuit_status()
        checks["anthropic_circuit"] = "open" if is_open else "closed"
        if is_open:
            checks["anthropic_circuit_reopen_seconds"] = int(remaining)
    except Exception:
        checks["anthropic_circuit"] = "unknown"

    # Overall status: 'ok' iff every str-valued check is exactly 'ok' (or 'closed' for the circuit).
    string_values = [v for v in checks.values() if isinstance(v, str)]
    overall = "ok" if all(v in ("ok", "closed") for v in string_values) else "degraded"

    # L-2: redact error details for unauthenticated callers so internal
    # hostnames and PostgREST errors aren't exposed via the public probe.
    is_admin = False
    try:
        admin_token = os.environ.get("ADMIN_TOKEN", "").strip()
        auth_header = request.headers.get("authorization") or ""
        if admin_token and auth_header.startswith("Bearer "):
            import hmac as _hmac
            is_admin = _hmac.compare_digest(auth_header[7:], admin_token)
    except Exception:
        pass
    if not is_admin:
        for k, v in checks.items():
            if isinstance(v, str) and v.startswith("error"):
                checks[k] = "error"

    return {
        "status": overall,
        "checks": checks,
        "version": os.environ.get("APP_VERSION", "dev"),
        "chunks_indexed": _cached_chunk_count(),
    }


# ---------------------------------------------------------------------------
# Setup — creates an organisation. Called once by the dashboard's /setup page.
# Open endpoint (no admin token) so a fresh deployment's first user can
# bootstrap themselves; subsequent admin operations gate on ADMIN_TOKEN.
# ---------------------------------------------------------------------------

class SetupRequest(BaseModel):
    company_name: str
    user_name: str | None = None


@app.post("/setup")
def setup(req: SetupRequest, request: Request) -> dict:
    name = (req.company_name or "").strip()
    if not name:
        raise HTTPException(400, "company_name is required")

    # Bootstrap gate. The very first call (no orgs in the DB yet) is
    # allowed unauthenticated so a fresh deploy can self-bootstrap.
    # Every subsequent call requires Authorization: Bearer $BOOTSTRAP_TOKEN
    # so the public internet can't DoS the DB by minting orgs at will,
    # and can't farm session cookies for the dashboard's admin proxies.
    from brain.store import list_organisations
    orgs = list_organisations()
    if orgs:
        bootstrap = os.environ.get("BOOTSTRAP_TOKEN", "").strip()
        if not bootstrap:
            raise HTTPException(
                403,
                "Setup is closed: BOOTSTRAP_TOKEN is not configured. "
                "Set it in the FastAPI environment to enable additional org creation.",
            )
        try:
            from api.auth import _extract_bearer, _constant_time_eq
            token = _extract_bearer(request.headers.get("authorization"))
        except HTTPException:
            raise HTTPException(401, "BOOTSTRAP_TOKEN required to create another organisation.")
        if not _constant_time_eq(token, bootstrap):
            raise HTTPException(401, "Invalid bootstrap token.")
    # Slugify: lowercase, alphanumeric + hyphens, deduplicate against
    # existing. On collision, suffix with a random hex token rather than
    # a monotonic counter so existence checks don't leak ordinal info
    # ("acme-2" → an attacker learns "acme" exists).
    import secrets as _secrets
    base_slug = "".join(ch if ch.isalnum() else "-" for ch in name.lower()).strip("-") or "org"
    slug = base_slug
    while get_organisation_by_slug(slug) is not None:
        slug = f"{base_slug}-{_secrets.token_hex(3)}"
    org = create_organisation(name=name, slug=slug)
    return {
        "id": str(org.get("id") or ""),
        "name": org.get("name") or name,
        "slug": org.get("slug") or slug,
        "plan": org.get("plan") or "free",
    }


# ---------------------------------------------------------------------------
# Drift / conflicts
# ---------------------------------------------------------------------------

@app.get("/conflicts")
def conflicts(
    include_snoozed: bool = False,
    limit: int = Query(50, ge=1, le=200),
    org_id: str = _OrgDep,
    _admin=_AdminDep,
) -> list[dict]:
    """Unresolved drift conflicts. Pass ?include_snoozed=true to also list snoozed ones."""
    rows = get_unresolved_conflicts(include_snoozed=include_snoozed, org_id=org_id)
    return rows[:limit]


@app.post("/conflicts/{conflict_id}/resolve")
def resolve_conflict_endpoint(
    conflict_id: str, req: ConflictResolveRequest, org_id: str = _OrgDep, _admin=_AdminDep
) -> dict:
    """Apply 'accept' | 'dismiss' | 'snooze' to a conflict."""
    if req.action not in {"accept", "dismiss", "snooze"}:
        raise HTTPException(400, f"unknown action: {req.action!r}")
    try:
        return resolve_conflict(conflict_id, req.action, req.resolved_by, org_id=org_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.get("/skills/{skill_id}/conflicts")
def skill_conflicts(
    skill_id: str,
    limit: int = Query(50, ge=1, le=200),
    org_id: str = _OrgDep,
    _admin=_AdminDep,
) -> list[dict]:
    """Conflict history for a single skill — any status."""
    rows = get_conflict_history(skill_id, org_id=org_id)
    return rows[:limit]


@app.post("/skills/{skill_id}/review")
def skill_mark_reviewed(skill_id: str, org_id: str = _OrgDep, _admin=_AdminDep) -> dict:
    """Mark a skill as freshly reviewed — clears needs_review + bumps reviewed_at."""
    from brain.staleness import mark_as_reviewed

    row = mark_as_reviewed(skill_id, org_id=org_id)
    if not row:
        raise HTTPException(404, f"skill not found: {skill_id}")
    return row


# ---------------------------------------------------------------------------
# Continuous ingestion: status, manual trigger, source CRUD (admin-only)
# ---------------------------------------------------------------------------
# _AdminDep is defined near _OrgDep further up; nothing additional to
# wire here beyond the request models.


class SourceCreate(BaseModel):
    source_type: str
    display_name: str
    config: dict


class SourceUpdate(BaseModel):
    display_name: str | None = None
    config: dict | None = None
    is_active: bool | None = None


_REQUIRED_CONFIG_KEYS = {
    "slack":    {"bot_token", "channel_ids"},
    "notion":   {"integration_token", "page_ids"},
    "gmail":    {"credentials_json", "label_filters"},
    "intercom": {"access_token"},
}


def _validate_source_config(source_type: str, config: dict) -> None:
    required = _REQUIRED_CONFIG_KEYS.get(source_type)
    if required is None:
        return  # github / gmail / intercom — accept any shape for now
    missing = [k for k in required if k not in config or config[k] in (None, "", [])]
    if missing:
        raise HTTPException(400, f"missing required config keys for {source_type}: {missing}")


@app.get("/ingest/status")
def ingest_status(org_id: str = _OrgDep, _admin=_AdminDep) -> dict:
    """Last run summary (this org's) + next scheduled run + cadence."""
    from brain.scheduler import scheduler

    last_db = get_latest_ingest_run(org_id=org_id)
    # The in-memory last_run_summary is a cross-org aggregate; prefer the
    # per-org DB row so each tenant's dashboard is accurate.
    return {
        "last_run": last_db or scheduler.last_run_summary,
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
def sources_list(
    limit: int = Query(50, ge=1, le=200),
    org_id: str = _OrgDep,
    _admin=_AdminDep,
) -> list[dict]:
    """Connected sources for this org. Tokens redacted."""
    rows = list_connected_sources(redact=True, org_id=org_id)
    return rows[:limit]


@app.post("/sources")
def sources_create(req: SourceCreate, org_id: str = _OrgDep, _admin=_AdminDep) -> dict:
    if req.source_type not in {"slack", "notion", "github", "gmail", "intercom"}:
        raise HTTPException(400, f"unsupported source_type: {req.source_type}")
    _validate_source_config(req.source_type, req.config)
    return insert_connected_source(req.source_type, req.display_name, req.config, org_id=org_id)


@app.patch("/sources/{source_id}")
def sources_update(
    source_id: str, req: SourceUpdate, org_id: str = _OrgDep, _admin=_AdminDep
) -> dict:
    existing = get_connected_source(source_id, org_id=org_id)
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
    updated = update_connected_source(source_id, patch, org_id=org_id)
    if updated is None:
        raise HTTPException(404, f"source not found: {source_id}")
    return updated


@app.delete("/sources/{source_id}")
def sources_delete(source_id: str, org_id: str = _OrgDep, _admin=_AdminDep) -> dict:
    """Soft-delete (is_active=false). Stops the scheduler picking it up."""
    ok = deactivate_connected_source(source_id, org_id=org_id)
    if not ok:
        raise HTTPException(404, f"source not found: {source_id}")
    return {"status": "ok", "deactivated": source_id}
