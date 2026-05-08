"""Slack event + interaction handlers for the Flowithm bot.

Architecture:
  message event   → trigger detection → 60s threading.Timer → post confirmation
  Extract button  → ack() → spawn background thread → fetch → Claude → API → render
  Dismiss button  → delete confirmation message
  Copy JSON       → background fetch + post code snippet in thread
  Update existing → in-place confirmation flow (Confirm / Cancel)

All button handlers ack() within Slack's 3-second window and do the actual
work in a daemon thread. We never block the Slack event loop.
"""
from __future__ import annotations

import functools
import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

import anthropic
import requests
from slack_bolt import App
from slack_sdk.web import WebClient

from brain.text_utils import cap_tokens
from slack.formatter import (
    build_confirmation_blocks,
    build_error_blocks,
    build_loading_blocks,
    build_update_confirmation_blocks,
    build_workflow_response_blocks,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
FLOWITHM_URL = os.environ.get("FLOWITHM_URL", "http://localhost:3000").rstrip("/")
FLOWITHM_API_URL = os.environ.get("FLOWITHM_API_URL", "http://localhost:8000").rstrip("/")

# P-5: bounded thread pool instead of unbounded threading.Thread spawns.
_SLACK_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="flowithm-slack")
# P-16: module-level user name cache shared across concurrent extractions.
_user_name_cache: dict[str, str] = {}


def _api_headers() -> dict:
    """Build the Authorization + X-Org-ID headers every FastAPI request
    from the bot needs. The internal endpoints are admin-gated post the
    C-4 lockdown — without the admin token every bot HTTP call would 401.
    """
    headers: dict[str, str] = {}
    admin_token = os.environ.get("ADMIN_TOKEN", "")
    org_id = os.environ.get("ORG_ID", "")
    if admin_token:
        headers["Authorization"] = f"Bearer {admin_token}"
    if org_id:
        headers["X-Org-ID"] = org_id
    return headers

MIN_WORDS = 20
THREAD_WAIT_SECONDS = 60
TOKEN_CAP = 4000
CLAUDE_MODEL = "claude-sonnet-4-6"

# Substring patterns are case-insensitive. The few that need word boundaries
# (SOP) use explicit \b in their regex form below. False positives are cheap
# (the user just clicks Dismiss); false negatives are expensive.
TRIGGER_PATTERNS = [
    r"runbook",
    r"run book",
    r"how do we",
    r"how does",
    r"how should we",
    r"process for",
    r"process when",
    r"policy for",
    r"policy on",
    r"policy when",
    r"when a customer",
    r"when the customer",
    r"on[\s-]?call",
    r"oncall",
    r"incident",
    r"outage",
    r"post[\s-]?mortem",
    r"escalation",
    r"escalate",
    r"what happens when",
    r"what do we do when",
    r"\bSOP\b",
    r"standard operating procedure",
]
TRIGGER_REGEX = re.compile("|".join(TRIGGER_PATTERNS), re.IGNORECASE)

# Slack message links to Notion/Google Doc pages — we surface these in the
# pasted thread material so Claude can reference them.
DOC_URL_REGEX = re.compile(
    # Matches notion.so / notion.site / docs.google.com — with or without a
    # subdomain (real Notion shared URLs are <workspace>.notion.site).
    r"https?://(?:[\w-]+\.)?(?:notion\.so|notion\.site|docs\.google\.com)/[^\s>|]+",
    re.IGNORECASE,
)

_anthropic_client: anthropic.Anthropic | None = None


def _anthropic() -> anthropic.Anthropic:
    global _anthropic_client
    if _anthropic_client is None:
        _anthropic_client = anthropic.Anthropic()
    return _anthropic_client


