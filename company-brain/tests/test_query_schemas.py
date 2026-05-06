"""brain.query — schema shapes and prompt invariants.

These are static-shape tests; they don't call Claude. The goal is to
catch field renames or schema regressions immediately.
"""
from brain.query import (
    QUERY_SYSTEM_PROMPT,
    SKILL_SCHEMA,
    SKILLS_SYSTEM_PROMPT,
    WORKFLOW_SCHEMA,
    WORKFLOW_SYSTEM_PROMPT,
)


# ---------------------------------------------------------------------------
# SKILL_SCHEMA — /skills response shape (per-step logic + sources_summary)
# ---------------------------------------------------------------------------

def test_skill_schema_required_fields():
    expected = {
        "process",
        "trigger",
        "steps",
        "decision_rules",
        "approvals",
        "exceptions",
        "sources_summary",
    }
    assert set(SKILL_SCHEMA["required"]) == expected


def test_skill_schema_dropped_description_and_sources_array():
    """The newer skill shape doesn't carry description or a sources[] array."""
    props = SKILL_SCHEMA["properties"]
    assert "description" not in props
    assert "sources" not in props
    assert props["sources_summary"]["type"] == "string"


def test_skill_step_logic_and_notes_are_nullable():
    step_props = SKILL_SCHEMA["properties"]["steps"]["items"]["properties"]
    assert step_props["logic"]["type"] == ["string", "null"]
    assert step_props["notes"]["type"] == ["string", "null"]
    assert step_props["action"]["type"] == "string"
    assert step_props["owner"]["type"] == "string"


def test_skill_schema_strict_object():
    assert SKILL_SCHEMA["additionalProperties"] is False
    step_items = SKILL_SCHEMA["properties"]["steps"]["items"]
    assert step_items["additionalProperties"] is False


# ---------------------------------------------------------------------------
# WORKFLOW_SCHEMA — /workflows/generate response shape (used by UI + history)
# ---------------------------------------------------------------------------

def test_workflow_schema_keeps_description_and_sources_array():
    """The workflow shape (UI + history rows) intentionally retains both."""
    props = WORKFLOW_SCHEMA["properties"]
    assert props["description"]["type"] == "string"
    assert props["sources"]["type"] == "array"
    assert "sources_summary" not in props


def test_workflow_step_no_logic_field():
    """The workflow step shape pre-dates per-step logic; notes is just a string."""
    step_props = WORKFLOW_SCHEMA["properties"]["steps"]["items"]["properties"]
    assert "logic" not in step_props
    assert step_props["notes"]["type"] == "string"


def test_workflow_schema_required_fields():
    expected = {
        "process",
        "description",
        "trigger",
        "steps",
        "decision_rules",
        "approvals",
        "exceptions",
        "sources",
    }
    assert set(WORKFLOW_SCHEMA["required"]) == expected


def test_workflow_schema_strict_object():
    assert WORKFLOW_SCHEMA["additionalProperties"] is False


# ---------------------------------------------------------------------------
# Prompt content — smoke checks on key phrases
# ---------------------------------------------------------------------------

def test_query_prompt_requires_grounding_and_citations():
    assert "ONLY the information" in QUERY_SYSTEM_PROMPT
    assert "[1]" in QUERY_SYSTEM_PROMPT  # citation format example


def test_skills_prompt_distinguishes_logic_from_decision_rules():
    """The crucial-to-get-right distinction; regression target."""
    assert "logic" in SKILLS_SYSTEM_PROMPT
    assert "decision_rules" in SKILLS_SYSTEM_PROMPT
    assert "per-step" in SKILLS_SYSTEM_PROMPT.lower()


def test_workflow_prompt_grounds_in_source_material():
    assert "ONLY information present" in WORKFLOW_SYSTEM_PROMPT
    assert "trigger" in WORKFLOW_SYSTEM_PROMPT
