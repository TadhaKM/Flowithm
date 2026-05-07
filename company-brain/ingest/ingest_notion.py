"""Ingest Notion pages → Chunks.

Two modes:
  Demo:  NotionIngestor().build_chunks(markdown_string)
  Live:  NotionIngestor(token=..., page_ids=[...], since=datetime).process(None)

Live mode hits the Notion REST API (Notion-Version: 2022-06-28):
  - GET /v1/pages/{id}             → metadata + last_edited_time + title
  - GET /v1/blocks/{id}/children   → block list (recursed for has_children)
Walks the block tree → markdown → reuses parse_sections() for chunking.
Skips pages whose last_edited_time predates `since` so incremental syncs
don't re-process unchanged pages.
"""
from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from brain.ingestors import BaseIngestor, Chunk
from brain.text_utils import cap_tokens

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
# Defensive cap on recursion — toggle-inside-toggle-inside-toggle infinite
# loops shouldn't be possible but a hard limit keeps us safe.
MAX_BLOCK_DEPTH = 6

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
            # Live mode: fetch + concatenate every page's markdown into one
            # blob, then route through the existing parse_sections chunker.
            if not self.token or not self.page_ids:
                return []
            text = self._fetch_via_api()
            if not text:
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

    # ------------------------------------------------------------------
    # Live API fetch
    # ------------------------------------------------------------------

    def _fetch_via_api(self) -> str:
        # Lazy import — keeps the demo / test path requests-free.
        import requests

        session = requests.Session()
        session.headers.update({
            "Authorization": f"Bearer {self.token}",
            "Notion-Version": NOTION_VERSION,
        })

        page_blocks: list[str] = []
        for page_id in self.page_ids:
            try:
                meta = self._get_json(session, f"{NOTION_API}/pages/{page_id}")
            except requests.HTTPError as exc:
                # 404 / 401 here usually means the integration isn't shared
                # with the page; surface as a per-source error rather than
                # killing the cycle.
                raise RuntimeError(f"Notion page {page_id} fetch failed: {exc}") from exc

            edited = meta.get("last_edited_time") or ""
            if self.since and edited and not self._after(edited, self.since):
                continue  # unchanged since last sync — skip the block fetch

            title = self._extract_title(meta) or page_id
            blocks = self._fetch_block_children(session, page_id)
            md_lines = [f"# {title}", ""]
            md_lines.extend(self._walk_blocks(session, blocks, depth=0))
            page_blocks.append("\n".join(md_lines))
            time.sleep(0.34)  # ~3 req/s — Notion's per-integration rate limit

        return "\n\n".join(page_blocks)

    @staticmethod
    def _after(iso_a: str, dt_b: datetime) -> bool:
        try:
            a = datetime.fromisoformat(iso_a.replace("Z", "+00:00"))
        except Exception:
            return True  # if we can't parse, err on the side of fetching
        return a >= dt_b

    @staticmethod
    def _extract_title(page: dict) -> str:
        """Notion stores the title as a `title` rich_text array on whichever
        property is the page's primary key. Walk properties to find it."""
        props = page.get("properties") or {}
        for v in props.values():
            if isinstance(v, dict) and v.get("type") == "title":
                return _rich_text_to_markdown(v.get("title") or [])
        return ""

    @staticmethod
    def _get_json(session, url: str, params: dict | None = None) -> dict:
        resp = session.get(url, params=params or {}, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def _fetch_block_children(self, session, block_id: str) -> list[dict]:
        out: list[dict] = []
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {"page_size": 100}
            if cursor:
                params["start_cursor"] = cursor
            data = self._get_json(session, f"{NOTION_API}/blocks/{block_id}/children", params=params)
            out.extend(data.get("results") or [])
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")
        return out

    def _walk_blocks(self, session, blocks: list[dict], depth: int) -> list[str]:
        lines: list[str] = []
        for b in blocks:
            md = _block_to_markdown(b, depth)
            if md is not None:
                lines.append(md)
            if b.get("has_children") and depth < MAX_BLOCK_DEPTH:
                children = self._fetch_block_children(session, b["id"])
                lines.extend(self._walk_blocks(session, children, depth + 1))
        return lines


# ---------------------------------------------------------------------------
# Block → markdown (module-level so they're importable for tests if needed)
# ---------------------------------------------------------------------------

def _rich_text_to_markdown(rt_list: list[dict]) -> str:
    """Notion rich_text array → inline markdown with bold/italic/code/links."""
    parts: list[str] = []
    for rt in rt_list or []:
        text = rt.get("plain_text", "")
        ann = rt.get("annotations") or {}
        href = rt.get("href")
        if ann.get("code"):
            text = f"`{text}`"
        if ann.get("bold"):
            text = f"**{text}**"
        if ann.get("italic"):
            text = f"*{text}*"
        if ann.get("strikethrough"):
            text = f"~~{text}~~"
        if href:
            text = f"[{text}]({href})"
        parts.append(text)
    return "".join(parts)


def _block_to_markdown(block: dict, depth: int) -> str | None:
    """Convert one Notion block to a markdown line. Returns None for
    block types we choose to skip (child_database, unsupported, etc.)."""
    btype = block.get("type")
    indent = "  " * depth
    payload = block.get(btype) if btype else None
    if not isinstance(payload, dict):
        return None

    rich = payload.get("rich_text") or []
    md_text = _rich_text_to_markdown(rich)

    if btype == "paragraph":
        return f"{indent}{md_text}" if md_text else ""
    if btype == "heading_1":
        return f"{indent}# {md_text}"
    if btype == "heading_2":
        return f"{indent}## {md_text}"
    if btype == "heading_3":
        return f"{indent}### {md_text}"
    if btype == "bulleted_list_item":
        return f"{indent}- {md_text}"
    if btype == "numbered_list_item":
        return f"{indent}1. {md_text}"
    if btype == "to_do":
        marker = "- [x]" if payload.get("checked") else "- [ ]"
        return f"{indent}{marker} {md_text}"
    if btype == "quote":
        return f"{indent}> {md_text}"
    if btype == "callout":
        emoji = (payload.get("icon") or {}).get("emoji") or ""
        prefix = f"{emoji} " if emoji else ""
        return f"{indent}> {prefix}{md_text}"
    if btype == "toggle":
        return f"{indent}- {md_text}"  # children inline as nested list
    if btype == "code":
        lang = payload.get("language") or ""
        return f"{indent}```{lang}\n{md_text}\n{indent}```"
    if btype == "divider":
        return f"{indent}---"
    if btype == "child_page":
        return f"{indent}[{payload.get('title') or 'sub-page'}]"
    # child_database / synced_block / table / unsupported → skip
    return None


def main() -> None:
    chunks = NotionIngestor().process(DEMO_PATH.read_text(encoding="utf-8"))
    print(json.dumps([asdict(c) for c in chunks], indent=2))
    print(f"Produced {len(chunks)} chunks.", file=sys.stderr)


if __name__ == "__main__":
    main()
