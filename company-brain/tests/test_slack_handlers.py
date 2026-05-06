"""slack.handlers — trigger detection, helpers, regex.

Tests skipped if slack-bolt isn't installed (it's a heavy dep that's only
required to run the bot itself). All tests are pure-logic — no fake Slack
event payloads, no Bolt app construction.
"""
from __future__ import annotations

import pytest

pytest.importorskip("slack_bolt")

from slack.handlers import (  # noqa: E402
    DOC_URL_REGEX,
    MIN_WORDS,
    THREAD_WAIT_SECONDS,
    TOKEN_CAP,
    TRIGGER_REGEX,
    _cap_tokens,
    _format_ts,
    _resolve_user_name,
)


# ---------------------------------------------------------------------------
# Trigger regex — positives
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "we should add this to the runbook today",
    "hey team — needs to go in the run book",
    "how do we handle this when a customer requests a refund mid-cycle",
    "how does our oncall rotation work for the EU team",
    "how should we escalate this to legal",
    "what's the process for onboarding contractors",
    "process when a deploy fails after merging",
    "policy for refunds over $1000",
    "policy on customer data retention please review",
    "policy when an account is flagged for fraud",
    "when a customer churns we should record reason",
    "when the customer asks for an extension we should",
    "who's on-call this week for engineering",
    "oncall rotation needs to be updated",
    "the on call engineer should respond",
    "we had an incident at 2am yesterday",
    "the outage on Tuesday — postmortem due Friday",
    "post-mortem for the database issue last week",
    "escalation to legal team — please advise",
    "we should escalate this to the CTO",
    "what happens when the migration fails midway",
    "what do we do when stripe webhook fails repeatedly",
    "this is our SOP for handling vendor calls",
    "the standard operating procedure says we wait",
])
def test_trigger_regex_matches_expected_phrases(text):
    assert TRIGGER_REGEX.search(text) is not None, f"should match: {text!r}"


# ---------------------------------------------------------------------------
# Trigger regex — negatives
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "good morning everyone hope you slept well",
    "what time is the all-hands tomorrow",
    "lunch options for friday's offsite",
    "happy birthday to our newest engineer",
    "the design review went really well today",
])
def test_trigger_regex_skips_neutral_chatter(text):
    assert TRIGGER_REGEX.search(text) is None, f"should NOT match: {text!r}"


# ---------------------------------------------------------------------------
# Linked-doc URL extraction
# ---------------------------------------------------------------------------

def test_doc_url_regex_finds_notion():
    text = "see the runbook https://www.notion.so/team/Runbook-abc123 thanks"
    matches = DOC_URL_REGEX.findall(text)
    assert "https://www.notion.so/team/Runbook-abc123" in matches


def test_doc_url_regex_finds_notion_site():
    text = "details at https://team.notion.site/Outage-2026-04-14-xyz"
    matches = DOC_URL_REGEX.findall(text)
    assert any("notion.site" in m for m in matches)


def test_doc_url_regex_finds_google_doc():
    text = "spec → https://docs.google.com/document/d/1abc/edit#heading=foo"
    matches = DOC_URL_REGEX.findall(text)
    assert len(matches) == 1


def test_doc_url_regex_skips_unrelated_urls():
    assert DOC_URL_REGEX.findall("https://example.com/x https://github.com/y") == []


# ---------------------------------------------------------------------------
# Helpers — _format_ts, _cap_tokens, _resolve_user_name
# ---------------------------------------------------------------------------

def test_format_ts_returns_utc_iso_like_string():
    out = _format_ts("1714500000.000100")
    assert "UTC" in out
    # 1714500000 = 2024-04-30 in UTC
    assert "2024-04" in out


def test_format_ts_invalid_input_falls_back():
    """Bad timestamps return the original string rather than crashing."""
    assert _format_ts("not-a-number") == "not-a-number"
    assert _format_ts("") == ""


def test_cap_tokens_no_op_below_cap():
    text = "this is well under the cap"
    assert _cap_tokens(text, 1000) == text


def test_cap_tokens_truncates_with_marker_above_cap():
    long_text = " ".join(["alpha"] * 5000)  # ~5000 tokens
    out = _cap_tokens(long_text, 200)
    assert "tokens omitted" in out
    # It's still much shorter than the original.
    assert len(out) < len(long_text)


def test_cap_tokens_keeps_head_and_tail():
    """The truncation strategy is head + omission marker + tail."""
    body = " ".join([f"head{i}" for i in range(400)])
    body += " " + " ".join([f"middle{i}" for i in range(2000)])
    body += " " + " ".join([f"tail{i}" for i in range(400)])
    out = _cap_tokens(body, 400)
    assert "head0" in out  # head preserved
    assert "tail399" in out  # tail preserved
    # Middle dropped
    assert "middle1000" not in out


def test_resolve_user_name_uses_cache_first():
    cache = {"U001": "Alice"}
    # client never gets called because cache hits
    name = _resolve_user_name(client=None, user_id="U001", cache=cache)
    assert name == "Alice"


def test_resolve_user_name_handles_empty_user_id():
    name = _resolve_user_name(client=None, user_id="", cache={})
    assert name == "unknown"


# ---------------------------------------------------------------------------
# Module-level constants are stable
# ---------------------------------------------------------------------------

def test_constants_match_spec():
    assert MIN_WORDS == 20
    assert THREAD_WAIT_SECONDS == 60
    assert TOKEN_CAP == 4000
