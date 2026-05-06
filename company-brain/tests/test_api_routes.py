"""api.main — FastAPI endpoints under TestClient with monkeypatched store/Claude.

These tests don't talk to Supabase, Anthropic, or Voyage. Each test
patches the function the endpoint delegates to.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    # Import inside the fixture so any module-level state inits per-test.
    from api.main import app
    return TestClient(app)


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------

def test_health_returns_status_and_chunk_count(client, monkeypatch):
    monkeypatch.setattr("api.main._cached_chunk_count", lambda: 42)
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok", "chunks_indexed": 42}


# ---------------------------------------------------------------------------
# /history
# ---------------------------------------------------------------------------

def test_history_returns_workflow_list(client, monkeypatch):
    sample = [{"id": "a", "process": "X"}, {"id": "b", "process": "Y"}]
    monkeypatch.setattr("api.main.list_workflows", lambda limit=5: sample)
    res = client.get("/history")
    assert res.status_code == 200
    assert res.json() == sample


def test_history_passes_limit_param(client, monkeypatch):
    captured = {}

    def fake_list(limit=5):
        captured["limit"] = limit
        return []

    monkeypatch.setattr("api.main.list_workflows", fake_list)
    client.get("/history?limit=12")
    assert captured["limit"] == 12


def test_clear_history_calls_clear_workflows(client, monkeypatch):
    monkeypatch.setattr("api.main.clear_workflows", lambda: 7)
    res = client.delete("/history")
    assert res.status_code == 200
    assert res.json() == {"status": "ok", "cleared": 7}


# ---------------------------------------------------------------------------
# /workflows/generate
# ---------------------------------------------------------------------------

def test_workflows_generate_passes_through_args(client, monkeypatch):
    captured = {}

    def fake_generate(name, content, source=None, source_metadata=None):
        captured.update(
            {
                "name": name,
                "content": content,
                "source": source,
                "source_metadata": source_metadata,
            }
        )
        return {
            "id": "abc",
            "process": name,
            "description": "",
            "trigger": "",
            "steps": [],
            "decision_rules": [],
            "approvals": [],
            "exceptions": [],
            "sources": [],
        }

    monkeypatch.setattr("api.main.generate_workflow_from_text", fake_generate)
    res = client.post(
        "/workflows/generate",
        json={
            "name": "Refund flow",
            "content": "raw paste",
            "source": "slack",
            "source_metadata": {"channel": "cs"},
        },
    )
    assert res.status_code == 200
    assert captured["name"] == "Refund flow"
    assert captured["content"] == "raw paste"
    assert captured["source"] == "slack"
    assert captured["source_metadata"] == {"channel": "cs"}


def test_workflows_generate_defaults_optional_fields(client, monkeypatch):
    captured = {}

    def fake_generate(name, content, source=None, source_metadata=None):
        captured["source"] = source
        captured["source_metadata"] = source_metadata
        return {
            "id": "x", "process": name, "description": "", "trigger": "",
            "steps": [], "decision_rules": [], "approvals": [], "exceptions": [], "sources": [],
        }

    monkeypatch.setattr("api.main.generate_workflow_from_text", fake_generate)
    client.post("/workflows/generate", json={"name": "X", "content": "Y"})
    assert captured["source"] is None
    assert captured["source_metadata"] is None


# ---------------------------------------------------------------------------
# /workflows/{id} + /workflows/similar + /workflows/{id}/archive
# ---------------------------------------------------------------------------

def test_workflows_similar_returns_match(client, monkeypatch):
    monkeypatch.setattr(
        "api.main.find_similar_workflow",
        lambda name, threshold=0.4, exclude_id=None: {"id": "1", "process": "Refund"},
    )
    res = client.get("/workflows/similar", params={"name": "Refunds"})
    assert res.status_code == 200
    assert res.json()["id"] == "1"


def test_workflows_similar_returns_null_when_no_match(client, monkeypatch):
    monkeypatch.setattr(
        "api.main.find_similar_workflow",
        lambda name, threshold=0.4, exclude_id=None: None,
    )
    res = client.get("/workflows/similar", params={"name": "Nothing"})
    assert res.status_code == 200
    assert res.json() is None


def test_workflows_get_returns_404_when_missing(client, monkeypatch):
    monkeypatch.setattr("api.main.get_workflow", lambda wid: None)
    res = client.get("/workflows/does-not-exist")
    assert res.status_code == 404


def test_workflows_get_returns_workflow(client, monkeypatch):
    monkeypatch.setattr(
        "api.main.get_workflow",
        lambda wid: {"id": wid, "process": "X"},
    )
    res = client.get("/workflows/abc-123")
    assert res.status_code == 200
    assert res.json() == {"id": "abc-123", "process": "X"}


def test_route_order_similar_takes_priority_over_id_match(client, monkeypatch):
    """Regression: /workflows/similar must NOT be greedy-matched as id="similar"."""
    # If get_workflow is called with "similar", the route ordering broke.
    monkeypatch.setattr(
        "api.main.get_workflow",
        lambda wid: pytest.fail(f"should not be called; got id={wid!r}"),
    )
    monkeypatch.setattr(
        "api.main.find_similar_workflow",
        lambda name, threshold=0.4, exclude_id=None: None,
    )
    res = client.get("/workflows/similar", params={"name": "X"})
    assert res.status_code == 200


def test_workflows_archive_404_on_missing(client, monkeypatch):
    monkeypatch.setattr("api.main.archive_workflow", lambda wid: False)
    res = client.post("/workflows/abc/archive")
    assert res.status_code == 404


def test_workflows_archive_success(client, monkeypatch):
    monkeypatch.setattr("api.main.archive_workflow", lambda wid: True)
    res = client.post("/workflows/abc/archive")
    assert res.status_code == 200
    assert res.json() == {"status": "ok", "archived": "abc"}


# ---------------------------------------------------------------------------
# /demo/{slug}
# ---------------------------------------------------------------------------

def test_demo_returns_file_contents(client, tmp_path, monkeypatch):
    fake = tmp_path / "fake_doc.txt"
    fake.write_text("hello demo content", encoding="utf-8")
    monkeypatch.setattr("api.main.DEMO_DIR", tmp_path)
    res = client.get("/demo/fake_doc")
    assert res.status_code == 200
    assert res.text == "hello demo content"


def test_demo_404_when_slug_missing(client, tmp_path, monkeypatch):
    monkeypatch.setattr("api.main.DEMO_DIR", tmp_path)
    res = client.get("/demo/no-such-file")
    assert res.status_code == 404


def test_demo_function_rejects_path_traversal(monkeypatch, tmp_path):
    """Test the handler directly — Starlette normalizes ".." out of the URL
    before it reaches us, so we have to invoke the function to verify the
    defense actually fires."""
    from fastapi import HTTPException
    from api.main import get_demo

    monkeypatch.setattr("api.main.DEMO_DIR", tmp_path)
    for bad in ("..", "../etc/passwd", "foo/bar", r"foo\bar", "", "   "):
        with pytest.raises(HTTPException) as exc:
            get_demo(bad)
        assert exc.value.status_code == 400, f"slug {bad!r} should 400"


# ---------------------------------------------------------------------------
# /skills (RAG path) — just confirm the endpoint wires up cleanly.
# ---------------------------------------------------------------------------

def test_skills_endpoint_calls_through(client, monkeypatch):
    fake_response = {
        "process": "Refund",
        "trigger": "customer asks",
        "steps": [],
        "decision_rules": [],
        "approvals": [],
        "exceptions": [],
        "sources_summary": "From the Notion refund policy.",
    }
    monkeypatch.setattr("api.main.generate_skills_file", lambda name: fake_response)
    res = client.post("/skills", json={"process_name": "Refund"})
    assert res.status_code == 200
    body = res.json()
    assert body["process"] == "Refund"
    assert body["sources_summary"].startswith("From the Notion")


# ---------------------------------------------------------------------------
# /query (RAG path)
# ---------------------------------------------------------------------------

def test_query_endpoint_calls_through(client, monkeypatch):
    monkeypatch.setattr(
        "api.main.query_brain",
        lambda question, top_k=6: {
            "answer": f"You asked: {question}",
            "sources": [],
            "confidence": "low",
        },
    )
    res = client.post("/query", json={"question": "How do refunds work?"})
    assert res.status_code == 200
    assert res.json()["answer"] == "You asked: How do refunds work?"


def test_query_passes_top_k(client, monkeypatch):
    captured = {}

    def fake(question, top_k=6):
        captured["top_k"] = top_k
        return {"answer": "", "sources": [], "confidence": "low"}

    monkeypatch.setattr("api.main.query_brain", fake)
    client.post("/query", json={"question": "X", "top_k": 3})
    assert captured["top_k"] == 3
