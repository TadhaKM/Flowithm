"""Intercom conversation ingestor.

Pulls closed conversations from Intercom — optionally filtered by tag —
and converts each into a Chunk. Edge cases / exceptions / escalations
are the highest-value source for discovering undocumented process rules,
so default behaviour favours longer threads (configurable min_message_count).

Two modes:
  Demo:  IntercomIngestor().build_chunks([])      -> []  (no live fetch)
  Live:  IntercomIngestor(access_token=..., tags=[...]).process(None)
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from brain.ingestors import BaseIngestor, Chunk
from brain.text_utils import cap_tokens

logger = logging.getLogger("flowithm.ingest_intercom")

INTERCOM_BASE = "https://api.intercom.io"
INTERCOM_VERSION = "2.10"

# Crude but effective HTML stripper for Intercom message bodies. We pass
# display_as=plaintext on the GET, but the search response returns raw
# bodies, so we sanitise defensively.
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(s: str) -> str:
    if not s:
        return ""
    return _HTML_TAG_RE.sub("", s).strip()


class IntercomIngestor(BaseIngestor):
    def __init__(
        self,
        access_token: str | None = None,
        since: datetime | None = None,
        tags: list[str] | None = None,
        min_message_count: int = 3,
    ) -> None:
        self.token = access_token
        self.since = since
        self.tags = tags or None
        self.min_message_count = max(1, int(min_message_count))

    def build_chunks(self, raw_data: Any = None) -> list[Chunk]:
        if not self.token:
            return []
        try:
            conversations = self._fetch_conversations()
        except Exception as exc:
            logger.error("Intercom fetch failed: %s", exc)
            raise

        chunks: list[Chunk] = []
        for conv in conversations:
            chunk = self._conversation_to_chunk(conv)
            if chunk:
                chunks.append(chunk)
        return chunks

    # ------------------------------------------------------------------
    # Fetch
    # ------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
            "Intercom-Version": INTERCOM_VERSION,
            "content-type": "application/json",
        }

    def _build_query(self) -> dict[str, Any]:
        q: dict[str, Any] = {"field": "state", "operator": "=", "value": "closed"}
        if self.since:
            q = {
                "operator": "AND",
                "value": [
                    q,
                    {
                        "field": "updated_at",
                        "operator": ">",
                        "value": int(self.since.timestamp()),
                    },
                ],
            }
        if self.tags:
            tag_queries = [
                {"field": "tag.name", "operator": "=", "value": t} for t in self.tags
            ]
            q = {
                "operator": "AND",
                "value": [
                    q,
                    {"operator": "OR", "value": tag_queries},
                ],
            }
        return q

    def _fetch_conversations(self) -> list[dict]:
        # Lazy import — requests is already a project dep but we keep the
        # import close to use to make the test/demo path obvious.
        import requests

        query = self._build_query()
        out: list[dict] = []
        next_page: str | None = None

        while True:
            payload: dict[str, Any] = {
                "query": query,
                "pagination": {"per_page": 50},
            }
            if next_page:
                payload["pagination"]["starting_after"] = next_page

            resp = requests.post(
                f"{INTERCOM_BASE}/conversations/search",
                headers=self._headers(),
                json=payload,
                timeout=20,
            )
            if resp.status_code == 429:
                import time as _time
                wait = min(float(resp.headers.get("Retry-After", 5)), 30)
                _time.sleep(wait)
                continue
            if resp.status_code in (401, 403):
                raise RuntimeError(f"Intercom auth failed ({resp.status_code}) — token may be revoked")
            resp.raise_for_status()
            data = resp.json()

            for stub in data.get("conversations", []) or []:
                cid = stub.get("id")
                if not cid:
                    continue
                try:
                    full = requests.get(
                        f"{INTERCOM_BASE}/conversations/{cid}",
                        headers=self._headers(),
                        params={"display_as": "plaintext"},
                        timeout=20,
                    )
                    full.raise_for_status()
                    out.append(full.json())
                except Exception as exc:
                    logger.warning("Intercom conv %s fetch failed: %s", cid, exc)

            cursor = (data.get("pages") or {}).get("next") or {}
            next_page = cursor.get("starting_after") if cursor else None
            if not next_page:
                break

        return out

    # ------------------------------------------------------------------
    # Conversion
    # ------------------------------------------------------------------

    def _conversation_to_chunk(self, conv: dict) -> Chunk | None:
        parts = ((conv.get("conversation_parts") or {}).get("conversation_parts") or [])
        if len(parts) < self.min_message_count:
            return None

        lines: list[str] = []

        opener_body = _strip_html((conv.get("source") or {}).get("body") or "")
        if opener_body:
            lines.append(f"Customer: {opener_body}")

        for part in parts:
            body = _strip_html(part.get("body") or "")
            if not body:
                continue
            author = part.get("author") or {}
            atype = author.get("type") or "unknown"
            name = author.get("name") or atype
            label = "Support agent" if atype == "admin" else "Customer"
            lines.append(f"{label} ({name}): {body}")

        if not lines:
            return None

        tags = [t.get("name") for t in ((conv.get("tags") or {}).get("tags") or []) if t.get("name")]
        content = "\n\n".join(lines)
        # Resolution sits at the end of support threads — middle_out keeps
        # both the original ask and the closing decision.
        content = cap_tokens(content, self.MAX_CHUNK_TOKENS, strategy="middle_out")

        cid = conv.get("id") or "unknown"
        return Chunk(
            source_type="intercom",
            source_name=f"Support: {cid}",
            content=content,
            metadata={
                "conversation_id": cid,
                "tags": tags,
                "created_at": conv.get("created_at"),
                "updated_at": conv.get("updated_at"),
                "assignee": (conv.get("assignee") or {}).get("name") or "unassigned",
            },
        )
