"""Public Agent API mounted at /api/v1.

Two route groups:
  /keys         — key management (no auth — protect at the network layer)
  /skills/...   — agent-callable surface (Bearer-token auth + rate limit)
"""
from __future__ import annotations

import secrets
import time
from datetime import datetime, timezone
from typing import Any

import anthropic
import bcrypt
from fastapi import APIRouter, BackgroundTasks, FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, Field

from api.auth import (
    AdminTokenDep,
    ApiKeyDep,
    DOCS_URL,
    KEY_PREFIX_LEN,
    log_match_request,
)
from brain.embedder import embed_query
from brain.store import (
    deactivate_api_key,
    find_similar_workflow,
    get_skill_by_name_fuzzy,
    get_skill_reviewed_at,
    insert_api_key,
    insert_execution,
    list_api_keys,
    list_skills_index,
    mark_needs_review,
    match_skills_by_embedding,
)

KEY_PLAINTEXT_PREFIX = "fb_live_"
KEY_TOKEN_BYTES = 32
HIGH_CONF = 0.75
LOW_CONF = 0.40

# Source freshness thresholds. <30d fresh, 30-90d aging, >90d stale.
# Used by the freshness envelope on /skills/match and /skills/{name}.
FRESH_DAYS = 30
AGING_DAYS = 90


