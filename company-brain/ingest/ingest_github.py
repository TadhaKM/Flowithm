"""Ingest github_issues.json into Chunks.

Each issue (title + body + all comments) becomes one chunk; oversized issues
are truncated via cap_tokens.
"""
import json
import sys
from dataclasses import asdict
from pathlib import Path

from brain.ingestors import BaseIngestor, Chunk
from brain.text_utils import cap_tokens

DEMO_PATH = Path(__file__).resolve().parent.parent / "demo-data" / "github_issues.json"


def load_issues(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def format_issue(issue: dict) -> str:
    parts = [
        f"Issue #{issue['number']}: {issue['title']}",
        "",
        issue.get("body", ""),
    ]
    for c in issue.get("comments", []):
        parts.append("")
        parts.append(f"Comment by {c['user']} ({c['created_at']}):")
        parts.append(c["body"])
    return "\n".join(parts)


class GitHubIngestor(BaseIngestor):
    def build_chunks(self, issues: list[dict]) -> list[Chunk]:
        chunks: list[Chunk] = []
        for issue in issues:
            content = cap_tokens(format_issue(issue), self.MAX_CHUNK_TOKENS, strategy="truncate")
            chunks.append(Chunk(
                source_type="github",
                source_name=f"#{issue['number']}: {issue['title']}",
                content=content,
                metadata={
                    "number": issue["number"],
                    "labels": issue.get("labels", []),
                    "state": issue["state"],
                },
            ))
        return chunks


def main() -> None:
    chunks = GitHubIngestor().process(load_issues(DEMO_PATH))
    print(json.dumps([asdict(c) for c in chunks], indent=2))
    print(f"Produced {len(chunks)} chunks.", file=sys.stderr)


if __name__ == "__main__":
    main()