# ---------------------------------------------------------------------------
# Public registration entry point
# ---------------------------------------------------------------------------
def register(app: App) -> None:
    """Wire all event + action handlers onto the Bolt app."""

    @app.event("message")
    def on_message(event: dict[str, Any], client: WebClient, context: dict[str, Any]):
        # Skip bot messages (covers our own bot too — Slack sets bot_id on
        # any message authored by a bot user).
        if event.get("bot_id") or event.get("subtype") == "bot_message":
            return
        # Skip DMs and group DMs — bot only operates in shared channels.
        if event.get("channel_type") in ("im", "mpim"):
            return
        # Skip message edits / deletes / etc.
        if event.get("subtype") not in (None, "thread_broadcast"):
            return

        text = (event.get("text") or "").strip()
        if not text or len(text.split()) < MIN_WORDS:
            return
        if not TRIGGER_REGEX.search(text):
            return

        channel_id = event["channel"]
        thread_ts = event.get("thread_ts") or event["ts"]
        triggered_by = event.get("user", "")
        team_id = event.get("team") or context.get("team_id") or ""

        action_value = json.dumps({
            "channel_id": channel_id,
            "thread_ts": thread_ts,
            "trigger_text": text[:500],
            "triggered_by": triggered_by,
            "team_id": team_id,
        })

        import time as _time

        def _delayed_confirm():
            _time.sleep(THREAD_WAIT_SECONDS)
            _post_confirmation(
                client=client,
                channel_id=channel_id,
                thread_ts=thread_ts,
                trigger_text=text,
                action_value=action_value,
            )

        _SLACK_POOL.submit(_delayed_confirm)

    # ------------------------------------------------------------------
    # Confirmation message buttons
    # ------------------------------------------------------------------
    @app.action("extract_workflow")
    def on_extract(ack, body, client: WebClient):
        ack()
        from slack.sign import verify_action
        value = verify_action((body.get("actions") or [{}])[0].get("value"))
        if not isinstance(value, dict):
            print("[extract] unsigned or tampered button value", flush=True)
            return
        _SLACK_POOL.submit(
            _extract_workflow_async,
            client=client,
            channel_id=value.get("channel_id"),
            thread_ts=value.get("thread_ts"),
            message_ts=body["message"]["ts"],
            triggered_by=value.get("triggered_by", ""),
            team_id=value.get("team_id", ""),
        )

    @app.action("dismiss")
    def on_dismiss(ack, body, client: WebClient):
        ack()
        try:
            client.chat_delete(
                channel=body["channel"]["id"],
                ts=body["message"]["ts"],
            )
        except Exception as exc:
            print(f"[dismiss] chat_delete failed: {exc}", flush=True)

    # ------------------------------------------------------------------
    # Workflow response buttons
    # ------------------------------------------------------------------
    @app.action("view_workflow")
    def on_view(ack, body, client):  # noqa: ARG001 — URL button, no-op
        ack()

    @app.action("copy_json")
    def on_copy_json(ack, body, client: WebClient):
        ack()
        from slack.sign import verify_action
        raw = (body.get("actions") or [{}])[0].get("value") or ""
        verified = verify_action(raw)
        workflow_id = verified if isinstance(verified, str) else str(verified or raw)
        _SLACK_POOL.submit(
            _post_json_snippet,
            client=client,
            channel_id=body["channel"]["id"],
            thread_ts=body["message"].get("thread_ts") or body["message"]["ts"],
            workflow_id=workflow_id,
        )

    @app.action("update_existing")
    def on_update_existing(ack, body, client: WebClient):
        ack()
        # H-5: every interactive button value is HMAC-signed by the
        # formatter; reject anything tampered or unsigned.
        from slack.sign import verify_action
        value = verify_action((body.get("actions") or [{}])[0].get("value"))
        if not isinstance(value, dict):
            return
        _SLACK_POOL.submit(
            _show_update_confirmation,
            client=client,
            channel_id=body["channel"]["id"],
            message_ts=body["message"]["ts"],
            new_id=value.get("new_id", ""),
            existing_id=value.get("existing_id", ""),
            existing_name=value.get("existing_name", "the existing workflow"),
        )

    @app.action("confirm_update")
    def on_confirm_update(ack, body, client: WebClient):
        ack()
        from slack.sign import verify_action
        value = verify_action((body.get("actions") or [{}])[0].get("value"))
        if not isinstance(value, dict):
            return
        _SLACK_POOL.submit(
            _perform_update,
            client=client,
            channel_id=body["channel"]["id"],
            message_ts=body["message"]["ts"],
            new_id=value.get("new_id", ""),
            existing_id=value.get("existing_id", ""),
        )

    @app.action("cancel_update")
    def on_cancel_update(ack, body, client: WebClient):
        ack()
        from slack.sign import verify_action
        value = verify_action((body.get("actions") or [{}])[0].get("value"))
        if not isinstance(value, dict):
            return
        workflow_id = value.get("new_id") or ""
        _SLACK_POOL.submit(
            _revert_to_workflow,
            client=client,
            channel_id=body["channel"]["id"],
            message_ts=body["message"]["ts"],
            workflow_id=workflow_id,
        )


