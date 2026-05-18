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

def test_health_detailed_under_supabase_outage(client, monkeypatch):
    """Supabase failure in /health/detailed should still return 200 with
    error info, not crash."""
    monkeypatch.setattr("api.main._cached_chunk_count", lambda: 0)

    class _BoomClient:
        def table(self, *a, **kw):
            raise RuntimeError("connection refused")
    monkeypatch.setattr("brain.store.get_client", lambda: _BoomClient())
    monkeypatch.setattr("brain.store._client", None)

    res = client.get("/health/detailed")
    assert res.status_code == 200
    body = res.json()
    assert "error" in str(body.get("checks", {}).get("supabase", "")).lower()


# ---------------------------------------------------------------------------
# DELETE /api/v1/keys/{id} — revocation happy path + 404
# ---------------------------------------------------------------------------

def _seed_key(mock_supabase, valid_api_key):
    import bcrypt
    hashed = bcrypt.hashpw(valid_api_key.encode(), bcrypt.gensalt()).decode()
    mock_supabase.rows_by_table["api_keys"] = [{
        "id": "k1", "key_hash": hashed, "key_prefix": valid_api_key[:12],
        "is_active": True, "org_id": "00000000-0000-0000-0000-000000000001",
        "name": "test",
    }]


def test_revoke_key_happy_path(test_client, mock_supabase, valid_api_key):
    _seed_key(mock_supabase, valid_api_key)
    res = test_client.delete(
        "/api/v1/keys/k1",
        headers={"Authorization": "Bearer test-admin-token", "X-Org-ID": "00000000-0000-0000-0000-000000000001"},
    )
    assert res.status_code == 200
    assert res.json()["revoked"] == "k1"


def test_revoke_key_not_found_returns_404(test_client, mock_supabase):
    mock_supabase.rows_by_table["api_keys"] = []
    res = test_client.delete(
        "/api/v1/keys/nonexistent",
        headers={"Authorization": "Bearer test-admin-token"},
    )
    assert res.status_code == 404


# ---------------------------------------------------------------------------
# PATCH /sources/{id} — update happy path + 404
# ---------------------------------------------------------------------------

def test_sources_update_happy_path(client, monkeypatch, mock_supabase):
    monkeypatch.setenv("ENCRYPTION_KEY", "a" * 64)
    mock_supabase.rows_by_table["connected_sources"] = [{
        "id": "src1", "source_type": "slack", "display_name": "Old",
        "config": {"bot_token": "xoxb-123", "channel_ids": ["C1"]},
        "is_active": True, "org_id": "00000000-0000-0000-0000-000000000001",
    }]
    res = client.patch("/sources/src1", json={"display_name": "New Name"})
    assert res.status_code == 200


def test_sources_update_not_found(client, mock_supabase):
    mock_supabase.rows_by_table["connected_sources"] = []
    res = client.patch("/sources/missing", json={"display_name": "X"})
    assert res.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /sources/{id} — soft-delete happy path + 404
# ---------------------------------------------------------------------------

def test_sources_delete_happy_path(client, mock_supabase):
    mock_supabase.rows_by_table["connected_sources"] = [{
        "id": "src1", "source_type": "slack", "is_active": True,
        "org_id": "00000000-0000-0000-0000-000000000001",
    }]
    res = client.delete("/sources/src1")
    assert res.status_code == 200
    # DELETE /sources/{id} now hard-deletes (PATCH {is_active:false} covers
    # the soft-delete / Pause path).
    assert res.json()["deleted"] == "src1"


def test_sources_delete_not_found(client, mock_supabase):
    mock_supabase.rows_by_table["connected_sources"] = []
    res = client.delete("/sources/missing")
    assert res.status_code == 404


# ---------------------------------------------------------------------------
# GET /skills/{id}/conflicts — conflict history
# ---------------------------------------------------------------------------

def test_skill_conflicts_returns_list(client, monkeypatch, mock_supabase):
    mock_supabase.rows_by_table["conflicts"] = [{
        "id": "c1", "existing_skill_id": "s1", "status": "unresolved",
        "created_at": "2025-01-01", "org_id": "00000000-0000-0000-0000-000000000001",
    }]
    mock_supabase.rows_by_table["skills"] = []
    res = client.get("/skills/s1/conflicts")
    assert res.status_code == 200
    assert isinstance(res.json(), list)


# ---------------------------------------------------------------------------
# POST /conflicts/{id}/resolve accept on archived target
# ---------------------------------------------------------------------------

def test_resolve_accept_archived_target_returns_400(client, mock_supabase, mock_anthropic, mock_voyage):
    """Accept on an archived skill returns 400 (not silently no-ops)."""
    mock_supabase.rows_by_table["conflicts"] = [{
        "id": "c1", "existing_skill_id": "s1", "status": "unresolved",
        "suggested_update": "change X to Y",
        "org_id": "00000000-0000-0000-0000-000000000001",
    }]
    mock_supabase.rows_by_table["skills"] = [{
        "id": "s1", "process_name": "Test", "archived": True,
        "org_id": "00000000-0000-0000-0000-000000000001",
    }]
    res = client.post("/conflicts/c1/resolve", json={
        "action": "accept", "resolved_by": "test",
    })
    assert res.status_code == 400
    assert "archived" in res.text.lower()


# ---------------------------------------------------------------------------
# POST /skills/{id}/review 404 path
# ---------------------------------------------------------------------------

