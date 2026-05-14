"""Ingest github_issues.json into Chunks.

Each issue (title + body + all comments) becomes one chunk; oversized issues
are truncated via cap_tokens.
"""
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

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
    def __init__(self, token: str | None = None) -> None:
        # token is only used by validate_connection() — the demo build_chunks
        # path reads from a static JSON export and needs no auth.
        self.token = token

    def validate_connection(self) -> dict[str, Any]:
        """One cheap GET /user call to confirm the token works.
        Returns {"valid": bool, "error": str | None}."""
        if not self.token:
            return {"valid": False, "error": "No token provided."}
        import requests

        try:
            resp = requests.get(
                "https://api.github.com/user",
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Accept": "application/vnd.github+json",
                },
                timeout=10,
            )
        except requests.RequestException as exc:
            return {"valid": False, "error": f"Could not reach GitHub: {exc}"}
        if resp.status_code == 200:
            return {"valid": True, "error": None}
        if resp.status_code == 401:
            return {"valid": False, "error": "Invalid or revoked GitHub token."}
        if resp.status_code == 403:
            return {"valid": False, "error": "GitHub token is rate-limited or lacks scope."}
        return {"valid": False, "error": f"GitHub returned HTTP {resp.status_code}."}

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
