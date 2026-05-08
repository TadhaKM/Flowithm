"""Slack Block Kit formatters for the Flowithm bot.

All user-visible Slack output goes through builders in this file. The
handlers module never builds blocks inline so every message-shape change
lives in one place.

Note on colors: Slack's `style` attribute on buttons only accepts "primary"
(green-ish), "danger" (red), or default. There is no way to set a custom
hex color for a button — so the brand teal #1D9E75 specified in the design
brief is approximated with `style: "primary"` for the main CTAs.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


def _sign_value(value: Any) -> str:
    """HMAC-sign a button value so handlers can verify it wasn't tampered."""
    try:
        from slack.sign import sign_action
        return sign_action(value)
    except Exception:
        # If signing fails (no key configured), fall back to raw JSON.
        return value if isinstance(value, str) else json.dumps(value)
from typing import Any

BOT_NAME = "Flowithm"


# ---------------------------------------------------------------------------
# Confirmation message — posted 60s after a triggering message.
# ---------------------------------------------------------------------------
def build_confirmation_blocks(trigger_text: str, action_value: str) -> list[dict]:
    preview = trigger_text.strip().replace("\n", " ")
    if len(preview) > 100:
        preview = preview[:97] + "…"
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f":mag:  *{BOT_NAME} detected a process in this thread*",
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"_“{preview}”_",
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Extract workflow"},
                    "style": "primary",
                    "action_id": "extract_workflow",
                    "value": _sign_value(action_value),
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Dismiss"},
                    "action_id": "dismiss",
                    "value": "dismiss",
                },
            ],
        },
    ]


# ---------------------------------------------------------------------------
# Loading state — shown while we call Claude / Flowithm.
# ---------------------------------------------------------------------------
def build_loading_blocks(text: str) -> list[dict]:
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f":hourglass_flowing_sand:  *{text}*",
            },
        }
    ]


# ---------------------------------------------------------------------------
# Rich workflow response — replaces the loading message after extraction.
# ---------------------------------------------------------------------------
def build_workflow_response_blocks(
    workflow: dict[str, Any],
    channel_name: str,
    message_count: int,
    deeplink: str,
    existing: dict[str, Any] | None = None,
    updated: bool = False,
    save_failed: bool = False,
) -> list[dict]:
    blocks: list[dict] = []
    process_name = workflow.get("process") or "Untitled process"
    workflow_id = str(workflow.get("id") or "")

    # Section 1 — Header
    header_label = "Workflow updated" if updated else "Workflow extracted"
    blocks.append({
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": f":white_check_mark:  *{header_label}*\n*{process_name}*",
        },
    })

    # Section 2 — Trigger
    trigger = workflow.get("trigger") or "manual / on demand"
    blocks.append({
        "type": "section",
        "fields": [
            {"type": "mrkdwn", "text": f"*Trigger*\n{trigger}"},
        ],
    })

    # Section 3 — Steps (first 4, then a "+ N more" context block)
    steps = workflow.get("steps") or []
    if steps:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*Steps*"},
        })
        for s in steps[:4]:
            owner = s.get("owner") or "unspecified"
            line = f"*{s.get('step', '?')}.* {s.get('action', '')}  •  Owner: _{owner}_"
            # The workflow shape uses `notes` for per-step conditional logic.
            logic = s.get("notes") or s.get("logic")
            if logic:
                line += f"\n         _↳ {logic}_"
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": line},
            })
        if len(steps) > 4:
            blocks.append({
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": f"_+ {len(steps) - 4} more steps_"}
                ],
            })

    # Section 4 — Decision rules (top 3)
    rules = workflow.get("decision_rules") or []
    if rules:
        bullets = "\n".join(f"• {r}" for r in rules[:3])
        more = "" if len(rules) <= 3 else f"\n_+ {len(rules) - 3} more_"
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Decision rules*\n{bullets}{more}"},
        })

    # Section 5 — Approvals
    approvals = workflow.get("approvals") or []
    if approvals:
        bullets = "\n".join(f"• {a}" for a in approvals)
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f":warning:  *Requires approval*\n{bullets}"},
        })

    # Section 6 — Divider
    blocks.append({"type": "divider"})

    # Section 7 — Action buttons
    elements: list[dict] = []
    if deeplink:
        elements.append({
            "type": "button",
            "text": {"type": "plain_text", "text": "View full workflow →"},
            "url": deeplink,
            "style": "primary",
            "action_id": "view_workflow",
        })
    elements.append({
        "type": "button",
        "text": {"type": "plain_text", "text": "Copy skills file JSON"},
        "action_id": "copy_json",
        "value": _sign_value(workflow_id),
    })
    if existing and existing.get("id") and str(existing.get("id")) != workflow_id:
        # H-5: sign the value blob so a workspace member can't edit the
        # button payload to target a workflow they shouldn't be able to
        # update. The matching verify_action() in handlers.py rejects
        # any tampered or unsigned value.
        from slack.sign import sign_action
        elements.append({
            "type": "button",
            "text": {"type": "plain_text", "text": "Update existing"},
            "style": "danger",
            "action_id": "update_existing",
            "value": sign_action({
                "new_id": workflow_id,
                "existing_id": str(existing.get("id")),
                "existing_name": existing.get("process") or "",
            }),
        })
    blocks.append({"type": "actions", "elements": elements})

    # Section 8 — Footer
    now = datetime.now(timezone.utc).strftime("%H:%M UTC")
    footer_lines = [
        f"Extracted from #{channel_name} • {message_count} messages • {now}",
    ]
    if save_failed:
        footer_lines.append(
            ":warning: Couldn't save to knowledge base — copy the JSON above to save manually."
        )
    else:
        footer_lines.append(f"Saved to {BOT_NAME} knowledge base")
    blocks.append({
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": "\n".join(footer_lines)}],
    })

    return blocks


# ---------------------------------------------------------------------------
# "Update existing?" — shown after the "Update existing" button is clicked.
# ---------------------------------------------------------------------------
def build_update_confirmation_blocks(
    existing_name: str,
    new_id: str,
    existing_id: str,
) -> list[dict]:
    # H-5: sign every button value below — same reason as in
    # build_workflow_response_blocks.
    from slack.sign import sign_action
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*Update existing workflow?*\n"
                    f"This will update *{existing_name}*. The old version will be "
                    f"archived and remain accessible. Continue?"
                ),
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Confirm update"},
                    "style": "primary",
                    "action_id": "confirm_update",
                    "value": sign_action({"new_id": new_id, "existing_id": existing_id}),
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Cancel"},
                    "action_id": "cancel_update",
                    "value": sign_action({"new_id": new_id, "kind": "cancel"}),
                },
            ],
        },
    ]


# ---------------------------------------------------------------------------
# Error state — replaces the loading or response message on failure.
# ---------------------------------------------------------------------------
def build_error_blocks(message: str) -> list[dict]:
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f":warning:  *{message}*",
            },
        }
    ]