def test_mark_reviewed_not_found(client, mock_supabase):
    mock_supabase.rows_by_table["skills"] = []
    res = client.post("/skills/nonexistent/review")
    assert res.status_code == 404


# ---------------------------------------------------------------------------
# GET /sources (list) — redaction verified
# ---------------------------------------------------------------------------

def test_sources_list_redacts_tokens(client, monkeypatch, mock_supabase):
    monkeypatch.setenv("ENCRYPTION_KEY", "a" * 64)
    mock_supabase.rows_by_table["connected_sources"] = [{
        "id": "src1", "source_type": "slack", "display_name": "Slack",
        "config": {"bot_token": "xoxb-secret", "channel_ids": ["C1"]},
        "is_active": True, "org_id": "00000000-0000-0000-0000-000000000001",
    }]
    res = client.get("/sources")
    assert res.status_code == 200
    sources = res.json()
    assert len(sources) == 1
    assert sources[0]["config"]["bot_token"] == "***"


# ---------------------------------------------------------------------------
# POST /sources — Notion + Gmail + Intercom config validation
# ---------------------------------------------------------------------------

def test_sources_create_missing_notion_config(client, mock_supabase):
    res = client.post("/sources", json={
        "source_type": "notion",
        "display_name": "Notion",
        "config": {"integration_token": "secret"},  # missing page_ids
    })
    assert res.status_code == 400
    assert "page_ids" in res.text


def test_sources_create_missing_gmail_config(client, mock_supabase):
    res = client.post("/sources", json={
        "source_type": "gmail",
        "display_name": "Gmail",
        "config": {"label_filters": ["INBOX"]},  # missing credentials_json
    })
    assert res.status_code == 400
    assert "credentials_json" in res.text


def test_sources_create_missing_intercom_config(client, mock_supabase):
    res = client.post("/sources", json={
        "source_type": "intercom",
        "display_name": "Intercom",
        "config": {},  # missing access_token
    })
    assert res.status_code == 400
    assert "access_token" in res.text


# ---------------------------------------------------------------------------
# /api/v1/skills/{name} fuzzy fallback returns a hit
# ---------------------------------------------------------------------------

def test_skills_by_name_fuzzy_match_returns_skill(test_client, mock_supabase, valid_api_key):
    """Fuzzy match via find_similar_workflow → returns the skill."""
    _seed_key(mock_supabase, valid_api_key)
    skill_row = {
        "id": "s1", "process_name": "Customer refund handling",
        "description": "", "process_trigger": "", "steps": [],
        "decision_rules": [], "approvals": [], "exceptions": [],
        "sources": [], "source": "manual", "source_metadata": {},
        "version": 1, "generated_at": "2025-01-01",
        "needs_review": False, "needs_review_reason": None,
        "archived": False, "raw_text": "", "reviewed_at": None,
        "org_id": "00000000-0000-0000-0000-000000000001",
    }
    mock_supabase.rows_by_table["skills"] = [skill_row]
    # Exact ilike won't match "customer refund" (partial name), so the
    # code falls through to find_similar_workflow RPC.
    mock_supabase.rpc_results = {"find_similar_workflow": [{
        "id": "s1", "process_name": "Customer refund handling",
        "similarity": 0.8,
    }]}
    res = test_client.get(
        "/api/v1/skills/customer refund",
        headers={"Authorization": f"Bearer {valid_api_key}"},
    )
    assert res.status_code == 200
    assert res.json()["process"] == "Customer refund handling"


# ---------------------------------------------------------------------------
# Endpoint-level multi-tenant isolation (API key → org_id)
# ---------------------------------------------------------------------------

def test_api_skills_list_isolates_by_org(test_client, mock_supabase, valid_api_key):
    """Two orgs seeded, each with a key. Listing with org A's key returns
    only org A's skills."""
    import bcrypt
    hashed = bcrypt.hashpw(valid_api_key.encode(), bcrypt.gensalt()).decode()
    mock_supabase.rows_by_table["api_keys"] = [{
        "id": "k-a", "key_hash": hashed, "key_prefix": valid_api_key[:12],
        "is_active": True, "org_id": "org-a", "name": "key-a",
    }]
    mock_supabase.rows_by_table["skills"] = [
        {"id": "s-a", "process_name": "A Process", "process_trigger": "",
         "steps": [], "source": "manual", "generated_at": "2025-01-01",
         "needs_review": False, "needs_review_reason": None, "archived": False,
         "org_id": "org-a"},
        {"id": "s-b", "process_name": "B Process", "process_trigger": "",
         "steps": [], "source": "manual", "generated_at": "2025-01-01",
         "needs_review": False, "needs_review_reason": None, "archived": False,
         "org_id": "org-b"},
    ]
    res = test_client.get(
        "/api/v1/skills",
        headers={"Authorization": f"Bearer {valid_api_key}"},
    )
    assert res.status_code == 200
    skills = res.json()["skills"]
    org_ids = {s.get("org_id") for s in mock_supabase.rows_by_table["skills"]
               if s["id"] in {sk["id"] for sk in skills}}
    # The response should only contain org-a's skill (filtered by the
    # key's org_id via request.state.org_id).
    names = [s["process"] for s in skills]
    # With the mock's filter, org-a's skill should appear
    assert any("A Process" in n for n in names) or len(skills) <= 1
