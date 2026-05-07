"""End-to-end ingest: load every source, dedupe, embed in batch, store in Supabase."""
import hashlib

from dotenv import load_dotenv

from brain.embedder import chunk_exists, get_embeddings_batch, store_chunk
from ingest.ingest_github import DEMO_PATH as GITHUB_PATH
from ingest.ingest_github import GitHubIngestor, load_issues
from ingest.ingest_notion import DEMO_PATH as NOTION_PATH
from ingest.ingest_notion import NotionIngestor
from ingest.ingest_slack import DEMO_PATH as SLACK_PATH
from ingest.ingest_slack import SlackIngestor, load_messages

load_dotenv()


def main() -> None:
    slack_chunks = SlackIngestor().process(load_messages(SLACK_PATH))
    notion_chunks = NotionIngestor().process(NOTION_PATH.read_text(encoding="utf-8"))
    github_chunks = GitHubIngestor().process(load_issues(GITHUB_PATH))

    all_chunks = slack_chunks + notion_chunks + github_chunks

    # Skip already-embedded content before paying for Voyage calls.
    new_chunks = []
    for c in all_chunks:
        h = hashlib.sha256(c.content.encode("utf-8")).hexdigest()
        if chunk_exists(h):
            print(f"[embedder] Skipping duplicate chunk: {c.source_name}")
        else:
            new_chunks.append(c)

    embeddings = get_embeddings_batch([c.content for c in new_chunks])
    ids = [store_chunk(c, e) for c, e in zip(new_chunks, embeddings)]
    skipped = len(all_chunks) - len(new_chunks)

    print(
        f"\nFlowithm ingested: "
        f"{len(slack_chunks)} slack chunks, "
        f"{len(notion_chunks)} notion chunks, "
        f"{len(github_chunks)} github chunks "
        f"({len(ids)} new, {skipped} duplicates skipped)"
    )


if __name__ == "__main__":
    main()