# ---------------------------------------------------------------------------
# Background workers
# ---------------------------------------------------------------------------
def _post_confirmation(
    client: WebClient,
    channel_id: str,
    thread_ts: str,
    trigger_text: str,
    action_value: str,
) -> None:
    try:
        client.chat_postMessage(
            channel=channel_id,
            thread_ts=thread_ts,
            blocks=build_confirmation_blocks(trigger_text, action_value),
            text="Flowithm detected a process in this thread",
        )
    except Exception as exc:
        print(f"[confirmation] chat_postMessage failed: {exc}", flush=True)


def _extract_workflow_async(
    client: WebClient,
    channel_id: str,
    thread_ts: str,
    message_ts: str,
    triggered_by: str,
    team_id: str,
) -> None:
    """The big workflow-extraction pipeline. Runs entirely off the Slack ack thread."""

    # 1) Loading state
    try:
        client.chat_update(
            channel=channel_id,
            ts=message_ts,
            blocks=build_loading_blocks("Extracting workflow… this takes about 10 seconds"),
            text="Extracting workflow…",
        )
    except Exception as exc:
        print(f"[extract] loading update failed: {exc}", flush=True)

    # 2) Collect the thread
    try:
        thread_text, message_count = _collect_thread(client, channel_id, thread_ts)
    except Exception as exc:
        print(f"[extract] collect_thread failed: {exc}", flush=True)
        _show_error(
            client, channel_id, message_ts,
            "Couldn't read the thread. Please try again or paste it manually at "
            f"{FLOWITHM_URL}.",
        )
        return

    if not thread_text.strip():
        _show_error(
            client, channel_id, message_ts,
            "The thread looks empty. Try again once there's a bit more context.",
        )
        return

    # 3) Channel name (best-effort)
    try:
        channel_info = client.conversations_info(channel=channel_id)
        channel_name = channel_info["channel"]["name"]
    except Exception:
        channel_name = "channel"

    # 4) Process-name detection via Claude
    try:
        process_name = _detect_process_name(thread_text)
    except Exception as exc:
        print(f"[extract] detect_process_name failed: {exc}", flush=True)
        _show_error(
            client, channel_id, message_ts,
            "Couldn't extract a workflow from this thread. The thread may be too short "
            f"or too ambiguous. Try again or paste it manually at {FLOWITHM_URL}.",
        )
        return

    # 5) Call Flowithm to generate + persist
    save_failed = False
    workflow: dict[str, Any] | None = None
    source_metadata = {
        "channel_id": channel_id,
        "channel_name": channel_name,
        "thread_ts": thread_ts,
        "message_count": message_count,
        "triggered_by": triggered_by,
        "workspace": team_id,
    }
    try:
        resp = requests.post(
            f"{FLOWITHM_API_URL}/workflows/generate",
            json={
                "name": process_name,
                "content": thread_text,
                "source": "slack",
                "source_metadata": source_metadata,
            },
            headers=_api_headers(),
            timeout=90,
        )
        resp.raise_for_status()
        workflow = resp.json()
    except Exception as exc:
        print(f"[extract] /workflows/generate failed: {exc}", flush=True)
        _show_error(
            client, channel_id, message_ts,
            "Couldn't extract a workflow from this thread. The thread may be too short "
            f"or too ambiguous. Try again or paste it manually at {FLOWITHM_URL}.",
        )
        return

    workflow_id = str(workflow.get("id") or "")
    if not workflow_id:
        # Generation worked but persistence didn't — show the JSON and warn.
        save_failed = True

    # 6) Look up similar existing — for the optional "Update existing" button
    existing: dict[str, Any] | None = None
    if workflow_id:
        try:
            sim = requests.get(
                f"{FLOWITHM_API_URL}/workflows/similar",
                params={"name": process_name, "exclude_id": workflow_id},
                headers=_api_headers(),
                timeout=10,
            )
            if sim.ok:
                existing = sim.json() or None
        except Exception as exc:
            print(f"[extract] similar lookup failed: {exc}", flush=True)

    # 7) Build deeplink
    deeplink = f"{FLOWITHM_URL}/workflow/{workflow_id}" if workflow_id else FLOWITHM_URL

    # 8) Render rich response
    try:
        client.chat_update(
            channel=channel_id,
            ts=message_ts,
            blocks=build_workflow_response_blocks(
                workflow=workflow,
                channel_name=channel_name,
                message_count=message_count,
                deeplink=deeplink,
                existing=existing,
                save_failed=save_failed,
            ),
            text=f"Workflow extracted: {workflow.get('process', '')}",
        )
    except Exception as exc:
        print(f"[extract] response chat_update failed: {exc}", flush=True)

    # 9) Drift check (inline — we're already off the Slack ack thread). If
    # genuine conflicts surface, post a follow-up in the same thread so the
    # user sees them without leaving Slack.
    if workflow_id:
        try:
            from brain.drift import check_for_drift
            conflicts = check_for_drift(thread_text, workflow)
            if conflicts:
                _post_drift_followup(client, channel_id, thread_ts, conflicts)
        except Exception as exc:
            print(f"[extract] drift check failed: {exc}", flush=True)


