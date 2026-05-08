"""slack.formatter — Block Kit shape tests.

These don't post to Slack — they assert the JSON-block structure that
chat.postMessage / chat.update will receive. Slack rejects malformed blocks
silently, so testing the structure here is the only way to catch
regressions before the bot is actually invited to a channel.
"""
from __future__ import annotations

import json

import pytest

pytest.importorskip("slack_bolt")  # formatter is part of the slack package

from slack.formatter import (  # noqa: E402
    BOT_NAME,
    build_confirmation_blocks,
    build_error_blocks,
    build_loading_blocks,
    build_update_confirmation_blocks,
    build_workflow_response_blocks,
)


# ---------------------------------------------------------------------------
# Confirmation blocks
# ---------------------------------------------------------------------------

def test_confirmation_includes_extract_and_dismiss_buttons():
    blocks = build_confirmation_blocks("hello", json.dumps({"x": 1}))
    actions = next(b for b in blocks if b["type"] == "actions")
    ids = {el["action_id"] for el in actions["elements"]}
    assert ids == {"extract_workflow", "dismiss"}


def test_confirmation_truncates_long_trigger_text():
    long = "x" * 500
    blocks = build_confirmation_blocks(long, "v")
    preview = blocks[1]["text"]["text"]
    # Must contain ellipsis when over 100 chars
    assert "…" in preview


def test_confirmation_short_trigger_no_truncation():
    blocks = build_confirmation_blocks("a short message", "v")
    assert "…" not in blocks[1]["text"]["text"]


def test_confirmation_uses_bot_name():
    blocks = build_confirmation_blocks("trigger", "v")
    header = blocks[0]["text"]["text"]
    assert BOT_NAME in header


# ---------------------------------------------------------------------------
# Workflow response blocks
# ---------------------------------------------------------------------------

def _basic_workflow(**overrides):
    base = {
        "id": "wf-1",
        "process": "Test process",
        "trigger": "thing happens",
        "steps": [],
        "decision_rules": [],
        "approvals": [],
        "exceptions": [],
    }
    base.update(overrides)
    return base


def test_workflow_response_has_view_and_copy_buttons():
    blocks = build_workflow_response_blocks(
        _basic_workflow(), "channel", 5, "https://flowithm.test/workflow/wf-1",
    )
    actions = next(b for b in blocks if b["type"] == "actions")
    ids = {el["action_id"] for el in actions["elements"]}
    assert "view_workflow" in ids
    assert "copy_json" in ids


def test_workflow_response_caps_steps_at_4_and_shows_more_count():
    workflow = _basic_workflow(
        steps=[
            {"step": i, "action": f"step {i}", "owner": "owner", "notes": ""}
            for i in range(1, 8)
        ],
    )
    blocks = build_workflow_response_blocks(workflow, "ch", 1, "http://x")

    # 4 numbered step sections (1.* through 4.*), no 5/6/7
    rendered_step_numbers = []
    for b in blocks:
        if b.get("type") == "section":
            text = b.get("text", {}).get("text", "")
            for n in range(1, 8):
                if text.startswith(f"*{n}.*"):
                    rendered_step_numbers.append(n)
    assert rendered_step_numbers == [1, 2, 3, 4]

    # "+ 3 more steps" context block present
    more = [
        b for b in blocks
        if b.get("type") == "context"
        and "more steps" in b["elements"][0]["text"]
    ]
    assert len(more) == 1
    assert "+ 3 more" in more[0]["elements"][0]["text"]


def test_workflow_response_caps_decision_rules_at_3():
    workflow = _basic_workflow(
        decision_rules=[f"rule {i}" for i in range(5)],
    )
    blocks = build_workflow_response_blocks(workflow, "ch", 1, "http://x")
    rules_block = next(
        b for b in blocks
        if b.get("type") == "section"
        and "Decision rules" in b.get("text", {}).get("text", "")
    )
    text = rules_block["text"]["text"]
    # First three rules present
    assert "rule 0" in text and "rule 1" in text and "rule 2" in text
    # Fourth and fifth are NOT in the visible text (they're in the "+ 2 more" tail)
    assert "rule 3" not in text and "rule 4" not in text
    # And the more-marker is shown
    assert "+ 2 more" in text


def test_workflow_response_omits_sections_when_empty():
    """Approvals / exceptions sections shouldn't render when their lists are empty."""
    blocks = build_workflow_response_blocks(_basic_workflow(), "ch", 1, "http://x")
    serialized = json.dumps(blocks)
    assert "Requires approval" not in serialized
    assert "Exceptions" not in serialized


def test_workflow_response_includes_approvals_when_present():
    blocks = build_workflow_response_blocks(
        _basic_workflow(approvals=["CFO sign-off required"]),
        "ch", 1, "http://x",
    )
    serialized = json.dumps(blocks)
    assert "Requires approval" in serialized
    assert "CFO sign-off required" in serialized


