"""api.auth — bearer parsing, bcrypt verify, rate limiter, admin token gate.

The rate-limit + circuit-breaker bits are time-sensitive; we manipulate
the in-memory state directly rather than waiting wall-clock seconds.
"""
from __future__ import annotations

import bcrypt
import pytest


# ---------------------------------------------------------------------------
# Bearer token parsing + bcrypt verify (pure logic)
# ---------------------------------------------------------------------------

def test_extract_bearer_missing_raises():
    from api.auth import _extract_bearer

    with pytest.raises(Exception) as exc:
        _extract_bearer(None)
    assert exc.value.status_code == 401


def test_extract_bearer_malformed_raises():
    from api.auth import _extract_bearer

    with pytest.raises(Exception) as exc:
        _extract_bearer("Token abc123")  # wrong scheme
    assert exc.value.status_code == 401


def test_extract_bearer_strips_whitespace():
    from api.auth import _extract_bearer
    assert _extract_bearer("Bearer   abc123  ") == "abc123"


def test_bcrypt_verify_round_trip():
    from api.auth import _bcrypt_verify

    plaintext = "fb_live_some-key-text"
    hashed = bcrypt.hashpw(plaintext.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    assert _bcrypt_verify(plaintext, hashed) is True
    assert _bcrypt_verify("fb_live_wrong", hashed) is False


def test_bcrypt_verify_rejects_garbage():
    from api.auth import _bcrypt_verify
    assert _bcrypt_verify("anything", "not-a-real-hash") is False


# ---------------------------------------------------------------------------
# Rate limiter (sliding window, 100 req/min)
# ---------------------------------------------------------------------------

def test_rate_limit_allows_under_threshold(monkeypatch):
    from api import auth

    # Fresh bucket per test.
    auth._rate_buckets.clear()
    for _ in range(99):
        auth._check_rate_limit("key-A")
    auth._check_rate_limit("key-A")  # 100th — still allowed


def test_rate_limit_blocks_after_threshold(monkeypatch):
    from api import auth

    auth._rate_buckets.clear()
    for _ in range(100):
        auth._check_rate_limit("key-B")
    with pytest.raises(Exception) as exc:
        auth._check_rate_limit("key-B")
    assert exc.value.status_code == 429
    assert exc.value.headers and exc.value.headers.get("Retry-After")


def test_rate_limit_keys_isolated():
    from api import auth

    auth._rate_buckets.clear()
    for _ in range(100):
        auth._check_rate_limit("key-X")
    # Different key — not affected by key-X's saturation.
    auth._check_rate_limit("key-Y")


def test_rate_limit_window_drops_old_entries(monkeypatch):
    """Force the bucket to contain only stale timestamps; the next call
    should sweep them out and not 429."""
    from api import auth

    auth._rate_buckets.clear()
    bucket = auth._rate_buckets["key-Z"]
    for i in range(100):
        bucket.append(0.0)  # well in the past
    auth._check_rate_limit("key-Z")  # sweep + accept


# ---------------------------------------------------------------------------
# Admin token gate
# ---------------------------------------------------------------------------

def test_admin_token_unset_returns_500(monkeypatch):
    from api.auth import verify_admin_token
    from fastapi import HTTPException

    monkeypatch.delenv("ADMIN_TOKEN", raising=False)

    class _R:
        headers = {"authorization": "Bearer whatever"}

    with pytest.raises(HTTPException) as exc:
        verify_admin_token(_R())
    assert exc.value.status_code == 500
    assert exc.value.detail.get("code") == "INTERNAL_ERROR"


def test_admin_token_wrong_returns_401(monkeypatch):
    from api.auth import verify_admin_token
    from fastapi import HTTPException

    monkeypatch.setenv("ADMIN_TOKEN", "correct-secret")

    class _R:
        headers = {"authorization": "Bearer wrong"}

    with pytest.raises(HTTPException) as exc:
        verify_admin_token(_R())
    assert exc.value.status_code == 401


def test_admin_token_correct_passes(monkeypatch):
    from api.auth import verify_admin_token

    monkeypatch.setenv("ADMIN_TOKEN", "the-admin-token")

    class _R:
        headers = {"authorization": "Bearer the-admin-token"}

    # No raise.
    verify_admin_token(_R())


def test_admin_token_constant_time_eq():
    from api.auth import _constant_time_eq

    assert _constant_time_eq("abc", "abc") is True
    assert _constant_time_eq("abc", "abd") is False
    assert _constant_time_eq("abc", "abcd") is False


# ---------------------------------------------------------------------------
# /api/v1/keys admin gate via TestClient
# ---------------------------------------------------------------------------

def test_create_key_without_admin_token_returns_401(test_client):
    res = test_client.post("/api/v1/keys", json={"name": "Production"})
    assert res.status_code == 401
    body = res.json()
    assert body.get("code") == "MISSING_API_KEY"


def test_create_key_with_wrong_admin_token_returns_401(test_client, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "expected-token")
    res = test_client.post(
        "/api/v1/keys",
        json={"name": "Production"},
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert res.status_code == 401
