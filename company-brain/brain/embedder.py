"""Embed chunks via Voyage AI and upsert them into Supabase.

Public surface:
    embed_and_store(chunks) -- ingest path: embed documents in batches, upsert
    embed_query(text)       -- query path:  embed a single user query
"""
import os
import time
from typing import Any

import voyageai
import voyageai.error
from dotenv import load_dotenv
from supabase import Client, create_client

load_dotenv()

MODEL = "voyage-3"
BATCH_SIZE = 20
TABLE = "chunks"
MAX_RETRIES = 6
INITIAL_BACKOFF_SECONDS = 1.0


def _voyage_client() -> voyageai.Client:
    return voyageai.Client(api_key=os.environ["VOYAGE_API_KEY"])


def _supabase_client() -> Client:
    return create_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_SERVICE_KEY"],
    )


def _embed_with_retry(
    client: voyageai.Client, texts: list[str], input_type: str
) -> list[list[float]]:
    """Embed texts with exponential backoff on rate-limit errors.

    Voyage 429s arrive as voyageai.error.RateLimitError. Other errors
    (auth, invalid request, server error) propagate immediately — they're
    not transient and retrying would just delay the failure.
    """
    backoff = INITIAL_BACKOFF_SECONDS
    for attempt in range(MAX_RETRIES):
        try:
            result = client.embed(texts, model=MODEL, input_type=input_type)
            return result.embeddings
        except voyageai.error.RateLimitError:
            if attempt == MAX_RETRIES - 1:
                raise
            time.sleep(backoff)
            backoff *= 2
    raise RuntimeError("unreachable")


def embed_and_store(chunks: list[dict[str, Any]]) -> int:
    """Embed each chunk's content and upsert chunk+embedding into Supabase.

    Returns the number of chunks stored.
    """
    if not chunks:
        print("No chunks to embed.")
        return 0

    voyage = _voyage_client()
    supabase = _supabase_client()
    total = len(chunks)
    stored = 0

    for start in range(0, total, BATCH_SIZE):
        batch = chunks[start : start + BATCH_SIZE]
        texts = [c["content"] for c in batch]
        embeddings = _embed_with_retry(voyage, texts, input_type="document")

        rows = [
            {
                "source_type": c["source_type"],
                "source_name": c["source_name"],
                "content": c["content"],
                "metadata": c.get("metadata", {}),
                "embedding": embedding,
            }
            for c, embedding in zip(batch, embeddings)
        ]
        supabase.table(TABLE).upsert(rows).execute()

        stored += len(batch)
        print(f"Embedded and stored {stored}/{total} chunks...")

    return stored


def embed_query(text: str) -> list[float]:
    """Embed a single user query for similarity search.

    Voyage uses input_type='query' at retrieval time (vs 'document' for
    stored content) — the two are trained as an asymmetric pair so the
    distinction matters for recall.
    """
    voyage = _voyage_client()
    return _embed_with_retry(voyage, [text], input_type="query")[0]
