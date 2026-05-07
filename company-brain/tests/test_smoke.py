"""Smoke tests — confirm every top-level module imports cleanly."""


def test_brain_modules_import():
    import brain.anthropic_client  # noqa: F401
    import brain.chunker  # noqa: F401
    import brain.drift  # noqa: F401
    import brain.embedder  # noqa: F401
    import brain.ingestors  # noqa: F401
    import brain.logger  # noqa: F401
    import brain.query  # noqa: F401
    import brain.staleness  # noqa: F401
    import brain.store  # noqa: F401
    import brain.text_utils  # noqa: F401


def test_api_module_imports():
    from api.main import app
    assert app is not None
    # FastAPI routes registered as expected
    paths = {route.path for route in app.routes}  # type: ignore[attr-defined]
    for expected in {
        "/health",
        "/history",
        "/query",
        "/skills",
        "/workflows/generate",
        "/workflows/similar",
        "/workflows/{workflow_id}",
        "/workflows/{workflow_id}/archive",
        "/demo/{slug}",
    }:
        assert expected in paths, f"missing route: {expected}"


def test_ingest_modules_import():
    import ingest.ingest_github  # noqa: F401
    import ingest.ingest_notion  # noqa: F401
    import ingest.ingest_slack  # noqa: F401
    # google + intercom imports are lazy inside methods, so the modules
    # themselves import cleanly even without those packages installed.
    import ingest.ingest_gmail  # noqa: F401
    import ingest.ingest_intercom  # noqa: F401
    # ingest_pdfs is allowed to be orphaned (no demo data); just verify import
    import ingest.ingest_pdfs  # noqa: F401


def test_slack_modules_import():
    import pytest

    pytest.importorskip("slack_bolt")
    import slack.app  # noqa: F401
    import slack.formatter  # noqa: F401
    import slack.handlers  # noqa: F401
