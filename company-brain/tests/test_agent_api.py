"""Public agent API at /api/v1/* — admin gate, key list shape, skill listing.

Auth is exercised through the TestClient (cuts through the real
FastAPI dependency wiring rather than calling verify_api_key directly).
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# /api/v1/keys (admin gate)
# ---------------------------------------------------------------------------

def test_list_keys_without_admin_token_returns_401(unauthed_client):
    res = unauthed_client.get("/api/v1/keys")
    assert res.status_code == 401
    assert res.json().get("code") == "MISSING_API_KEY"


def test_revoke_key_without_admin_token_returns_401(unauthed_client):
    res = unauthed_client.delete("/api/v1/keys/some-id")
    assert res.status_code == 401


def test_create_key_admin_returns_plaintext_once(test_client, monkeypatch, mock_supabase):
    """Admin POST mints a key and returns the plaintext exactly once."""
    monkeypatch.setenv("ADMIN_TOKEN", "admin-test")
    res = test_client.post(
        "/api/v1/keys",
        json={"name": "Smoke test"},
        headers={"Authorization": "Bearer admin-test"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["name"] == "Smoke test"
    assert body["key"].startswith("fb_live_")
    assert body["prefix"] == body["key"][:12]


# ---------------------------------------------------------------------------
# /api/v1/skills/* (Bearer auth)
# ---------------------------------------------------------------------------

def test_list_skills_without_auth_returns_401(unauthed_client):
    res = unauthed_client.get("/api/v1/skills")
    assert res.status_code == 401


def _seed_active_key(mock_supabase, plaintext_key: str) -> dict:
    """Insert an api_keys row whose key_hash will bcrypt-verify
    against `plaintext_key`. Returns the row."""
    import bcrypt
    hashed = bcrypt.hashpw(plaintext_key.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    row = {
        "id": "key-row-1",
        "name": "Smoke test",
        "key_hash": hashed,
        "key_prefix": plaintext_key[:12],
        "is_active": True,
        "org_id": "00000000-0000-0000-0000-000000000001",
    }
    mock_supabase.rows_by_table["api_keys"] = [row]
    return row


def test_list_skills_with_valid_key(test_client, mock_supabase, valid_api_key):
    """Valid Bearer + non-empty skills index → 200 with the index shape."""
    _seed_active_key(mock_supabase, valid_api_key)
    mock_supabase.rows_by_table["skills"] = [{
        "id": "s-list-1",
        "process_name": "Refunds",
        "process_trigger": "Customer requests refund",
        "steps": [{"step": 1, "action": "verify"}],
        "source": "manual",
        "generated_at": "2026-05-01T00:00:00+00:00",
        "needs_review": False,
        "needs_review_reason": None,
        "archived": False,
        "org_id": "00000000-0000-0000-0000-000000000001",
    }]

    res = test_client.get(
        "/api/v1/skills?limit=10",
        headers={"Authorization": f"Bearer {valid_api_key}"},
    )
    assert res.status_code == 200
    body = res.json()
    assert "skills" in body
    assert "total" in body
    if body["skills"]:
        row = body["skills"][0]
        assert "id" in row
        assert "process" in row
        assert "step_count" in row
        assert "needs_review" in row


def test_invalid_key_returns_401(test_client, mock_supabase):
    """Bearer that doesn't match any prefix → 401."""
    mock_supabase.rows_by_table["api_keys"] = []  # no keys exist
    res = test_client.get(
        "/api/v1/skills",
        headers={"Authorization": "Bearer fb_live_invalid_token_value"},
    )
    assert res.status_code == 401
    assert res.json().get("code") == "INVALID_API_KEY"


def test_revoked_key_returns_401(test_client, mock_supabase, valid_api_key):
    """Bearer matches but is_active=False → 401. Pre-filtered by the DB
    query (P-14) so the response is INVALID_API_KEY, not REVOKED — no
    information leakage about key existence."""
    row = _seed_active_key(mock_supabase, valid_api_key)
    row["is_active"] = False
    res = test_client.get(
        "/api/v1/skills",
        headers={"Authorization": f"Bearer {valid_api_key}"},
    )
    assert res.status_code == 401
    assert res.json().get("code") == "INVALID_API_KEY"


def test_match_no_skills_returns_404(test_client, mock_supabase, mock_voyage, valid_api_key):
    """No skills → 404 with empty suggestions."""
    _seed_active_key(mock_supabase, valid_api_key)
    mock_supabase.rpc_results = {"match_skills": []}

    res = test_client.get(
        "/api/v1/skills/match?q=customer+refund",
        headers={"Authorization": f"Bearer {valid_api_key}"},
    )
    assert res.status_code == 404
    body = res.json()
    assert body.get("code") == "SKILL_NOT_FOUND"


def test_execute_persists_outcome(test_client, mock_supabase, valid_api_key):
    """POST /skills/execute writes one row into executions."""
    _seed_active_key(mock_supabase, valid_api_key)
    # The endpoint now verifies the skill belongs to the key's org before
    # recording an execution, so seed a matching skill row.
    mock_supabase.rows_by_table["skills"] = [{
        "id": "skill-99",
        "process_name": "Test process",
        "org_id": "00000000-0000-0000-0000-000000000001",
    }]

    payload = {
        "skill_id": "skill-99",
        "step_number": 2,
        "outcome": "completed",
        "duration_seconds": 4,
    }
    res = test_client.post(
        "/api/v1/skills/execute",
        json=payload,
        headers={"Authorization": f"Bearer {valid_api_key}"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body.get("received") is True

    insert_calls = [
        c for c in mock_supabase.calls
        if c.get("table") == "executions" and c.get("action") == "insert"
    ]
    assert len(insert_calls) == 1
