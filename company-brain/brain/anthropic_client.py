"""Resilient wrapper around `anthropic.Anthropic().messages.create()`.

Adds three things every Anthropic call in the codebase needs:

1. **Retries** — 3 attempts with 1s/2s/4s exponential backoff on transient
   failures (rate-limit 429, server 5xx, connection / timeout errors).
   Permanent errors (auth, invalid request, refusal) raise immediately.
2. **Per-call timeout** — `ANTHROPIC_TIMEOUT_SECONDS` env (default 60s).
3. **Circuit breaker** — after 5 consecutive failures the breaker opens
   for 60s and `CircuitOpenError` is raised on every call rather than
   hammering a sick API. Closes again on the next successful call.

Public surface:
    messages_create(client, **kwargs) -> Message
    CircuitOpenError                  — raised when the breaker is open
    AnthropicCallError                — raised after retries are exhausted
"""
from __future__ import annotations

import os
import threading
import time
from typing import Any

import anthropic

from brain.logger import get_logger

log = get_logger("flowithm.anthropic")

DEFAULT_TIMEOUT = float(os.getenv("ANTHROPIC_TIMEOUT_SECONDS", "60"))
MAX_RETRIES = 3
INITIAL_BACKOFF = 1.0  # 1s -> 2s -> 4s
BREAKER_THRESHOLD = 5  # consecutive failures
BREAKER_OPEN_SECONDS = 60.0


class CircuitOpenError(RuntimeError):
    """Raised when the Anthropic circuit breaker is open. Callers should
    return a degraded response (cached answer / empty list / etc.) rather
    than retrying immediately."""


class AnthropicCallError(RuntimeError):
    """Retries exhausted on a transient error path. Distinct from
    CircuitOpenError so callers can decide whether to bubble up or
    degrade — most paths just bubble."""


# Circuit breaker state. Module-level so all callers share one breaker.
_lock = threading.Lock()
_consecutive_failures = 0
_open_until: float = 0.0  # monotonic clock; 0 = closed


def _record_failure() -> None:
    global _consecutive_failures, _open_until
    with _lock:
        _consecutive_failures += 1
        if _consecutive_failures >= BREAKER_THRESHOLD:
            _open_until = time.monotonic() + BREAKER_OPEN_SECONDS
            log.error("anthropic circuit breaker OPEN", extra={
                "consecutive_failures": _consecutive_failures,
                "reopen_after_seconds": BREAKER_OPEN_SECONDS,
            })


def _record_success() -> None:
    global _consecutive_failures, _open_until
    with _lock:
        if _consecutive_failures > 0 or _open_until > 0:
            log.info("anthropic circuit breaker CLOSED",
                     extra={"prior_failures": _consecutive_failures})
        _consecutive_failures = 0
        _open_until = 0.0


def _circuit_status() -> tuple[bool, float]:
    with _lock:
        if _open_until == 0.0:
            return False, 0.0
        remaining = _open_until - time.monotonic()
        if remaining <= 0:
            return False, 0.0  # auto-reset on next call
        return True, remaining


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, anthropic.RateLimitError):
        return True
    if isinstance(exc, (anthropic.APIConnectionError, anthropic.APITimeoutError)):
        return True
    if isinstance(exc, anthropic.APIStatusError):
        return getattr(exc, "status_code", 0) in {500, 502, 503, 504, 529}
    return False


def _retry_after_seconds(exc: BaseException) -> float | None:
    """Pull Retry-After from a RateLimitError if the header is present."""
    if not isinstance(exc, anthropic.RateLimitError):
        return None
    resp = getattr(exc, "response", None)
    if resp is None:
        return None
    val = resp.headers.get("retry-after") if hasattr(resp, "headers") else None
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def messages_create(client: anthropic.Anthropic, **kwargs: Any):
    """Drop-in replacement for `client.messages.create(**kwargs)`.

    `timeout` defaults to ANTHROPIC_TIMEOUT_SECONDS unless the caller
    overrides it. Passes everything else through verbatim.

    Raises:
        CircuitOpenError  — breaker is open; caller should degrade
        AnthropicCallError — retries exhausted on a transient error
        anthropic.*       — terminal Anthropic errors (auth, invalid
                            request, etc.) bubble up unchanged
    """
    open_, remaining = _circuit_status()
    if open_:
        raise CircuitOpenError(
            f"Anthropic circuit breaker is open for another {int(remaining)}s "
            "after consecutive failures."
        )

    kwargs.setdefault("timeout", DEFAULT_TIMEOUT)
    backoff = INITIAL_BACKOFF
    last_exc: BaseException | None = None

    for attempt in range(MAX_RETRIES):
        try:
            result = client.messages.create(**kwargs)
            _record_success()
            return result
        except anthropic.BadRequestError as exc:
            # Surface "context too long" specifically — caller can decide
            # to truncate and retry; we don't auto-truncate here because
            # different callers want different summarisation strategies.
            log.warning("anthropic bad request",
                        extra={"error": str(exc), "attempt": attempt + 1})
            _record_failure()
            raise
        except anthropic.AuthenticationError:
            log.error("anthropic auth failed — check ANTHROPIC_API_KEY")
            _record_failure()
            raise
        except Exception as exc:
            last_exc = exc
            if not _is_retryable(exc):
                log.error("anthropic non-retryable error",
                          exc_info=True, extra={"error": str(exc)})
                _record_failure()
                raise
            wait = _retry_after_seconds(exc) or backoff
            log.warning("anthropic transient error — retrying", extra={
                "attempt": attempt + 1,
                "max_attempts": MAX_RETRIES,
                "sleep_seconds": wait,
                "error": str(exc),
            })
            if attempt == MAX_RETRIES - 1:
                break
            time.sleep(wait)
            backoff *= 2

    _record_failure()
    raise AnthropicCallError(
        f"Anthropic call failed after {MAX_RETRIES} retries"
    ) from last_exc
