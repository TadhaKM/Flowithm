"""brain.drift — check_for_drift, resolve_conflict, hydration helpers.

Drift is the most heavily-mocked module in the codebase: it touches
Voyage, Anthropic, and Supabase in one cycle. We rely on
mock_voyage / mock_anthropic / mock_supabase from conftest to keep
every call deterministic and offline.
"""
from __future__ import annotations

import json

import pytest


# ---------------------------------------------------------------------------
# check_for_drift — detection + persistence
# ---------------------------------------------------------------------------

def test_no_skills_returns_empty(mock_supabase, mock_voyage, mock_anthropic):
    """Cold-start: no existing skills → no possible conflict, no Claude call."""
    from brain.drift import check_for_drift

    mock_supabase.rows_by_table["skills"] = []
    out = check_for_drift("new content", {"id": "new-1", "process": "Refunds"})
    assert out == []
    assert mock_anthropic["calls"] == []


def test_detects_conflict(mock_supabase, mock_voyage, mock_anthropic, sample_skill):
    """Claude returns one contradiction → it lands in the conflicts table."""
    from brain.drift import check_for_drift

    existing_skill_id = "fixture-existing-1"
    mock_supabase.rows_by_table["skills"] = [{
        "id": existing_skill_id,
        "process_name": "Customer refund handling",
        "description": "",
        "process_trigger": "Customer requests refund",
        "steps": sample_skill["steps"],
        "decision_rules": sample_skill["decision_rules"],
        "approvals": sample_skill["approvals"],
        "exceptions": sample_skill["exceptions"],
        "sources": [],
        "summary_embedding": [0.1] * 1024,
        "archived": False,
        "org_id": "00000000-0000-0000-0000-000000000001",
    }]
    mock_anthropic["response_text"] = json.dumps({
        "conflicts": [{
            "existing_skill_id": existing_skill_id,
            "existing_process_name": "Customer refund handling",
            "conflict_type": "contradiction",
            "conflict_description": "30 -> 60 day window",
            "existing_rule": "30 days",
            "new_evidence": "60 days for all customers",
            "suggested_update": "Update to 60 days",
            "severity": "high",
        }],
    })

    new_skill = {"id": "new-2", "process": "Customer refund handling (updated)"}
    inserted = check_for_drift("60 days for all", new_skill)

    assert len(inserted) == 1
    inserted_row = inserted[0]
    assert inserted_row["existing_skill_id"] == existing_skill_id
    assert inserted_row["conflict_type"] == "contradiction"

    # The conflict landed in the right table via insert.
    insert_calls = [
        c for c in mock_supabase.calls
        if c.get("table") == "conflicts" and c.get("action") == "insert"
    ]
    assert len(insert_calls) == 1


def test_hallucinated_skill_id_is_dropped(mock_supabase, mock_voyage, mock_anthropic, sample_skill):
    """If Claude invents an existing_skill_id that wasn't in our candidate
    set, we don't trust it and skip the row."""
    from brain.drift import check_for_drift

    mock_supabase.rows_by_table["skills"] = [{
        "id": "real-skill",
        "process_name": "Refunds",
        "summary_embedding": [0.1] * 1024,
        "archived": False,
        "org_id": "00000000-0000-0000-0000-000000000001",
    }]
    mock_anthropic["response_text"] = json.dumps({
        "conflicts": [{
            "existing_skill_id": "ghost-skill-id",  # never in our candidate set
            "existing_process_name": "Phantom",
            "conflict_type": "contradiction",
            "conflict_description": "fake",
            "existing_rule": "x",
            "new_evidence": "y",
            "suggested_update": "z",
            "severity": "low",
        }],
    })

    out = check_for_drift("text", {"id": "new-3"})
    assert out == []


def test_check_for_drift_swallows_exceptions(monkeypatch, sample_skill):
    """Drift is non-raising — a Supabase outage logs and returns []."""
    from brain import drift

    def boom():
        raise RuntimeError("supabase is down")

    monkeypatch.setattr("brain.drift.get_client", boom)
    out = drift.check_for_drift("text", {"id": "x"})
    assert out == []


# ---------------------------------------------------------------------------
# resolve_conflict — accept / dismiss / snooze
# ---------------------------------------------------------------------------

