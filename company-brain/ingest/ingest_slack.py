"""Ingest Slack messages → Chunks.

Two modes:
  Demo:  SlackIngestor().build_chunks(messages_loaded_from_json)
  Live:  SlackIngestor(token=..., channel_ids=[...], since=datetime).process(None)

Live mode hits Slack Web API conversations.history with `oldest=since.timestamp()`
per channel, paginates, and pulls thread replies for any message that has a
`thread_ts`. Falls back to demo behaviour when token is None.
"""
from __future__ import annotations

import json
import sys
import time
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from brain.chunker import chunk_text
from brain.ingestors import BaseIngestor, Chunk

DEMO_PATH = Path(__file__).resolve().parent.parent / "demo-data" / "slack_export.json"


def load_messages(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def group_threads(messages: list[dict]) -> list[list[dict]]:
    """Bucket messages by thread. Standalone messages key on their own ts."""
    by_key: dict[str, list[dict]] = defaultdict(list)
    for m in messages:
        key = m.get("thread_ts") or m["ts"]
        by_key[key].append(m)

    groups = list(by_key.values())
    for g in groups:
        g.sort(key=lambda m: m["ts"])
    groups.sort(key=lambda g: g[0]["ts"])
    return groups


def format_message(m: dict) -> str:
    return f"#{m['channel']} | {m['user']}: {m['text']}"


class SlackIngestor(BaseIngestor):
    """All constructor params optional — keeps the demo path test-friendly."""

    def __init__(
        self,
        token: str | None = None,
        channel_ids: list[str] | None = None,
        since: datetime | None = None,
    ) -> None:
        self.token = token
        self.channel_ids = channel_ids or []
        self.since = since

    def validate_connection(self) -> dict[str, Any]:
        """One cheap auth.test call to confirm the bot token is live.
        Returns {"valid": bool, "error": str | None}."""
        if not self.token:
            return {"valid": False, "error": "No bot token provided."}
        try:
            from slack_sdk.web import WebClient
            from slack_sdk.errors import SlackApiError
        except ImportError:
            return {"valid": False, "error": "slack_sdk is not installed on the server."}
        try:
            resp = WebClient(token=self.token, timeout=10).auth_test()
            if resp.get("ok"):
                return {"valid": True, "error": None}
            return {"valid": False, "error": f"Slack rejected the token: {resp.get('error', 'unknown')}"}
        except SlackApiError as exc:
            code = exc.response.get("error", "unknown") if exc.response else "unknown"
            friendly = {
                "invalid_auth": "Invalid bot token.",
                "token_revoked": "This bot token has been revoked.",
                "token_expired": "This bot token has expired.",
                "account_inactive": "The Slack workspace or bot account is inactive.",
            }.get(code, f"Slack auth failed: {code}")
            return {"valid": False, "error": friendly}
        except Exception as exc:
            return {"valid": False, "error": f"Could not reach Slack: {exc}"}

    def build_chunks(self, messages: list[dict] | None) -> list[Chunk]:
        # Live mode: token + channel_ids set, messages was passed as None
        # by the scheduler. Fetch first, then chunk.
        if messages is None:
            if not self.token or not self.channel_ids:
                # No token AND no caller-provided messages — nothing to do.
                # Treat as empty rather than raise, so an empty channel list
                # in connected_sources doesn't sink the whole cycle.
                return []
            messages = self._fetch_via_api()

        chunks: list[Chunk] = []
        for thread in group_threads(messages):
            parent = thread[0]
            is_thread = len(thread) > 1
            content = "\n".join(format_message(m) for m in thread)

            metadata = {
                "channel": parent["channel"],
                "author": parent["user"],
                "timestamp": parent["ts"],
            }
            if is_thread:
                metadata["thread_ts"] = parent["ts"]

            for piece in chunk_text(content, max_tokens=self.MAX_CHUNK_TOKENS):
                chunks.append(Chunk(
                    source_type="slack",
                    source_name=parent["channel"],
                    content=piece,
                    metadata=dict(metadata),
                ))
        return chunks

    # ------------------------------------------------------------------
    # Live API fetch — only imported when token is set.
    # ------------------------------------------------------------------

    def _fetch_via_api(self) -> list[dict]:
        # Lazy import keeps slack_sdk optional for the demo + tests.
        from slack_sdk.web import WebClient
        from slack_sdk.errors import SlackApiError

        client = WebClient(token=self.token)
        oldest = f"{self.since.timestamp()}" if self.since else "0"

        import logging

        logger = logging.getLogger("flowithm.ingest_slack")
        all_messages: list[dict] = []
        for channel_id in self.channel_ids:
            # H-7: catch per-channel so one bad channel doesn't abort the rest.
            try:
                channel_name = self._resolve_channel_name(client, channel_id)
                cursor: str | None = None
                while True:
                    resp = client.conversations_history(
                        channel=channel_id,
                        oldest=oldest,
                        limit=200,
                        cursor=cursor,
                    )
                    for raw in resp.get("messages", []) or []:
                        if raw.get("subtype") in {"channel_join", "channel_leave", "bot_message"}:
                            continue
                        all_messages.append(_normalise(raw, channel_name))
                        if raw.get("reply_count") and raw.get("thread_ts") == raw.get("ts"):
                            all_messages.extend(self._fetch_thread(client, channel_id, channel_name, raw["ts"]))

                    cursor = (resp.get("response_metadata") or {}).get("next_cursor")
                    if not cursor:
                        break
                    time.sleep(0.5)
            except SlackApiError as exc:
                error_code = exc.response.get("error", "unknown") if exc.response else "unknown"
                if error_code in ("invalid_auth", "token_revoked", "account_inactive"):
                    raise  # H-11: propagate auth failures to deactivate the source
                retry_after = exc.response.headers.get("Retry-After") if exc.response else None
                if retry_after:
                    time.sleep(min(float(retry_after), 30))
                logger.error("Slack channel %s failed, skipping: %s", channel_id, error_code)
            except Exception as exc:
                logger.error("Slack channel %s unexpected error: %s", channel_id, exc)
        return all_messages

    @staticmethod
    def _fetch_thread(client: Any, channel_id: str, channel_name: str, thread_ts: str) -> list[dict]:
        from slack_sdk.errors import SlackApiError

        out: list[dict] = []
        cursor: str | None = None
        while True:
            try:
                resp = client.conversations_replies(
                    channel=channel_id, ts=thread_ts, cursor=cursor, limit=200
                )
            except SlackApiError:
                break
            replies = resp.get("messages") or []
            # Skip the first message — it's the parent we already captured.
            for raw in replies[1:]:
                out.append(_normalise(raw, channel_name))
            cursor = (resp.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                break
        return out

    @staticmethod
    def _resolve_channel_name(client: Any, channel_id: str) -> str:
        from slack_sdk.errors import SlackApiError

        try:
            info = client.conversations_info(channel=channel_id)
            return info.get("channel", {}).get("name") or channel_id
        except SlackApiError:
            return channel_id


def _normalise(raw: dict, channel_name: str) -> dict:
    """Coerce a Slack API message into the shape the existing chunker expects."""
    return {
        "channel": channel_name,
        "user": raw.get("user") or raw.get("bot_id") or "unknown",
        "ts": raw.get("ts") or "0",
        "text": raw.get("text") or "",
        **({"thread_ts": raw["thread_ts"]} if raw.get("thread_ts") else {}),
    }


def main() -> None:
    chunks = SlackIngestor().process(load_messages(DEMO_PATH))
    print(json.dumps([asdict(c) for c in chunks], indent=2))
    print(f"Produced {len(chunks)} chunks.", file=sys.stderr)


if __name__ == "__main__":
    main()