def test_workflow_response_update_existing_button_only_when_existing_present():
    # No existing → no update_existing button
    blocks = build_workflow_response_blocks(_basic_workflow(), "ch", 1, "http://x")
    actions = next(b for b in blocks if b["type"] == "actions")
    ids = {el["action_id"] for el in actions["elements"]}
    assert "update_existing" not in ids

    # With existing → button appears
    existing = {"id": "old-1", "process": "Older version"}
    blocks_with = build_workflow_response_blocks(
        _basic_workflow(), "ch", 1, "http://x", existing=existing,
    )
    actions_with = next(b for b in blocks_with if b["type"] == "actions")
    ids_with = {el["action_id"] for el in actions_with["elements"]}
    assert "update_existing" in ids_with


def test_workflow_response_save_failed_warning_in_footer():
    blocks = build_workflow_response_blocks(
        _basic_workflow(), "ch", 1, "http://x", save_failed=True,
    )
    footer = blocks[-1]
    assert footer["type"] == "context"
    assert "Couldn't save" in footer["elements"][0]["text"]


def test_workflow_response_updated_label_when_updated_flag():
    blocks = build_workflow_response_blocks(
        _basic_workflow(), "ch", 1, "http://x", updated=True,
    )
    header = blocks[0]["text"]["text"]
    assert "Workflow updated" in header


def test_workflow_response_uses_default_trigger_text_when_empty():
    blocks = build_workflow_response_blocks(
        _basic_workflow(trigger=""), "ch", 1, "http://x",
    )
    serialized = json.dumps(blocks)
    assert "manual / on demand" in serialized


def test_workflow_response_view_button_carries_url():
    blocks = build_workflow_response_blocks(
        _basic_workflow(), "ch", 1, "https://example.com/workflow/wf-1",
    )
    actions = next(b for b in blocks if b["type"] == "actions")
    view = next(el for el in actions["elements"] if el["action_id"] == "view_workflow")
    assert view["url"] == "https://example.com/workflow/wf-1"


def test_workflow_response_footer_includes_channel_and_count():
    blocks = build_workflow_response_blocks(
        _basic_workflow(), "engineering", 17, "http://x",
    )
    footer = blocks[-1]
    text = footer["elements"][0]["text"]
    assert "engineering" in text
    assert "17 messages" in text


# ---------------------------------------------------------------------------
# Misc builders
# ---------------------------------------------------------------------------

def test_loading_blocks_have_one_section_with_text():
    blocks = build_loading_blocks("Doing work…")
    assert len(blocks) == 1
    assert blocks[0]["type"] == "section"
    assert "Doing work" in blocks[0]["text"]["text"]


def test_update_confirmation_has_confirm_and_cancel():
    blocks = build_update_confirmation_blocks("Old name", "new-1", "old-1")
    actions = next(b for b in blocks if b["type"] == "actions")
    ids = {el["action_id"] for el in actions["elements"]}
    assert ids == {"confirm_update", "cancel_update"}


def test_update_confirmation_carries_ids_in_button_value():
    """Confirm-update button value is HMAC-signed (H-5). The plaintext
    JSON form was a vector for forged button payloads, so we now verify
    via slack.sign rather than json.loads."""
    from slack.sign import verify_action

    blocks = build_update_confirmation_blocks("Old name", "new-1", "old-1")
    actions = next(b for b in blocks if b["type"] == "actions")
    confirm = next(el for el in actions["elements"] if el["action_id"] == "confirm_update")
    payload = verify_action(confirm["value"])
    assert payload is not None
    assert payload["new_id"] == "new-1"
    assert payload["existing_id"] == "old-1"


def test_update_confirmation_value_rejects_tampering():
    """A modified blob must fail verification. Verify both body- and
    signature-side tampering plus an entirely unsigned plaintext."""
    from slack.sign import sign_action, verify_action

    blocks = build_update_confirmation_blocks("Old name", "new-1", "old-1")
    actions = next(b for b in blocks if b["type"] == "actions")
    confirm = next(el for el in actions["elements"] if el["action_id"] == "confirm_update")

    body, sig = confirm["value"].split(".", 1)

    # Body for an attacker-supplied payload signed under a wrong key — the
    # cleanest demonstration of "the body says X but the sig was made for Y".
    forged = sign_action({"new_id": "victim-id", "existing_id": "another-victim"})
    forged_body, _ = forged.split(".", 1)
    assert verify_action(forged_body + "." + sig) is None

    # Wrong signature half.
    assert verify_action(body + ".aaaaaaaaaaaa") is None

    # Plain-JSON values like the pre-H-5 format should be rejected too.
    import json as _json
    assert verify_action(_json.dumps({"new_id": "x", "existing_id": "y"})) is None
    # Empty / missing.
    assert verify_action("") is None
    assert verify_action(None) is None


def test_error_blocks_include_message():
    blocks = build_error_blocks("Something went wrong")
    assert "Something went wrong" in blocks[0]["text"]["text"]
