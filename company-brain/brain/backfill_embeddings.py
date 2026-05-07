"""Embed every existing skills row that's missing a summary_embedding.

Run once after the 1024-dim summary_embedding column ships — without this
the agent /skills/match endpoint returns 404 for older skills. Idempotent
and safe to re-run; only rows where summary_embedding is null are touched.

Usage:
    python -m brain.backfill_embeddings
"""
from __future__ import annotations

from dotenv import load_dotenv

from brain.embedder import get_embeddings_batch
from brain.query import _build_skill_summary_text
from brain.store import _row_to_workflow, get_client, update_skill_summary_embedding

load_dotenv()


def main() -> None:
    client = get_client()
    result = (
        client.table("skills")
        .select("*")
        .is_("summary_embedding", "null")
        .execute()
    )
    rows = result.data or []
    if not rows:
        print("All skills already have summary embeddings. Nothing to do.")
        return

    workflows = [_row_to_workflow(r) for r in rows]
    summaries = [_build_skill_summary_text(w) for w in workflows]
    pairs = [(r, s) for r, s in zip(rows, summaries) if s]
    if not pairs:
        print(f"{len(rows)} skill row(s) had no embeddable text — nothing to embed.")
        return

    print(f"Embedding {len(pairs)} skill(s)...")
    vectors = get_embeddings_batch([s for _, s in pairs])
    for (row, _), vec in zip(pairs, vectors):
        update_skill_summary_embedding(str(row["id"]), vec)
    print(f"OK: backfilled {len(pairs)} summary embedding(s).")


if __name__ == "__main__":
    main()
