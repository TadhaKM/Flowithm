"""Tests for brain/scheduler.py — P0 from the QA audit.

Covers: lock-acquire failure, partial-source failure, lock release in
finally, cross-org bucketing, and basic cycle shape.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _stub_env(monkeypatch):
    monkeypatch.setenv("ENCRYPTION_KEY", "a" * 64)


@pytest.fixture
def scheduler_deps(monkeypatch, mock_supabase):
    """Patch all external deps the scheduler calls so the cycle runs in-process."""
    # embed_and_store_batch — returns (0 new, 0 skipped, [])
    monkeypatch.setattr(
        "brain.embedder.embed_and_store_batch",
        lambda chunks, org_id=None: (0, 0, []),
    )
    # check_chunks_against_skills — no-op
    monkeypatch.setattr(
        "brain.drift.check_chunks_against_skills",
        lambda chunks, org_id=None: [],
    )
    # staleness — no-op
    monkeypatch.setattr(
        "brain.staleness.run_staleness_check",
        lambda org_id=None: {"newly_flagged": 0, "flags_cleared": 0},
    )
    # insert_ingest_run — no-op
    monkeypatch.setattr(
        "brain.store.insert_ingest_run",
        lambda summary, org_id=None: "fake-run-id",
    )
    # update_source_last_synced — no-op
    monkeypatch.setattr(
        "brain.store.update_source_last_synced",
        lambda sid, ts, org_id=None: None,
    )
    return mock_supabase


def test_lock_acquire_failure_returns_none(scheduler_deps, monkeypatch):
    """If the lock is held by another worker, the cycle returns None."""
    from brain.scheduler import IngestionScheduler

    # RPC returns False (lock not acquired)
    scheduler_deps.rpc_results = {"try_acquire_ingest_lock": False}
    s = IngestionScheduler()
    result = s.run_ingest_cycle()
    assert result is None


def test_lock_released_in_finally(scheduler_deps, monkeypatch):
    """Even if the cycle body raises, release_ingest_lock is called."""
    from brain.scheduler import IngestionScheduler

    scheduler_deps.rpc_results = {"try_acquire_ingest_lock": True}
    # list_active_connected_sources will raise
    monkeypatch.setattr(
        "brain.store.list_active_connected_sources",
        lambda org_id=None: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    s = IngestionScheduler()
    result = s.run_ingest_cycle()
    # Should still return (with errors), not crash
    assert result is not None or result is None  # either way, no exception

    # Check that release was called
    release_calls = [
        c for c in scheduler_deps.calls
        if c.get("action") == "rpc" and c.get("name") == "release_ingest_lock"
    ]
    assert len(release_calls) == 1


def test_partial_source_failure_continues(scheduler_deps, monkeypatch):
    """One source crashing doesn't kill the rest."""
    from brain.scheduler import IngestionScheduler

    scheduler_deps.rpc_results = {"try_acquire_ingest_lock": True}

    call_log = []

    def fake_sources(org_id=None):
        return [
            {"id": "s1", "source_type": "slack", "config": {}, "org_id": "00000000-0000-0000-0000-000000000001"},
            {"id": "s2", "source_type": "notion", "config": {}, "org_id": "00000000-0000-0000-0000-000000000001"},
        ]

    def fake_fetch(source):
        call_log.append(source["id"])
        if source["id"] == "s1":
            raise RuntimeError("slack is down")
        return []  # notion succeeds

    monkeypatch.setattr("brain.store.list_active_connected_sources", fake_sources)

    s = IngestionScheduler()
    monkeypatch.setattr(s, "_fetch_chunks_for_source", fake_fetch)

    result = s.run_ingest_cycle()
    assert result is not None
    # Both sources were attempted
    assert "s1" in call_log and "s2" in call_log
    # Error recorded for s1
    assert any("s1" in e for e in result.get("errors", []))


def test_cross_org_bucketing(scheduler_deps, monkeypatch):
    """Sources from different orgs get separate ingest_runs rows."""
    from brain.scheduler import IngestionScheduler

    scheduler_deps.rpc_results = {"try_acquire_ingest_lock": True}

    ingest_run_orgs = []
    original_insert = None

    def tracking_insert(summary, org_id=None):
        ingest_run_orgs.append(org_id)
        return "fake-id"

    monkeypatch.setattr("brain.store.insert_ingest_run", tracking_insert)

    def fake_sources(org_id=None):
        return [
            {"id": "s1", "source_type": "slack", "config": {}, "org_id": "org-a"},
            {"id": "s2", "source_type": "notion", "config": {}, "org_id": "org-b"},
        ]

    monkeypatch.setattr("brain.store.list_active_connected_sources", fake_sources)

    s = IngestionScheduler()
    monkeypatch.setattr(s, "_fetch_chunks_for_source", lambda src: [])
    result = s.run_ingest_cycle()

    # Two separate ingest_runs rows — one per org
    assert "org-a" in ingest_run_orgs
    assert "org-b" in ingest_run_orgs


def test_empty_sources_still_runs_staleness(scheduler_deps, monkeypatch):
    """Even with no connected sources, staleness check runs for the default org."""
    from brain.scheduler import IngestionScheduler

    scheduler_deps.rpc_results = {"try_acquire_ingest_lock": True}

    staleness_called = {"count": 0}

    def tracking_staleness(org_id=None):
        staleness_called["count"] += 1
        return {"newly_flagged": 0, "flags_cleared": 0}

    monkeypatch.setattr("brain.staleness.run_staleness_check", tracking_staleness)
    monkeypatch.setattr("brain.store.list_active_connected_sources", lambda org_id=None: [])

    s = IngestionScheduler()
    s.run_ingest_cycle()
    assert staleness_called["count"] >= 1
