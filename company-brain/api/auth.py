"""Bearer-token auth + sliding-window rate limit + per-request logging.

Used as a FastAPI dependency on every /api/v1/* route. Side-effects
(usage counter bump, audit row write) run in BackgroundTasks so they
never block the response. Slow requests (>1s) print a warning to stdout.
"""
from __future__ import annotations

import os
import threading
import time
from collections import defaultdict, deque
from typing import Any

import bcrypt
from fastapi import BackgroundTasks, Depends, HTTPException, Request

from brain.store import (
    find_api_keys_by_prefix,
    increment_api_key_usage,
    insert_api_request,
)

DOCS_URL = "https://flowithm.io/docs"
KEY_PREFIX_LEN = 12
RATE_LIMIT_PER_MINUTE = 100
SLOW_REQUEST_THRESHOLD_MS = 1000

# Sliding-window in-memory limiter. Process-local; resets on restart.
# Acceptable for single-process deployments. For multi-worker or HA,
# swap for Redis with INCR + EXPIRE.
_rate_lock = threading.Lock()
_rate_buckets: dict[str, deque[float]] = defaultdict(deque)


def _err(status: int, code: str, error: str, **extra: Any) -> HTTPException:
    payload = {"error": error, "code": code, "docs": DOCS_URL}
    payload.update(extra)
    return HTTPException(status_code=status, detail=payload)


def _bcrypt_verify(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def _check_rate_limit(key_id: str) -> None:
    """100 req/60s sliding window. Raises 429 with Retry-After on overflow."""
    now = time.monotonic()
    cutoff = now - 60.0
    with _rate_lock:
        bucket = _rate_buckets[key_id]
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= RATE_LIMIT_PER_MINUTE:
            retry_after = max(1, int(60 - (now - bucket[0])))
            err = _err(
                429,
                "RATE_LIMIT_EXCEEDED",
                f"Rate limit of {RATE_LIMIT_PER_MINUTE} req/min exceeded.",
                retry_after_seconds=retry_after,
            )
            err.headers = {"Retry-After": str(retry_after)}
            raise err
        bucket.append(now)


def _log_request(
    api_key_id: str,
    endpoint: str,
    response_time_ms: int,
    query_text: str | None = None,
    matched_skill_id: str | None = None,
) -> None:
    """Background-task body — never raises."""
    try:
        increment_api_key_usage(api_key_id)
        insert_api_request(
            api_key_id=api_key_id,
            endpoint=endpoint,
            response_time_ms=response_time_ms,
            query_text=query_text,
            matched_skill_id=matched_skill_id,
        )
        if response_time_ms > SLOW_REQUEST_THRESHOLD_MS:
            print(
                f"[agent-api] slow request: {endpoint} took {response_time_ms}ms"
                + (f" (q={query_text!r})" if query_text else ""),
                flush=True,
            )
    except Exception as exc:
        print(f"[agent-api] _log_request failed: {exc}", flush=True)


def _extract_bearer(authorization: str | None) -> str:
    if not authorization:
        raise _err(401, "MISSING_API_KEY", "Missing API key. Pass `Authorization: Bearer <key>`.")
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        raise _err(401, "MISSING_API_KEY", "Malformed Authorization header. Expected `Bearer <key>`.")
    return parts[1].strip()


async def verify_api_key(
    request: Request,
    background: BackgroundTasks,
) -> dict[str, Any]:
    """FastAPI dependency: parses Bearer token, verifies bcrypt, applies
    rate limit, wires up the post-response audit log on `background`.

    Returns the matched api_keys row (without key_hash). Routes that need
    to log a query string or matched skill id should attach a second
    background task in the route body — see /api/v1/skills/match.
    """
    started = time.perf_counter()
    token = _extract_bearer(request.headers.get("authorization"))

    prefix = token[:KEY_PREFIX_LEN]
    candidates = find_api_keys_by_prefix(prefix)
    if not candidates:
        raise _err(401, "INVALID_API_KEY", "Invalid API key.")

    matched = None
    for c in candidates:
        if _bcrypt_verify(token, c.get("key_hash") or ""):
            matched = c
            break
    if not matched:
        raise _err(401, "INVALID_API_KEY", "Invalid API key.")
    if not matched.get("is_active", True):
        raise _err(401, "REVOKED_API_KEY", "API key has been revoked.")

    _check_rate_limit(str(matched["id"]))

    request.state.api_key_id = str(matched["id"])
    request.state.started_perf = started
    request.state.endpoint = request.url.path

    background.add_task(
        _log_request,
        api_key_id=str(matched["id"]),
        endpoint=request.url.path,
        response_time_ms=int((time.perf_counter() - started) * 1000),
    )

    return {k: v for k, v in matched.items() if k != "key_hash"}


def log_match_request(
    background: BackgroundTasks,
    request: Request,
    query_text: str,
    matched_skill_id: str | None,
) -> None:
    """Helper for /skills/match — logs the query + matched skill, replacing
    the basic audit task verify_api_key already scheduled."""
    background.add_task(
        _log_request,
        api_key_id=getattr(request.state, "api_key_id", ""),
        endpoint=getattr(request.state, "endpoint", request.url.path),
        response_time_ms=int(
            (time.perf_counter() - getattr(request.state, "started_perf", time.perf_counter())) * 1000
        ),
        query_text=query_text,
        matched_skill_id=matched_skill_id,
    )


# Re-exported so route handlers don't need to import auth + Depends both.
ApiKeyDep = Depends(verify_api_key)


# ---------------------------------------------------------------------------
# Admin gate — protects /api/v1/keys (key management).
# ---------------------------------------------------------------------------
# Lives behind a single static Bearer token from $ADMIN_TOKEN. The dashboard
# proxies key-management calls through Next.js server routes that inject
# this header; the plaintext never leaves the server. If $ADMIN_TOKEN is
# unset, every call to /keys is refused — fail closed.

def verify_admin_token(request: Request) -> None:
    expected = os.environ.get("ADMIN_TOKEN", "").strip()
    if not expected:
        raise _err(
            500,
            "INTERNAL_ERROR",
            "ADMIN_TOKEN is not configured on the server. "
            "Set it in .env before calling /api/v1/keys.",
        )
    token = _extract_bearer(request.headers.get("authorization"))
    if not _constant_time_eq(token, expected):
        raise _err(401, "INVALID_API_KEY", "Invalid admin token.")


def _constant_time_eq(a: str, b: str) -> bool:
    """Constant-time comparison so an attacker can't time-guess characters."""
    if len(a) != len(b):
        return False
    diff = 0
    for x, y in zip(a, b):
        diff |= ord(x) ^ ord(y)
    return diff == 0


AdminTokenDep = Depends(verify_admin_token)
