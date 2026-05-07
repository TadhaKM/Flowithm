"""Ingest Notion pages → Chunks.

Two modes:
  Demo:  NotionIngestor().build_chunks(markdown_string)
  Live:  NotionIngestor(token=..., page_ids=[...], since=datetime).process(None)

Live mode is currently a stub — connect a Notion integration token and
implement _fetch_via_api() against the Notion REST API
(GET /v1/blocks/{id}/children, recursive block-tree → markdown).
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from brain.ingestors import BaseIngestor, Chunk
from brain.text_utils import cap_tokens

DEMO_PATH = Path(__file__).resolve().parent.parent / "demo-data" / "notion_pages.md"

HEADING_RE = re.compile(r"^(#{1,2})\s+(.+?)\s*$", re.MULTILINE)
HORIZONTAL_RULE_RE = re.compile(r"^---\s*$", re.MULTILINE)


def parse_sections(text: str) -> list[dict]:
    matches = list(HEADING_RE.finditer(text))
    sections = []
    current_h1 = None

    for i, m in enumerate(matches):
        level = len(m.group(1))
        heading = m.group(2).strip()
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[body_start:body_end]
        body = HORIZONTAL_RULE_RE.sub("", body).strip()

        if level == 1:
            current_h1 = heading

        sections.append({
            "heading": heading,
            "page_title": current_h1 or heading,
            "body": body,
        })

    return sections


class NotionIngestor(BaseIngestor):
    """All constructor params optional — keeps the demo path test-friendly."""

    def __init__(
        self,
        token: str | None = None,
        page_ids: list[str] | None = None,
        since: datetime | None = None,
    ) -> None:
        self.token = token
        self.page_ids = page_ids or []
        self.since = since

    def build_chunks(self, text: str | None) -> list[Chunk]:
        if text is None:
            # Live mode requested by the scheduler. Not implemented yet —
            # raise loudly so the cycle records it as an error in
            # ingest_runs rather than silently producing no chunks.
            if self.token or self.page_ids:
                raise NotImplementedError(
                    "Notion live fetch not implemented yet. "
                    "Connect a Notion integration token and implement "
                    "_fetch_via_api() against /v1/blocks/{id}/children."
                )
            return []

        chunks: list[Chunk] = []
        for s in parse_sections(text):
            body = cap_tokens(s["body"], self.MAX_CHUNK_TOKENS, strategy="truncate")
            chunks.append(Chunk(
                source_type="notion",
                source_name=s["heading"],
                content=body,
                metadata={
                    "page_title": s["page_title"],
                    "section": s["heading"],
                },
            ))
        return chunks


def main() -> None:
    chunks = NotionIngestor().process(DEMO_PATH.read_text(encoding="utf-8"))
    print(json.dumps([asdict(c) for c in chunks], indent=2))
    print(f"Produced {len(chunks)} chunks.", file=sys.stderr)


if __name__ == "__main__":
    main()
