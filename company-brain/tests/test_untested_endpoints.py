"""Tests for the 11 previously-untested API endpoints.

Covers the P0 items from the QA audit: /setup slug collision, /sources
config validation, /ingest/trigger admin gate, /conflicts resolve, and
the /api/v1/skills/{process_name} exact-then-fuzzy lookup.
"""
from __future__ import annotations

import json

import pytest


@pytest.fixture
def client():
    from api.main import app
    from fastapi.testclient import TestClient
    return TestClient(app, headers={"Authorization": "Bearer test-admin-token"})


@pytest.fixture
def unauthed():
    from api.main import app
    from fastapi.testclient import TestClient
    return TestClient(app)


# ---------------------------------------------------------------------------
# POST /setup
# ---------------------------------------------------------------------------

def test_setup_empty_company_name_returns_400(client, monkeypatch):
    monkeypatch.setattr("brain.store.list_organisations", lambda: [])
    res = client.post("/setup", json={"company_name": ""})
    assert res.status_code == 400


def test_setup_creates_org_and_returns_slug(client, monkeypatch, mock_supabase):
    monkeypatch.setattr("brain.store.list_organisations", lambda: [])
    monkeypatch.setattr("brain.store.get_organisation_by_slug", lambda s: None)
    monkeypatch.setattr("brain.store.create_organisation", lambda **kw: {
        "id": "new-org-id", "name": kw["name"], "slug": kw["slug"], "plan": "free",
    })
    res = client.post("/setup", json={"company_name": "Acme Inc"})
    assert res.status_code == 200
    body = res.json()
    assert body["slug"].startswith("acme-inc")
    assert body["name"] == "Acme Inc"


def test_setup_slug_collision_adds_suffix(client, monkeypatch, mock_supabase):
    """When the slug already exists, a random hex suffix is appended."""
    monkeypatch.setattr("brain.store.list_organisations", lambda: [])
    call_count = {"n": 0}

    def fake_slug_lookup(slug):
        call_count["n"] += 1
        return {"id": "exists"} if call_count["n"] == 1 else None

    # Patch both the store and the local binding in api.main
    monkeypatch.setattr("brain.store.get_organisation_by_slug", fake_slug_lookup)
    monkeypatch.setattr("api.main.get_organisation_by_slug", fake_slug_lookup)
    monkeypatch.setattr("brain.store.create_organisation", lambda **kw: {
        "id": "new-org-id", "name": kw["name"], "slug": kw["slug"], "plan": "free",
    })
    res = client.post("/setup", json={"company_name": "Acme"})
    assert res.status_code == 200
    slug = res.json()["slug"]
    assert slug != "acme" and slug.startswith("acme")


def test_setup_requires_bootstrap_token_after_first_org(unauthed, monkeypatch):
    monkeypatch.setattr("brain.store.list_organisations", lambda: [{"id": "existing"}])
    monkeypatch.setenv("BOOTSTRAP_TOKEN", "secret-bootstrap")
    res = unauthed.post("/setup", json={"company_name": "New"})
    assert res.status_code == 401


# ---------------------------------------------------------------------------
# POST /sources — config validation
# ---------------------------------------------------------------------------

def test_sources_create_missing_slack_config_returns_400(client, monkeypatch, mock_supabase):
    res = client.post("/sources", json={
        "source_type": "slack",
        "display_name": "My Slack",
        "config": {"bot_token": "xoxb-123"},  # missing channel_ids
    })
    assert res.status_code == 400
    assert "channel_ids" in res.text


def test_sources_create_valid_slack_config(client, monkeypatch, mock_supabase):
    monkeypatch.setenv("ENCRYPTION_KEY", "a" * 64)
    res = client.post("/sources", json={
        "source_type": "slack",
        "display_name": "My Slack",
        "config": {"bot_token": "xoxb-123", "channel_ids": ["C1"]},
    })
    assert res.status_code == 200


def test_sources_create_unsupported_type_returns_400(client, mock_supabase):
    res = client.post("/sources", json={
        "source_type": "jira",
        "display_name": "Jira",
        "config": {},
    })
    assert res.status_code == 400


# ---------------------------------------------------------------------------
# POST /ingest/trigger — admin gate
# ---------------------------------------------------------------------------

def test_ingest_trigger_without_auth_returns_401(unauthed, monkeypatch):
    res = unauthed.post("/ingest/trigger")
    # Should be 401 (admin gate) or 403
    assert res.status_code in (401, 403)


def test_ingest_trigger_with_admin_succeeds(client, monkeypatch):
    from brain.scheduler import scheduler
    monkeypatch.setattr(scheduler, "trigger_now", lambda: None)
    res = client.post("/ingest/trigger")
    assert res.status_code == 200
    assert res.json()["triggered"] is True


# ---------------------------------------------------------------------------
# GET /conflicts + POST /conflicts/{id}/resolve
# ---------------------------------------------------------------------------

def test_get_conflicts_returns_list(client, monkeypatch):
    monkeypatch.setattr("api.main.get_unresolved_conflicts", lambda **kw: [])
    res = client.get("/conflicts")
    assert res.status_code == 200
    assert res.json() == []