def _post_drift_followup(
    client: WebClient,
    channel_id: str,
    thread_ts: str,
    conflicts: list[dict[str, Any]],
) -> None:
    """Post a thread reply summarising drift conflicts. Posts the highest-severity
    conflict in full + a "+N more" hint when there are several."""
    sev_rank = {"high": 0, "medium": 1, "low": 2}
    sorted_conflicts = sorted(conflicts, key=lambda c: sev_rank.get(c.get("severity"), 3))
    top = sorted_conflicts[0]
    extras = len(sorted_conflicts) - 1
    review_link = f"{FLOWITHM_URL}/brain"

    lines = [
        f"*Flowithm detected a potential conflict with an existing workflow:*",
        "",
        top.get("conflict_description") or "",
        "",
        f"*Existing:* {top.get('existing_rule') or '—'}",
        f"*New evidence:* {top.get('new_evidence') or '—'}",
    ]
    if extras > 0:
        lines.append("")
        lines.append(f"_+{extras} more conflict(s) — see Flowithm to review them all._")
    lines.append("")
    lines.append(f"Review and update in Flowithm → {review_link}")

    try:
        client.chat_postMessage(
            channel=channel_id,
            thread_ts=thread_ts,
            text="\n".join(lines),
            mrkdwn=True,
        )
    except Exception as exc:
        print(f"[drift] follow-up post failed: {exc}", flush=True)


def _post_json_snippet(
    client: WebClient,
    channel_id: str,
    thread_ts: str,
    workflow_id: str,
) -> None:
    workflow = _fetch_workflow(workflow_id)
    if not workflow:
        return
    payload = {
        k: v
        for k, v in workflow.items()
        if k not in ("id", "generated_at", "source", "source_metadata", "archived", "archived_at")
    }
    json_text = json.dumps(payload, indent=2, ensure_ascii=False)
    text = (
        "Skills file JSON — paste this into any AI agent:\n```\n" + json_text + "\n```"
    )
    try:
        client.chat_postMessage(
            channel=channel_id,
            thread_ts=thread_ts,
            text=text,
            mrkdwn=True,
        )
    except Exception as exc:
        print(f"[copy_json] chat_postMessage failed: {exc}", flush=True)


def _show_update_confirmation(
    client: WebClient,
    channel_id: str,
    message_ts: str,
    new_id: str,
    existing_id: str,
    existing_name: str,
) -> None:
    try:
        client.chat_update(
            channel=channel_id,
            ts=message_ts,
            blocks=build_update_confirmation_blocks(
                existing_name=existing_name,
                new_id=new_id,
                existing_id=existing_id,
            ),
            text=f"Update existing workflow: {existing_name}?",
        )
    except Exception as exc:
        print(f"[update_existing] chat_update failed: {exc}", flush=True)


def _perform_update(
    client: WebClient,
    channel_id: str,
    message_ts: str,
    new_id: str,
    existing_id: str,
) -> None:
    if not (new_id and existing_id):
        _show_error(client, channel_id, message_ts, "Update failed — missing workflow ids.")
        return

    # Archive the existing
    try:
        resp = requests.post(
            f"{FLOWITHM_API_URL}/workflows/{existing_id}/archive",
            headers=_api_headers(),
            timeout=10,
        )
        resp.raise_for_status()
    except Exception as exc:
        print(f"[update] archive failed: {exc}", flush=True)
        _show_error(client, channel_id, message_ts, "Couldn't archive the existing workflow.")
        return

    workflow = _fetch_workflow(new_id)
    if not workflow:
        _show_error(client, channel_id, message_ts, "Couldn't reload the new workflow.")
        return

    meta = workflow.get("source_metadata") or {}
    channel_name = meta.get("channel_name", "channel")
    message_count = meta.get("message_count", 0)
    deeplink = f"{FLOWITHM_URL}/workflow/{new_id}"

    try:
        client.chat_update(
            channel=channel_id,
            ts=message_ts,
            blocks=build_workflow_response_blocks(
                workflow=workflow,
                channel_name=channel_name,
                message_count=message_count,
                deeplink=deeplink,
                existing=None,
                updated=True,
            ),
            text=f"Workflow updated: {workflow.get('process', '')}",
        )
    except Exception as exc:
        print(f"[update] response chat_update failed: {exc}", flush=True)


