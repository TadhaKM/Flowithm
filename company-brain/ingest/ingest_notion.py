"""Ingest notion_pages.md into chunk dicts.

Splits the file into sections at H1 and H2 boundaries (H3+ stay inside their
parent section). Each section becomes one chunk.

Standalone for now: prints chunks to stdout as JSON, count to stderr.
"""
import json
import re
import sys
from pathlib import Path

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


def build_chunks(text: str) -> list[dict]:
    chunks = []
    for s in parse_sections(text):
        if not s["body"]:
            continue
        chunks.append({
            "source_type": "notion",
            "source_name": s["heading"],
            "content": s["body"],
            "metadata": {
                "page_title": s["page_title"],
                "section": s["heading"],
            },
        })
    return chunks


def main() -> None:
    text = DEMO_PATH.read_text(encoding="utf-8")
    chunks = build_chunks(text)
    print(json.dumps(chunks, indent=2))
    print(f"Produced {len(chunks)} chunks.", file=sys.stderr)


if __name__ == "__main__":
    main()
