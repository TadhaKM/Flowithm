"""brain.text_utils — count_tokens + cap_tokens (truncate / middle_out / smart)."""
from __future__ import annotations

import pytest

from brain.text_utils import _MIDDLE_MARKER, cap_tokens, count_tokens


def test_count_tokens_basic():
    assert count_tokens("Hello world") == 2


def test_count_tokens_empty():
    assert count_tokens("") == 0


def test_count_tokens_unicode():
    # tiktoken handles non-ascii; just ensure we get a positive count.
    assert count_tokens("café crème brûlée") > 3


def test_cap_tokens_under_limit_unchanged():
    text = "the quick brown fox"
    assert cap_tokens(text, 100, strategy="truncate") == text
    assert cap_tokens(text, 100, strategy="middle_out") == text


def test_cap_tokens_truncate_drops_tail():
    body = " ".join([f"word{i}" for i in range(500)])
    out = cap_tokens(body, 50, strategy="truncate")
    assert count_tokens(out) <= 50
    assert "word0" in out
    assert "word499" not in out
    assert _MIDDLE_MARKER not in out


def test_cap_tokens_middle_out_keeps_head_and_tail():
    head = " ".join([f"head{i}" for i in range(200)])
    middle = " ".join([f"middle{i}" for i in range(2000)])
    tail = " ".join([f"tail{i}" for i in range(200)])
    body = " ".join([head, middle, tail])

    out = cap_tokens(body, 200, strategy="middle_out")
    assert _MIDDLE_MARKER in out
    assert "head0" in out
    assert "tail199" in out
    assert "middle1000" not in out


def test_cap_tokens_smart_no_call_when_under_limit(mock_anthropic):
    text = "short text under any reasonable limit"
    out = cap_tokens(text, 100, strategy="smart")
    assert out == text
    # We shouldn't have hit Claude.
    assert mock_anthropic["calls"] == []


def test_cap_tokens_smart_calls_claude_when_over_limit(mock_anthropic):
    mock_anthropic["response_text"] = "compact summary preserving rules"
    long_text = " ".join(["alpha"] * 5000)
    out = cap_tokens(long_text, 200, strategy="smart")
    # The fake Claude returns the response_text verbatim.
    assert out == "compact summary preserving rules"
    # Exactly one Claude call.
    assert len(mock_anthropic["calls"]) == 1


def test_cap_tokens_unknown_strategy_raises():
    # Need text long enough that the strategy branch actually runs —
    # under-limit text returns early without checking strategy.
    long_text = " ".join(["alpha"] * 200)
    with pytest.raises(ValueError, match="unknown strategy"):
        cap_tokens(long_text, 50, strategy="not-a-strategy")
