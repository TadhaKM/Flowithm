"""Tests for Claude malformed-output paths in drift.py and query.py.

P0 from the QA audit: mock_anthropic always returns valid JSON, but real
Claude occasionally returns prefixed prose, refusal blocks, or empty
content. These tests verify those paths don't crash.
"""
from __future__ import annotations

import json

import pytest


# ---------------------------------------------------------------------------
# drift.check_for_drift — malformed / refusal paths
# ---------------------------------------------------------------------------

def test_drift_claude_refusal_returns_empty(mock_supabase, mock_voyage, mock_anthropic):
    """stop_reason='refusal' → logged and returns []."""
    from brain.drift import check_for_drift

    skill_row = {
        "id": "s1", "process_name": "Refunds", "description": "", "process_trigger": "",
        "steps": [], "decision_rules": [], "approvals": [], "exceptions": [], "sources": [],
        "source": "manual", "version": 1, "generated_at": "2025-01-01",
        "needs_review": False, "needs_review_reason": None, "similarity": 0.9,
        "summary_embedding": [0.1] * 1024, "archived": False,
        "org_id": "00000000-0000-0000-0000-000000000001",
    }
    mock_supabase.rpc_results = {"match_skills": [skill_row]}
    mock_anthropic["response_text"] = ""
    mock_anthropic["stop_reason"] = "refusal"

    result = check_for_drift("some text", {"id": "new-1"})
    assert result == []


def test_drift_claude_invalid_json_returns_empty(mock_supabase, mock_voyage, mock_anthropic):
    """Garbled JSON → caught by json.loads, returns []."""
    from brain.drift import check_for_drift

    skill_row = {
        "id": "s1", "process_name": "Refunds", "description": "", "process_trigger": "",
        "steps": [], "decision_rules": [], "approvals": [], "exceptions": [], "sources": [],
        "source": "manual", "version": 1, "generated_at": "2025-01-01",
        "needs_review": False, "needs_review_reason": None, "similarity": 0.9,
        "summary_embedding": [0.1] * 1024, "archived": False,
        "org_id": "00000000-0000-0000-0000-000000000001",
    }
    mock_supabase.rpc_results = {"match_skills": [skill_row]}
    mock_anthropic["response_text"] = "I think there might be a conflict but..."  # not JSON

    result = check_for_drift("some text", {"id": "new-1"})
    assert result == []


def test_drift_claude_empty_conflicts_array(mock_supabase, mock_voyage, mock_anthropic):
    """Claude says no conflicts → returns []."""
    from brain.drift import check_for_drift

    skill_row = {
        "id": "s1", "process_name": "Refunds", "description": "", "process_trigger": "",
        "steps": [], "decision_rules": [], "approvals": [], "exceptions": [], "sources": [],
        "source": "manual", "version": 1, "generated_at": "2025-01-01",
        "needs_review": False, "needs_review_reason": None, "similarity": 0.9,
        "summary_embedding": [0.1] * 1024, "archived": False,
        "org_id": "00000000-0000-0000-0000-000000000001",
    }
    mock_supabase.rpc_results = {"match_skills": [skill_row]}
    mock_anthropic["response_text"] = json.dumps({"conflicts": []})

    result = check_for_drift("some text", {"id": "new-1"})
    assert result == []


# ---------------------------------------------------------------------------
# query.generate_workflow_from_text — malformed / refusal paths
# ---------------------------------------------------------------------------

def test_generate_workflow_refusal_raises(mock_supabase, mock_voyage, mock_anthropic):
    """Claude refuses → should raise RuntimeError."""
    from brain.query import generate_workflow_from_text

    mock_anthropic["stop_reason"] = "refusal"
    mock_anthropic["response_text"] = ""

    with pytest.raises(RuntimeError, match="refused"):
        generate_workflow_from_text("process", "some material")


def test_generate_workflow_malformed_json_raises(mock_supabase, mock_voyage, mock_anthropic):
    """Non-JSON response → json.loads raises."""
    from brain.query import generate_workflow_from_text

    mock_anthropic["stop_reason"] = "end_turn"
    mock_anthropic["response_text"] = "Here is the workflow: {broken json"

    with pytest.raises(Exception):
        generate_workflow_from_text("process", "some material")


# ---------------------------------------------------------------------------
# drift.check_chunks_against_skills — basic coverage
# ---------------------------------------------------------------------------

