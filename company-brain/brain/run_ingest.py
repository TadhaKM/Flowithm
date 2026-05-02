"""End-to-end ingest: load every source, embed everything, store in Supabase."""
from dotenv import load_dotenv

from brain.embedder import embed_and_store
from ingest import ingest_github, ingest_notion, ingest_slack

load_dotenv()


def main() -> None:
    slack_chunks = ingest_slack.build_chunks(
        ingest_slack.load_messages(ingest_slack.DEMO_PATH)
    )
    notion_chunks = ingest_notion.build_chunks(
        ingest_notion.DEMO_PATH.read_text(encoding="utf-8")
    )
    github_chunks = ingest_github.build_chunks(
        ingest_github.load_issues(ingest_github.DEMO_PATH)
    )

    all_chunks = slack_chunks + notion_chunks + github_chunks
    embed_and_store(all_chunks)

    print(
        f"\nCompany Brain ingested: "
        f"{len(slack_chunks)} slack chunks, "
        f"{len(notion_chunks)} notion chunks, "
        f"{len(github_chunks)} github chunks"
    )


if __name__ == "__main__":
    main()
