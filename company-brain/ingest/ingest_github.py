"""Ingest github_issues.json into chunk dicts.

Each issue (title + body + all comments) becomes one chunk.

Standalone for now: prints chunks to stdout as JSON, count to stderr.
"""
import json
import sys
from pathlib import Path

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


def build_chunks(issues: list[dict]) -> list[dict]:
    chunks = []
    for issue in issues:
        chunks.append({
            "source_type": "github",
            "source_name": f"#{issue['number']}: {issue['title']}",
            "content": format_issue(issue),
            "metadata": {
                "number": issue["number"],
                "labels": issue.get("labels", []),
                "state": issue["state"],
            },
        })
    return chunks


def main() -> None:
    issues = load_issues(DEMO_PATH)
    chunks = build_chunks(issues)
    print(json.dumps(chunks, indent=2))
    print(f"Produced {len(chunks)} chunks.", file=sys.stderr)


if __name__ == "__main__":
    main()