def test_check_chunks_against_skills_no_skills_returns_empty(mock_supabase, mock_voyage, mock_anthropic):
    """No skills in the org → returns [] without calling Claude."""
    from brain.drift import check_chunks_against_skills
    from brain.ingestors import Chunk

    mock_supabase.rpc_results = {"match_skills": []}
    chunks = [Chunk(source_type="slack", source_name="test", content="some content", metadata={})]
    result = check_chunks_against_skills(chunks)
    assert result == []
    assert len(mock_anthropic["calls"]) == 0


def test_check_chunks_against_skills_with_hit(mock_supabase, mock_voyage, mock_anthropic):
    """A chunk that matches a skill and Claude flags → conflict inserted."""
    from brain.drift import check_chunks_against_skills
    from brain.ingestors import Chunk

    mock_supabase.rpc_results = {"match_skills": [{
        "id": "s1", "process_name": "Refunds", "similarity": 0.9,
        "description": "", "process_trigger": "", "steps": [],
        "decision_rules": [], "approvals": [], "exceptions": [],
        "sources": [], "source": "manual", "version": 1,
        "generated_at": "2025-01-01", "needs_review": False,
        "needs_review_reason": None,
    }]}
    mock_anthropic["response_text"] = json.dumps({
        "is_conflict": True,
        "conflict_type": "contradiction",
        "conflict_description": "test conflict",
        "existing_rule": "old rule",
        "new_evidence": "new evidence",
        "suggested_update": "update it",
        "severity": "high",
    })

    chunks = [Chunk(source_type="slack", source_name="test", content="conflicting content", metadata={})]
    result = check_chunks_against_skills(chunks)
    assert len(result) == 1
    assert result[0]["conflict_type"] == "contradiction"


# ---------------------------------------------------------------------------
# Multi-tenant isolation (negative tenancy test)
# ---------------------------------------------------------------------------

def test_list_workflows_isolates_by_org(mock_supabase):
    """Two orgs seeded → listing for org A returns zero of org B's rows."""
    from brain.store import list_workflows

    mock_supabase.rows_by_table["skills"] = [
        {
            "id": "a1", "process_name": "Org A process", "description": "",
            "process_trigger": "", "steps": [], "decision_rules": [],
            "approvals": [], "exceptions": [], "sources": [],
            "source": "manual", "source_metadata": {}, "raw_text": "",
            "archived": False, "archived_at": None, "reviewed_at": None,
            "generated_at": "2025-01-01", "needs_review": False,
            "needs_review_reason": None, "version": 1,
            "org_id": "org-a",
        },
        {
            "id": "b1", "process_name": "Org B process", "description": "",
            "process_trigger": "", "steps": [], "decision_rules": [],
            "approvals": [], "exceptions": [], "sources": [],
            "source": "manual", "source_metadata": {}, "raw_text": "",
            "archived": False, "archived_at": None, "reviewed_at": None,
            "generated_at": "2025-01-01", "needs_review": False,
            "needs_review_reason": None, "version": 1,
            "org_id": "org-b",
        },
    ]

    # Org A should only see its own row.
    result_a = list_workflows(limit=10, org_id="org-a")
    assert len(result_a) == 1
    assert result_a[0]["process"] == "Org A process"

    # Org B should only see its own row.
    result_b = list_workflows(limit=10, org_id="org-b")
    assert len(result_b) == 1
    assert result_b[0]["process"] == "Org B process"


def test_get_unresolved_conflicts_isolates_by_org(mock_supabase):
    """Conflicts from org A are invisible to org B."""
    from brain.drift import get_unresolved_conflicts

    mock_supabase.rows_by_table["conflicts"] = [
        {
            "id": "c1", "existing_skill_id": "s1", "new_skill_id": None,
            "existing_process_name": "A's process", "conflict_type": "contradiction",
            "conflict_description": "test", "existing_rule": "x",
            "new_evidence": "y", "suggested_update": "z", "severity": "high",
            "status": "unresolved", "snoozed_until": None,
            "resolved_by": None, "resolved_at": None, "created_at": "2025-01-01",
            "org_id": "org-a",
        },
        {
            "id": "c2", "existing_skill_id": "s2", "new_skill_id": None,
            "existing_process_name": "B's process", "conflict_type": "update",
            "conflict_description": "test", "existing_rule": "x",
            "new_evidence": "y", "suggested_update": "z", "severity": "medium",
            "status": "unresolved", "snoozed_until": None,
            "resolved_by": None, "resolved_at": None, "created_at": "2025-01-01",
            "org_id": "org-b",
        },
    ]
    mock_supabase.rows_by_table["skills"] = []

    result_a = get_unresolved_conflicts(org_id="org-a")
    assert len(result_a) == 1
    assert result_a[0]["existing_process_name"] == "A's process"