def _revert_to_workflow(
    client: WebClient,
    channel_id: str,
    message_ts: str,
    workflow_id: str,
) -> None:
    workflow = _fetch_workflow(workflow_id)
    if not workflow:
        return
    meta = workflow.get("source_metadata") or {}
    channel_name = meta.get("channel_name", "channel")
    message_count = meta.get("message_count", 0)
    deeplink = f"{FLOWITHM_URL}/workflow/{workflow_id}"
    try:
        client.chat_update(
            channel=channel_id,
            ts=message_ts,
            blocks=build_workflow_response_blocks(
                workflow=workflow,
                channel_name=channel_name,
                message_count=message_count,
                deeplink=deeplink,
                existing=None,
            ),
            text=f"Workflow: {workflow.get('process', '')}",
        )
    except Exception as exc:
        print(f"[cancel_update] chat_update failed: {exc}", flush=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _show_error(client: WebClient, channel_id: str, message_ts: str, message: str) -> None:
    try:
        client.chat_update(
            channel=channel_id,
            ts=message_ts,
            blocks=build_error_blocks(message),
            text=message,
        )
    except Exception as exc:
        print(f"[error] chat_update failed: {exc}", flush=True)


def _fetch_workflow(workflow_id: str) -> dict[str, Any] | None:
    if not workflow_id:
        return None
    try:
        resp = requests.get(
            f"{FLOWITHM_API_URL}/workflows/{workflow_id}",
            headers=_api_headers(),
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        print(f"[fetch_workflow] failed: {exc}", flush=True)
        return None


def _collect_thread(client: WebClient, channel: str, thread_ts: str) -> tuple[str, int]:
    """Walk conversations.replies (paginated) and return formatted text + count."""
    cursor: str | None = None
    raw_messages: list[dict[str, Any]] = []
    while True:
        kwargs: dict[str, Any] = {"channel": channel, "ts": thread_ts, "limit": 200}
        if cursor:
            kwargs["cursor"] = cursor
        result = client.conversations_replies(**kwargs)
        raw_messages.extend(result.get("messages", []))
        if not result.get("has_more"):
            break
        cursor = (result.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            break

    user_cache: dict[str, str] = {}
    formatted: list[str] = []
    for m in raw_messages:
        author = _resolve_user_name(client, m.get("user") or "", user_cache)
        ts = _format_ts(m.get("ts") or "")
        text = (m.get("text") or "").replace("\r", "")
        formatted.append(f"{author} [{ts}]: {text}")
        for url in DOC_URL_REGEX.findall(text):
            formatted.append(f"[linked doc: {url}]")

    full = "\n".join(formatted)
    return cap_tokens(full, TOKEN_CAP, strategy="middle_out"), len(raw_messages)


def _resolve_user_name(client: WebClient, user_id: str, cache: dict[str, str]) -> str:
    if not user_id:
        return "unknown"
    # P-16: check module-level LRU first, then per-request cache.
    cached = _user_name_cache.get(user_id)
    if cached:
        return cached
    if user_id in cache:
        return cache[user_id]
    try:
        result = client.users_info(user=user_id)
        u = result.get("user") or {}
        name = (
            u.get("real_name")
            or (u.get("profile") or {}).get("display_name")
            or u.get("name")
            or user_id
        )
    except Exception:
        name = user_id
    _user_name_cache[user_id] = name
    cache[user_id] = name
    return name


def _format_ts(ts: str) -> str:
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M UTC"
        )
    except Exception:
        return ts


def _detect_process_name(thread_text: str) -> str:
    """Ask Claude for a 2-5 word process name."""
    from brain.anthropic_client import messages_create

    msg = messages_create(
        _anthropic(),
        model=CLAUDE_MODEL,
        max_tokens=64,
        messages=[
            {
                "role": "user",
                "content": (
                    "What process or workflow is being discussed in this Slack thread? "
                    "Reply with just a 2-5 word process name, nothing else.\n\n"
                    f"Thread:\n{thread_text}"
                ),
            }
        ],
    )
    text = "".join(b.text for b in msg.content if b.type == "text").strip()
    text = text.strip("\"'.“” ").strip()
    if not text:
        text = "Untitled process"
    return text[:80]
