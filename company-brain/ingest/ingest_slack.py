"""Ingest slack_export.json into Chunks."""
import json
import sys
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path

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
    def build_chunks(self, messages: list[dict]) -> list[Chunk]:
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


def main() -> None:
    chunks = SlackIngestor().process(load_messages(DEMO_PATH))
    print(json.dumps([asdict(c) for c in chunks], indent=2))
    print(f"Produced {len(chunks)} chunks.", file=sys.stderr)


if __name__ == "__main__":
    main()