def test_resolve_unknown_action_raises(mock_supabase):
    from brain.drift import resolve_conflict

    with pytest.raises(ValueError):
        resolve_conflict("c1", "explode", "tester")


def test_resolve_missing_conflict_raises(mock_supabase):
    from brain.drift import resolve_conflict

    mock_supabase.rows_by_table["conflicts"] = []
    with pytest.raises(LookupError):
        resolve_conflict("c-not-found", "dismiss", "tester")


def test_resolve_dismiss(mock_supabase):
    from brain.drift import resolve_conflict

    mock_supabase.rows_by_table["conflicts"] = [{
        "id": "c-1",
        "existing_skill_id": "s-1",
        "status": "unresolved",
        "org_id": "00000000-0000-0000-0000-000000000001",
    }]

    row = resolve_conflict("c-1", "dismiss", "tester")
    assert row.get("status") == "dismissed"
    assert row.get("resolved_by") == "tester"
    assert row.get("resolved_at") is not None


def test_resolve_snooze_sets_seven_day_deadline(mock_supabase):
    """The snooze path always pushes snoozed_until to now + 7 days."""
    from brain.drift import resolve_conflict, SNOOZE_DAYS

    mock_supabase.rows_by_table["conflicts"] = [{
        "id": "c-2",
        "existing_skill_id": "s-2",
        "status": "unresolved",
        "org_id": "00000000-0000-0000-0000-000000000001",
    }]

    assert SNOOZE_DAYS == 7
    row = resolve_conflict("c-2", "snooze", "tester")
    assert row.get("status") == "snoozed"
    assert row.get("snoozed_until") is not None


def test_resolve_accept_archived_target_raises(mock_supabase):
    """An accept on a conflict pointing at an archived skill is refused
    — UI keeps Accept disabled, this is the API-level guard."""
    from brain.drift import resolve_conflict

    mock_supabase.rows_by_table["conflicts"] = [{
        "id": "c-3",
        "existing_skill_id": "s-archived",
        "status": "unresolved",
        "suggested_update": "x",
        "org_id": "00000000-0000-0000-0000-000000000001",
    }]
    mock_supabase.rows_by_table["skills"] = [{
        "id": "s-archived",
        "archived": True,
        "process_name": "Old skill",
        "org_id": "00000000-0000-0000-0000-000000000001",
    }]

    with pytest.raises(ValueError, match="archived"):
        resolve_conflict("c-3", "accept", "tester")


# ---------------------------------------------------------------------------
# get_unresolved_conflicts — hydration + targets_archived_version
# ---------------------------------------------------------------------------

def test_unresolved_conflicts_flag_archived_target(mock_supabase):
    from brain.drift import get_unresolved_conflicts

    mock_supabase.rows_by_table["conflicts"] = [{
        "id": "c-archive",
        "existing_skill_id": "s-archive",
        "status": "unresolved",
        "severity": "high",
        "created_at": "2026-05-01T00:00:00+00:00",
        "org_id": "00000000-0000-0000-0000-000000000001",
    }]
    mock_supabase.rows_by_table["skills"] = [{
        "id": "s-archive",
        "process_name": "Old refunds",
        "process_trigger": "trigger",
        "archived": True,
        "org_id": "00000000-0000-0000-0000-000000000001",
    }]

    rows = get_unresolved_conflicts()
    assert len(rows) == 1
    assert rows[0]["targets_archived_version"] is True
    # Live process_name from the (now-archived) skill row.
    assert rows[0]["existing_process_name"] == "Old refunds"


def test_unresolved_conflicts_active_target_not_flagged(mock_supabase):
    from brain.drift import get_unresolved_conflicts

    mock_supabase.rows_by_table["conflicts"] = [{
        "id": "c-active",
        "existing_skill_id": "s-active",
        "status": "unresolved",
        "severity": "medium",
        "created_at": "2026-05-01T00:00:00+00:00",
        "org_id": "00000000-0000-0000-0000-000000000001",
    }]
    mock_supabase.rows_by_table["skills"] = [{
        "id": "s-active",
        "process_name": "Active skill",
        "process_trigger": "trigger",
        "archived": False,
        "org_id": "00000000-0000-0000-0000-000000000001",
    }]

    rows = get_unresolved_conflicts()
    assert len(rows) == 1
    assert rows[0]["targets_archived_version"] is False
