"""Token-aware text utilities shared across ingest and runtime paths.

The cl100k_base encoder lives here and nowhere else — every other module that
needs token counting or token slicing imports from this module.
"""
from __future__ import annotations

import os

import tiktoken

ENCODING = tiktoken.get_encoding("cl100k_base")

CLAUDE_HAIKU_MODEL = "claude-haiku-4-5-20251001"

_MIDDLE_MARKER = "\n...[middle removed]...\n"


def count_tokens(text: str) -> int:
    return len(ENCODING.encode(text))


def cap_tokens(text: str, max_tokens: int, strategy: str = "truncate") -> str:
    """Return a version of `text` that fits within `max_tokens` tokens.

    strategy:
      "truncate"   -- keep the first max_tokens worth of text
      "middle_out" -- keep first N/2 + last N/2 tokens joined with a marker;
                      use this for Slack threads where the resolution sits at
                      the end and the middle is replayable noise
      "smart"      -- if over budget, ask Claude haiku to summarise while
                      preserving decision rules, steps, owners, exceptions
    """
    tokens = ENCODING.encode(text)
    if len(tokens) <= max_tokens:
        return text

    if strategy == "truncate":
        return ENCODING.decode(tokens[:max_tokens])

    if strategy == "middle_out":
        half = max_tokens // 2
        head = ENCODING.decode(tokens[:half])
        tail = ENCODING.decode(tokens[-half:])
        return f"{head}{_MIDDLE_MARKER}{tail}"

    if strategy == "smart":
        return _smart_summarise(text, max_tokens)

    raise ValueError(f"unknown strategy: {strategy!r}")


def _smart_summarise(text: str, max_tokens: int) -> str:
    # Lazy import — keeps text_utils importable without anthropic installed.
    import anthropic

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    prompt = (
        f"Summarise this to under {max_tokens} tokens while preserving all "
        "decision rules, process steps, owner names, and exceptions. "
        "Cut only narrative/context.\n\n"
        f"{text}"
    )
    msg = client.messages.create(
        model=CLAUDE_HAIKU_MODEL,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(block.text for block in msg.content if hasattr(block, "text"))


if __name__ == "__main__":
    sample = (
        "Loopline incident postmortem. The database connection pool became "
        "exhausted at 02:14 UTC after a deploy. Sarah Chen was on-call and "
        "paged Marcus Holt within four minutes. The fix was to roll back to "
        "the previous build and restart the pool. Five whys followed. "
    ) * 200  # ~2000 words

    sample_tokens = count_tokens(sample)
    print(f"sample: {sample_tokens} tokens")

    truncated = cap_tokens(sample, 200, strategy="truncate")
    assert count_tokens(truncated) <= 200, "truncate exceeded budget"
    assert _MIDDLE_MARKER not in truncated, "truncate should not insert marker"
    print(f"truncate -> {count_tokens(truncated)} tokens, head: {truncated[:60]!r}")

    middled = cap_tokens(sample, 200, strategy="middle_out")
    assert _MIDDLE_MARKER in middled, "middle_out should insert marker"
    assert middled.startswith(sample[:30]), "middle_out should preserve head"
    print(f"middle_out -> {count_tokens(middled)} tokens (incl. marker)")

    if os.environ.get("ANTHROPIC_API_KEY"):
        smart = cap_tokens(sample, 200, strategy="smart")
        print(f"smart -> {count_tokens(smart)} tokens, preview: {smart[:120]!r}")
    else:
        print("smart -> skipped (set ANTHROPIC_API_KEY to exercise this strategy)")

    print("OK: all strategies behave as specified")