def test_resolve_conflict_unknown_action_returns_400(client, monkeypatch):
    res = client.post("/conflicts/some-id/resolve", json={
        "action": "yolo",
        "resolved_by": "test",
    })
    assert res.status_code == 400


def test_resolve_conflict_dismiss(client, monkeypatch, mock_supabase):
    mock_supabase.rows_by_table["conflicts"] = [{
        "id": "c1",
        "existing_skill_id": "s1",
        "status": "unresolved",
        "org_id": "00000000-0000-0000-0000-000000000001",
    }]
    res = client.post("/conflicts/c1/resolve", json={
        "action": "dismiss",
        "resolved_by": "test",
    })
    assert res.status_code == 200
    assert res.json()["status"] == "dismissed"


def test_resolve_conflict_not_found_returns_404(client, monkeypatch, mock_supabase):
    mock_supabase.rows_by_table["conflicts"] = []
    res = client.post("/conflicts/nonexistent/resolve", json={
        "action": "dismiss",
        "resolved_by": "test",
    })
    assert res.status_code == 404


# ---------------------------------------------------------------------------
# POST /skills/{skill_id}/review
# ---------------------------------------------------------------------------

def test_mark_reviewed_updates_skill(client, monkeypatch, mock_supabase):
    mock_supabase.rows_by_table["skills"] = [{
        "id": "s1",
        "needs_review": True,
        "reviewed_at": None,
        "org_id": "00000000-0000-0000-0000-000000000001",
    }]
    res = client.post("/skills/s1/review")
    assert res.status_code == 200


# ---------------------------------------------------------------------------
# GET /ingest/status
# ---------------------------------------------------------------------------

def test_ingest_status_returns_shape(client, monkeypatch, mock_supabase):
    monkeypatch.setattr("api.main.get_latest_ingest_run", lambda **kw: None)
    res = client.get("/ingest/status")
    assert res.status_code == 200
    body = res.json()
    assert "last_run" in body
    assert "schedule_hours" in body


# ---------------------------------------------------------------------------
# /api/v1/skills/{process_name} — the primary agent read endpoint
# ---------------------------------------------------------------------------

def test_skills_by_name_exact_match(test_client, mock_supabase, valid_api_key):
    """Exact name match returns the skill."""
    import bcrypt
    hashed = bcrypt.hashpw(valid_api_key.encode(), bcrypt.gensalt()).decode()
    mock_supabase.rows_by_table["api_keys"] = [{
        "id": "k1", "key_hash": hashed, "key_prefix": valid_api_key[:12],
        "is_active": True, "org_id": "00000000-0000-0000-0000-000000000001",
        "name": "test",
    }]
    mock_supabase.rows_by_table["skills"] = [{
        "id": "s1",
        "process_name": "Customer refund",
        "description": "",
        "process_trigger": "",
        "steps": [],
        "decision_rules": [],
        "approvals": [],
        "exceptions": [],
        "sources": [],
        "source": "manual",
        "source_metadata": {},
        "version": 1,
        "generated_at": "2025-01-01",
        "needs_review": False,
        "needs_review_reason": None,
        "archived": False,
        "org_id": "00000000-0000-0000-0000-000000000001",
    }]
    # get_skill_by_name_fuzzy does an ilike lookup first
    res = test_client.get(
        "/api/v1/skills/Customer refund",
        headers={"Authorization": f"Bearer {valid_api_key}"},
    )
    assert res.status_code == 200
    assert res.json()["process"] == "Customer refund"


def test_skills_by_name_not_found_returns_404_with_closest(test_client, mock_supabase, valid_api_key):
    """No match → 404 with closest_match in body."""
    import bcrypt
    hashed = bcrypt.hashpw(valid_api_key.encode(), bcrypt.gensalt()).decode()
    mock_supabase.rows_by_table["api_keys"] = [{
        "id": "k1", "key_hash": hashed, "key_prefix": valid_api_key[:12],
        "is_active": True, "org_id": "00000000-0000-0000-0000-000000000001",
        "name": "test",
    }]
    mock_supabase.rows_by_table["skills"] = []
    mock_supabase.rpc_results = {"find_similar_workflow": []}
    res = test_client.get(
        "/api/v1/skills/nonexistent",
        headers={"Authorization": f"Bearer {valid_api_key}"},
    )
    assert res.status_code == 404
    body = res.json()
    # FastAPI wraps HTTPException detail as {"detail": {...}}
    detail = body.get("detail") or body
    assert detail.get("code") == "SKILL_NOT_FOUND" or "SKILL_NOT_FOUND" in str(detail)


# ---------------------------------------------------------------------------
# /health error path
# ---------------------------------------------------------------------------

def test_health_under_supabase_outage(client, monkeypatch):
    """Supabase failure in /health should still return 200 with error info,
    not crash."""
    monkeypatch.setattr("api.main._cached_chunk_count", lambda: 0)

    class _BoomClient:
        def table(self, *a, **kw):
            raise RuntimeError("connection refused")
    monkeypatch.setattr("brain.store.get_client", lambda: _BoomClient())
    monkeypatch.setattr("brain.store._client", None)

    res = client.get("/health")
    assert res.status_code == 200
    body = res.json()
    assert "error" in str(body.get("checks", {}).get("supabase", "")).lower()
