"""brain.anthropic_client — retry, circuit breaker, error classification."""
from __future__ import annotations

import time

import anthropic
import pytest


def test_messages_create_passes_through_on_success(monkeypatch):
    """Happy path: one call, success returned, breaker closes."""
    from brain import anthropic_client

    # Reset breaker state.
    anthropic_client._consecutive_failures = 0
    anthropic_client._open_until = 0.0

    sentinel = object()

    class _FakeMsgs:
        def create(self, **kwargs):
            return sentinel

    class _FakeClient:
        messages = _FakeMsgs()

    out = anthropic_client.messages_create(_FakeClient(), max_tokens=10, messages=[])
    assert out is sentinel


def test_messages_create_retries_on_rate_limit(monkeypatch):
    """RateLimitError on attempt 1, success on attempt 2 → exactly 2 calls."""
    from brain import anthropic_client

    anthropic_client._consecutive_failures = 0
    anthropic_client._open_until = 0.0

    monkeypatch.setattr(time, "sleep", lambda *_: None)  # don't actually sleep

    calls = {"n": 0}
    sentinel = object()

    class _FakeMsgs:
        def create(self, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                # Build a RateLimitError without going through the network.
                raise anthropic.RateLimitError(
                    message="rate limited",
                    response=_make_response(429),
                    body={},
                )
            return sentinel

    class _FakeClient:
        messages = _FakeMsgs()

    out = anthropic_client.messages_create(_FakeClient())
    assert calls["n"] == 2
    assert out is sentinel


def test_circuit_opens_after_consecutive_failures(monkeypatch):
    """5 in-a-row terminal failures → 6th call raises CircuitOpenError."""
    from brain import anthropic_client

    anthropic_client._consecutive_failures = 0
    anthropic_client._open_until = 0.0
    monkeypatch.setattr(time, "sleep", lambda *_: None)

    class _AlwaysAuthError:
        class messages:
            @staticmethod
            def create(**kwargs):
                raise anthropic.AuthenticationError(
                    message="bad key",
                    response=_make_response(401),
                    body={},
                )

    # Fail 5 times — auth errors are non-retryable so each call is 1 attempt.
    for _ in range(5):
        with pytest.raises(anthropic.AuthenticationError):
            anthropic_client.messages_create(_AlwaysAuthError())

    # Breaker should now be open.
    is_open, _ = anthropic_client._circuit_status()
    assert is_open is True

    with pytest.raises(anthropic_client.CircuitOpenError):
        anthropic_client.messages_create(_AlwaysAuthError())


def test_success_resets_breaker(monkeypatch):
    """A success after partial failure should zero the consecutive counter."""
    from brain import anthropic_client

    anthropic_client._consecutive_failures = 3
    anthropic_client._open_until = 0.0

    class _Ok:
        class messages:
            @staticmethod
            def create(**kwargs):
                return object()

    anthropic_client.messages_create(_Ok())
    assert anthropic_client._consecutive_failures == 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_response(status_code: int):
    """anthropic exception classes want an httpx-like Response. Build a
    minimal stub that exposes the few attributes the SDK touches."""
    import httpx
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return httpx.Response(status_code=status_code, request=req)