def _freshness_envelope(
    skill_id: str,
    generated_at: str | None,
    reviewed_at: str | None,
    needs_review_already: bool,
    background: BackgroundTasks,
    org_id: str | None,
) -> dict[str, Any]:
    """Compute the freshness fields for an agent-API skill response:
    last_confirmed_at, days_since_confirmed, source_freshness, freshness_warning.

    Per spec:
      last_confirmed_at  = reviewed_at if set, else generated_at
      days_since_confirmed = days between that timestamp and now (UTC)
      source_freshness   = fresh (<30d) | aging (30-90d) | stale (>=90d)
      freshness_warning  = null (fresh) | advisory text (aging) | escalation
                           instruction (stale)

    Side effect: if stale and needs_review isn't already set, schedules a
    background task to flip needs_review=true so the dashboard's review
    queue picks it up. Background tasks run after the response is sent,
    so this doesn't add latency."""
    last_confirmed = reviewed_at or generated_at
    if not last_confirmed:
        return {
            "last_confirmed_at": None,
            "days_since_confirmed": None,
            "source_freshness": None,
            "freshness_warning": None,
        }
    try:
        confirmed_dt = datetime.fromisoformat(str(last_confirmed).replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return {
            "last_confirmed_at": last_confirmed,
            "days_since_confirmed": None,
            "source_freshness": None,
            "freshness_warning": None,
        }
    # Supabase timestamptz round-trips with a tz, but test fixtures sometimes
    # hand back naive strings — assume UTC so the subtraction can't blow up.
    if confirmed_dt.tzinfo is None:
        confirmed_dt = confirmed_dt.replace(tzinfo=timezone.utc)

    days = max(0, (datetime.now(timezone.utc) - confirmed_dt).days)

    if days < FRESH_DAYS:
        return {
            "last_confirmed_at": last_confirmed,
            "days_since_confirmed": days,
            "source_freshness": "fresh",
            "freshness_warning": None,
        }
    if days < AGING_DAYS:
        return {
            "last_confirmed_at": last_confirmed,
            "days_since_confirmed": days,
            "source_freshness": "aging",
            "freshness_warning": (
                f"This workflow was last reviewed {days} days ago. "
                "Consider verifying before acting on it."
            ),
        }

    # Stale — auto-flag for review (idempotent; mark_needs_review swallows errors).
    if not needs_review_already and skill_id:
        background.add_task(
            mark_needs_review,
            skill_id,
            f"Auto-flagged: {days} days since last confirmation.",
            org_id,
        )
    return {
        "last_confirmed_at": last_confirmed,
        "days_since_confirmed": days,
        "source_freshness": "stale",
        "freshness_warning": (
            f"This workflow has not been reviewed in {days} days and may be "
            "outdated. Escalate to a human rather than acting automatically."
        ),
    }

agent_app = FastAPI(
    title="Flowithm Agent API",
    version="1.0.0",
    description=(
        "Public API for AI agents to query Flowithm at runtime. "
        "Authenticate every call with a Bearer token issued via "
        "POST /keys. See /docs for the live OpenAPI spec."
    ),
    docs_url="/docs",
    openapi_url="/openapi.json",
)


# ---------------------------------------------------------------------------
# Exception handlers — sub-apps don't inherit the parent app's handlers so
# we register our own to ensure clean {error, code, docs} envelopes instead
# of FastAPI's default plain-text "Internal Server Error".
# ---------------------------------------------------------------------------

from fastapi import Request as _Request  # noqa: E402 (local to avoid circular)
from fastapi.responses import JSONResponse as _JSONResponse  # noqa: E402
from fastapi.exceptions import RequestValidationError as _RVE  # noqa: E402


@agent_app.exception_handler(HTTPException)
async def _agent_http_exc(request: _Request, exc: HTTPException):
    detail = exc.detail
    if isinstance(detail, dict) and "error" in detail and "code" in detail:
        body = detail
    else:
        body = {"error": str(detail) if detail else "Error", "code": "INTERNAL_ERROR", "docs": DOCS_URL}
    return _JSONResponse(status_code=exc.status_code, content=body, headers=getattr(exc, "headers", None))


@agent_app.exception_handler(_RVE)
async def _agent_validation_exc(request: _Request, exc: _RVE):
    return _JSONResponse(
        status_code=422,
        content={"error": "Invalid request", "code": "INVALID_REQUEST", "docs": DOCS_URL, "details": exc.errors()},
    )


@agent_app.exception_handler(Exception)
async def _agent_unhandled_exc(request: _Request, exc: Exception):
    import logging
    logging.getLogger("flowithm.agent").error("unhandled exception", exc_info=True)
    return _JSONResponse(
        status_code=500,
        content={"error": "Internal server error", "code": "INTERNAL_ERROR", "docs": DOCS_URL},
    )


# ---------------------------------------------------------------------------
# Standard error envelope
# ---------------------------------------------------------------------------

def _err(status: int, code: str, error: str, **extra: Any) -> HTTPException:
    payload = {"error": error, "code": code, "docs": DOCS_URL}
    payload.update(extra)
    return HTTPException(status_code=status, detail=payload)


@agent_app.exception_handler(HTTPException)
async def _http_exception_handler(request: Request, exc: HTTPException):
    """Force every error into the {error, code, docs} envelope, even when
    a downstream library raises a plain HTTPException."""
    from fastapi.responses import JSONResponse

    detail = exc.detail
    if isinstance(detail, dict) and "error" in detail and "code" in detail:
        body = detail
    else:
        body = {
            "error": str(detail),
            "code": _code_for_status(exc.status_code),
            "docs": DOCS_URL,
        }
    return JSONResponse(status_code=exc.status_code, content=body, headers=getattr(exc, "headers", None))


def _code_for_status(status: int) -> str:
    return {
        400: "INVALID_REQUEST",
        401: "INVALID_API_KEY",
        404: "SKILL_NOT_FOUND",
        429: "RATE_LIMIT_EXCEEDED",
        500: "INTERNAL_ERROR",
    }.get(status, "INTERNAL_ERROR")


# ---------------------------------------------------------------------------
# /keys — management (admin surface)
# ---------------------------------------------------------------------------

class CreateKeyRequest(BaseModel):
    name: str = Field(..., min_length=1, description="Human label, e.g. 'Production support bot'.")
    org_id: str | None = Field(
        default=None,
        description="Organisation this key belongs to. Defaults to the ORG_ID env (single-tenant fallback).",
    )


class CreateKeyResponse(BaseModel):
    id: str
    prefix: str
    key: str = Field(..., description="Plaintext key — shown ONCE. Store it now; it cannot be retrieved.")
    name: str


class ApiKeySummary(BaseModel):
    id: str
    name: str
    prefix: str
    created_at: str | None = None
    last_used_at: str | None = None
    request_count: int = 0
    is_active: bool = True


@agent_app.post(
    "/keys",
    response_model=CreateKeyResponse,
    summary="Generate a new API key (admin)",
    description=(
        "Admin-only. Requires `Authorization: Bearer $ADMIN_TOKEN`. "
        "Generates a cryptographically random API key, stores its bcrypt "
        "hash, and returns the plaintext exactly once. The plaintext "
        "cannot be retrieved later — the only way to replace a lost key "
        "is to revoke it and create a new one."
    ),
)
def create_api_key(req: CreateKeyRequest, _admin=AdminTokenDep) -> dict[str, Any]:
    plaintext = KEY_PLAINTEXT_PREFIX + secrets.token_urlsafe(KEY_TOKEN_BYTES)
    prefix = plaintext[:KEY_PREFIX_LEN]
    hashed = bcrypt.hashpw(plaintext.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    row = insert_api_key(req.name, prefix, hashed, org_id=req.org_id or None)
    # Audit log without leaking the prefix — anything an attacker could
    # use to narrow down a brute-force search lives only in the DB.
    from brain.logger import get_logger
    get_logger("flowithm.agent_api").info("api key issued", extra={
        "id": row.get("id"),
        "name": req.name,
    })
    return {
        "id": str(row.get("id") or ""),
        "prefix": prefix,
        "key": plaintext,
        "name": req.name,
    }


@agent_app.get(
    "/keys",
    response_model=list[ApiKeySummary],
    summary="List API keys (admin)",
    description=(
        "Admin-only. Requires `Authorization: Bearer $ADMIN_TOKEN`. "
        "Returns every key (active and revoked). Plaintext is never included."
    ),
)
def list_keys(
    request: Request,
    _admin=AdminTokenDep,
    limit: int = Query(50, ge=1, le=200),
) -> list[dict[str, Any]]:
    org = request.headers.get("x-org-id") or None
    rows = list_api_keys(org_id=org)
    # list_api_keys returns the DB column `key_prefix`; ApiKeySummary expects
    # `prefix`. Map explicitly so response validation doesn't 500.
    return [
        {
            "id": str(r.get("id") or ""),
            "name": r.get("name") or "",
            "prefix": r.get("key_prefix") or "",
            "created_at": r.get("created_at"),
            "last_used_at": r.get("last_used_at"),
            "request_count": r.get("request_count") or 0,
            "is_active": bool(r.get("is_active", True)),
        }
        for r in rows[:limit]
    ]


@agent_app.delete(
    "/keys/{key_id}",
    summary="Revoke an API key (admin)",
    description=(
        "Admin-only. Requires `Authorization: Bearer $ADMIN_TOKEN`. "
        "Soft-delete: sets is_active=false. Subsequent auth attempts return 401 REVOKED_API_KEY."
    ),
)
def revoke_key(key_id: str, request: Request, _admin=AdminTokenDep) -> dict[str, Any]:
    org = request.headers.get("x-org-id") or None
    ok = deactivate_api_key(key_id, org_id=org)
    if not ok:
        raise _err(404, "INVALID_REQUEST", f"API key not found: {key_id}")
    return {"status": "ok", "revoked": key_id}


# ---------------------------------------------------------------------------
# /skills — public agent surface (auth required)
# ---------------------------------------------------------------------------

class SkillIndexRow(BaseModel):
    id: str
    process: str
    trigger: str
    step_count: int
    last_updated: str | None = None
    source: str
    needs_review: bool = False
    needs_review_reason: str | None = None


class SkillIndexResponse(BaseModel):
    skills: list[SkillIndexRow]
    total: int


@agent_app.get(
    "/skills",
    response_model=SkillIndexResponse,
    summary="List skills (lightweight index)",
    description=(
        "Index of every non-archived skill — id, process, trigger, "
        "step_count, last_updated, source. Use ?updated_after=ISO_DATE "
        "for incremental sync; ?source= to filter; ?limit/?offset for "
        "pagination."
    ),
    responses={
        200: {
            "content": {
                "application/json": {
                    "example": {
                        "skills": [
                            {
                                "id": "62a4c719-75fa-4f72-bb36-c26dc43eabc2",
                                "process": "Customer refund handling",
                                "trigger": "customer files refund request via support email",
                                "step_count": 14,
                                "last_updated": "2026-05-07T13:53:17Z",
                                "source": "manual",
                            }
                        ],
                        "total": 1,
                    }
                }
            }
        }
    },
)
def list_skills_endpoint(
    request: Request,
    api_key=ApiKeyDep,
    source: str | None = Query(None, description="Filter: slack | notion | manual | github"),
    updated_after: str | None = Query(None, description="ISO 8601 timestamp; only skills updated at or after this time."),
    needs_review: bool | None = Query(None, description="Filter to only skills with the staleness flag set."),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    rows, total = list_skills_index(
        source=source,
        updated_after=updated_after,
        needs_review=needs_review,
        limit=limit,
        offset=offset,
        org_id=getattr(request.state, "org_id", None) or None,
    )
    return {"skills": rows, "total": total}


@agent_app.get(
    "/skills/match",
    summary="Semantic match — natural-language → workflow",
    description=(
        "Embeds the query (voyage-3) and runs cosine similarity over "
        "skills.summary_embedding. Returns the top hit with a confidence "
        "tier ('high' >= 0.75, 'medium' 0.40-0.75). Below 0.40, returns "
        "404 SKILL_NOT_FOUND with up to 3 closest suggestions.\n\n"
        "**Freshness envelope** (top-level fields):\n"
        "- `last_confirmed_at` — ISO8601 of `reviewed_at` if set, else `generated_at`. "
        "When a workflow was last vouched for as accurate.\n"
        "- `days_since_confirmed` — integer days between `last_confirmed_at` and now (UTC).\n"
        "- `source_freshness` — `fresh` (<30d) | `aging` (30-90d) | `stale` (>=90d). "
        "Agents should treat `stale` workflows as advisory and escalate.\n"
        "- `freshness_warning` — null when fresh; a human-readable advisory when aging; "
        "an escalation instruction when stale. **Surface this verbatim to end users / "
        "operators when present.**\n\n"
        "Side effect: a stale match auto-flags the underlying skill for review "
        "(needs_review=true) so it appears in the dashboard's review queue."
    ),
    responses={
        200: {
            "content": {
                "application/json": {
                    "example": {
                        "matched": True,
                        "confidence": "high",
                        "similarity_score": 0.83,
                        "query": "customer wants a refund after 45 days",
                        "skill": {"id": "...", "process": "Customer refund handling", "...": "..."},
                        "last_confirmed_at": "2026-02-01T12:00:00+00:00",
                        "days_since_confirmed": 103,
                        "source_freshness": "stale",
                        "freshness_warning": (
                            "This workflow has not been reviewed in 103 days and may be "
                            "outdated. Escalate to a human rather than acting automatically."
                        ),
                    }
                }
            }
        },
        404: {
            "content": {
                "application/json": {
                    "example": {
                        "error": "No workflow matched query 'customer wants snowboarding gear'",
                        "code": "SKILL_NOT_FOUND",
                        "docs": DOCS_URL,
                        "suggestions": [],
                    }
                }
            }
        },
    },
)
def match_skill_endpoint(
    request: Request,
    background: BackgroundTasks,
    api_key=ApiKeyDep,
    q: str = Query(..., min_length=2, description="Natural-language description of the situation."),
) -> dict[str, Any]:
    started = time.perf_counter()
    org_id = getattr(request.state, "org_id", None) or None
    try:
        embedding = embed_query(q)
    except ValueError:
        raise _err(400, "INVALID_REQUEST", "Query text is empty.")
    except Exception as exc:
        raise _err(503, "EMBEDDING_UNAVAILABLE", f"Embedding service error: {exc}")

    matches = match_skills_by_embedding(embedding, k=3, org_id=org_id)
    if not matches:
        log_match_request(background, request, q, None)
        raise _err(
            404, "SKILL_NOT_FOUND",
            f"No workflow matched query {q!r}",
            suggestions=[],
        )

    top = matches[0]
    score = float(top.get("similarity") or 0.0)

    if score < LOW_CONF:
        log_match_request(background, request, q, None)
        suggestions = [
            {
                "id": str(m.get("id") or ""),
                "process": m.get("process_name") or "",
                "similarity_score": float(m.get("similarity") or 0.0),
            }
            for m in matches
        ]
        raise _err(
            404, "SKILL_NOT_FOUND",
            f"No workflow matched query {q!r} above the confidence threshold.",
            suggestions=suggestions,
        )

    confidence = "high" if score >= HIGH_CONF else "medium"
    skill = _row_to_agent_skill(top)
    log_match_request(background, request, q, skill["id"])

    # The match_skills RPC doesn't return reviewed_at; fetch it for the top
    # hit so the freshness envelope can use "reviewed_at if set, else
    # generated_at" per the spec.
    reviewed_at = get_skill_reviewed_at(skill["id"], org_id=org_id)
    freshness = _freshness_envelope(
        skill_id=skill["id"],
        generated_at=top.get("generated_at"),
        reviewed_at=reviewed_at,
        needs_review_already=bool(top.get("needs_review")),
        background=background,
        org_id=org_id,
    )

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    if elapsed_ms > 1000:
        print(f"[agent-api] /skills/match slow: {elapsed_ms}ms (q={q!r})", flush=True)

    return {
        "matched": True,
        "confidence": confidence,
        "similarity_score": round(score, 4),
        "query": q,
        "skill": skill,
        **freshness,
    }


@agent_app.get(
    "/skills/{process_name}",
    summary="Fetch a skill by exact or fuzzy name",
    description=(
        "Tries an exact (case-insensitive) match first. If none, falls back "
        "to pg_trgm fuzzy match at threshold 0.4. On miss, returns 404 with "
        "the closest match name (above 0.2) for the agent to retry with.\n\n"
        "Response includes the same freshness fields as `/skills/match`:\n"
        "- `last_confirmed_at` — `reviewed_at` if set, else `generated_at`.\n"
        "- `days_since_confirmed` — integer days vs. now (UTC).\n"
        "- `source_freshness` — `fresh` | `aging` | `stale`.\n"
        "- `freshness_warning` — null when fresh; a verbatim advisory for aging "
        "or escalation for stale.\n\n"
        "Stale skills auto-flag `needs_review=true` server-side."
    ),
)
def get_skill_endpoint(
    process_name: str,
    request: Request,
    background: BackgroundTasks,
    api_key=ApiKeyDep,
) -> dict[str, Any]:
    org_id = getattr(request.state, "org_id", None) or None
    skill, closest = get_skill_by_name_fuzzy(process_name, org_id=org_id)
    if skill:
        agent_skill = _workflow_to_agent_skill(skill)
        freshness = _freshness_envelope(
            skill_id=str(agent_skill.get("id") or ""),
            generated_at=skill.get("generated_at"),
            reviewed_at=skill.get("reviewed_at"),
            needs_review_already=bool(skill.get("needs_review")),
            background=background,
            org_id=org_id,
        )
        return {**agent_skill, **freshness}
    raise _err(
        404, "SKILL_NOT_FOUND",
        f"No workflow found for {process_name!r}",
        closest_match=closest,
    )


class ExecuteRequest(BaseModel):
    skill_id: str
    step_number: int | None = None
    outcome: str = Field(..., description="One of: completed | escalated | exception_triggered")
    exception_note: str | None = None
    duration_seconds: int | None = None


@agent_app.post(
    "/skills/execute",
    summary="Report execution outcome (agent feedback loop)",
    description=(
        "Agents call this after each workflow step. If exception_note is "
        "supplied, Claude judges whether it represents a genuinely-new "
        "edge case; if so, a drift check is scheduled in the background."
    ),
)
def execute_skill_endpoint(
    body: ExecuteRequest,
    request: Request,
    background: BackgroundTasks,
    api_key=ApiKeyDep,
) -> dict[str, Any]:
    if body.outcome not in {"completed", "escalated", "exception_triggered"}:
        raise _err(400, "INVALID_REQUEST", f"unknown outcome: {body.outcome!r}")

    org_id = getattr(request.state, "org_id", None) or None
    execution_id = insert_execution(
        skill_id=body.skill_id,
        step_number=body.step_number,
        outcome=body.outcome,
        exception_note=body.exception_note,
        duration_seconds=body.duration_seconds,
        org_id=org_id,
    )

    if body.exception_note:
        background.add_task(_maybe_trigger_drift_from_exception, body.skill_id, body.exception_note)

    return {"received": True, "execution_id": execution_id}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _row_to_agent_skill(row: dict[str, Any]) -> dict[str, Any]:
    """match_skills RPC row → public skill JSON."""
    return {
        "id": str(row.get("id") or ""),
        "process": row.get("process_name") or "",
        "description": row.get("description") or "",
        "trigger": row.get("process_trigger") or "",
        "steps": row.get("steps") or [],
        "decision_rules": row.get("decision_rules") or [],
        "approvals": row.get("approvals") or [],
        "exceptions": row.get("exceptions") or [],
        "sources": row.get("sources") or [],
        "source": row.get("source") or "manual",
        "version": int(row.get("version") or 1),
        "last_updated": row.get("generated_at"),
        "needs_review": bool(row.get("needs_review")),
        "needs_review_reason": row.get("needs_review_reason"),
    }


def _workflow_to_agent_skill(workflow: dict[str, Any]) -> dict[str, Any]:
    """_row_to_workflow output → public agent shape (omits raw_text, archived flags)."""
    return {
        "id": workflow.get("id"),
        "process": workflow.get("process") or "",
        "description": workflow.get("description") or "",
        "trigger": workflow.get("trigger") or "",
        "steps": workflow.get("steps") or [],
        "decision_rules": workflow.get("decision_rules") or [],
        "approvals": workflow.get("approvals") or [],
        "exceptions": workflow.get("exceptions") or [],
        "sources": workflow.get("sources") or [],
        "source": workflow.get("source") or "manual",
        "version": int(workflow.get("version") or 1) if workflow.get("version") is not None else 1,
        "last_updated": workflow.get("generated_at"),
        "needs_review": bool(workflow.get("needs_review")),
        "needs_review_reason": workflow.get("needs_review_reason"),
    }


def _maybe_trigger_drift_from_exception(skill_id: str, exception_note: str) -> None:
    """If Claude says the note isn't already covered by the skill, kick off a drift check."""
    try:
        from brain.drift import schedule_drift_check
        from brain.store import get_workflow

        skill = get_workflow(skill_id)
        if not skill:
            return

        from brain.drift import _get_anthropic
        client = _get_anthropic()
        prompt = (
            f"Existing skill (JSON):\n{skill}\n\n"
            f"Exception just reported by an agent: {exception_note}\n\n"
            "Is this exception already covered (anywhere — steps, decision_rules, "
            "approvals, or exceptions) in the skill? Reply only YES or NO."
        )
        from brain.anthropic_client import messages_create
        msg = messages_create(
            client,
            model="claude-haiku-4-5-20251001",
            max_tokens=8,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in msg.content if hasattr(b, "text")).strip().upper()
        if text.startswith("NO"):
            schedule_drift_check(exception_note, skill)
    except Exception as exc:
        print(f"[agent-api] _maybe_trigger_drift_from_exception failed: {exc}", flush=True)
