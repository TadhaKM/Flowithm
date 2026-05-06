"""brain.store._row_to_workflow — DB column → workflow JSON mapping.

The DB-side rename `process_trigger` (because TRIGGER is reserved in
Postgres) → `trigger` in the JSON shape is the most error-prone bit, and
worth pinning with tests.
"""
from brain.store import _row_to_workflow


def test_full_row_maps_every_field():
    row = {
        "id": "abc-123",
        "process_name": "Customer refund handling",
        "description": "How we refund",
        "process_trigger": "customer files refund request",
        "steps": [
            {"step": 1, "action": "ack", "owner": "cs", "notes": ""},
            {"step": 2, "action": "process", "owner": "cs", "notes": ""},
        ],
        "decision_rules": ["if amount > $500 then escalate"],
        "approvals": ["CS lead for goodwill credits"],
        "exceptions": ["VIP customers"],
        "sources": ["slack:cs-team"],
        "source": "slack",
        "source_metadata": {"channel_name": "cs-team", "message_count": 12},
        "raw_text": "the original paste",
        "archived": False,
        "archived_at": None,
        "reviewed_at": "2026-05-01T12:00:00Z",
        "generated_at": "2026-05-01T11:00:00Z",
    }
    wf = _row_to_workflow(row)

    # Renames
    assert wf["process"] == "Customer refund handling"  # process_name → process
    assert wf["trigger"] == "customer files refund request"  # process_trigger → trigger

    # Pass-throughs
    assert wf["id"] == "abc-123"
    assert wf["description"] == "How we refund"
    assert len(wf["steps"]) == 2
    assert wf["decision_rules"] == ["if amount > $500 then escalate"]
    assert wf["approvals"] == ["CS lead for goodwill credits"]
    assert wf["exceptions"] == ["VIP customers"]
    assert wf["sources"] == ["slack:cs-team"]
    assert wf["source"] == "slack"
    assert wf["source_metadata"]["channel_name"] == "cs-team"
    assert wf["raw_text"] == "the original paste"
    assert wf["reviewed_at"] == "2026-05-01T12:00:00Z"
    assert wf["generated_at"] == "2026-05-01T11:00:00Z"


def test_missing_optional_columns_default_to_safe_values():
    row = {
        "id": "x",
        "process_name": "Bare process",
        # Every other column missing
    }
    wf = _row_to_workflow(row)
    assert wf["description"] == ""
    assert wf["trigger"] == ""
    assert wf["steps"] == []
    assert wf["decision_rules"] == []
    assert wf["approvals"] == []
    assert wf["exceptions"] == []
    assert wf["sources"] == []
    assert wf["source"] == "manual"  # default fallback
    assert wf["source_metadata"] == {}
    assert wf["raw_text"] == ""
    assert wf["archived"] is False
    assert wf["archived_at"] is None
    assert wf["reviewed_at"] is None


def test_archived_row():
    row = {
        "id": "a",
        "process_name": "P",
        "archived": True,
        "archived_at": "2026-04-01T00:00:00Z",
    }
    wf = _row_to_workflow(row)
    assert wf["archived"] is True
    assert wf["archived_at"] == "2026-04-01T00:00:00Z"


def test_null_jsonb_columns_become_empty_lists():
    """Postgres null jsonb returns Python None; mapping should normalize."""
    row = {
        "id": "n",
        "process_name": "N",
        "steps": None,
        "decision_rules": None,
        "approvals": None,
        "exceptions": None,
        "sources": None,
        "source_metadata": None,
    }
    wf = _row_to_workflow(row)
    assert wf["steps"] == []
    assert wf["decision_rules"] == []
    assert wf["approvals"] == []
    assert wf["exceptions"] == []
    assert wf["sources"] == []
    assert wf["source_metadata"] == {}


def test_falsy_source_falls_back_to_manual():
    row = {"id": "1", "process_name": "P", "source": ""}
    assert _row_to_workflow(row)["source"] == "manual"
