"""Embed text via Voyage AI and persist chunks into Supabase.

Public surface:
    get_embedding(text)              -- single text → vector
    get_embeddings_batch(texts)      -- many texts → vectors, batched + progress
    chunk_exists(content_hash)       -- precheck: is this content already stored?
    store_chunk(chunk, embedding)    -- single Chunk + vector → stored uuid
    embed_and_store(chunk)           -- convenience wrapper (single chunk, dedups)
    embed_query(text)                -- query-side embedding (asymmetric pair)
"""
import hashlib
import os
import time
from datetime import datetime, timezone

import voyageai
import voyageai.error
from dotenv import load_dotenv
from supabase import Client, create_client

from brain.ingestors import Chunk

load_dotenv()

MODEL = "voyage-3"
TABLE = "chunks"
DEFAULT_BATCH_SIZE = 20
MAX_RETRIES = 3
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
    """Call Voyage with exponential backoff on rate-limit errors.

    Voyage 429s arrive as voyageai.error.RateLimitError. Other errors
    (auth, invalid request, server error) propagate immediately — they're
    not transient and retrying would just delay the failure.
    """
    backoff = INITIAL_BACKOFF_SECONDS
    last_exc: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            return client.embed(texts, model=MODEL, input_type=input_type).embeddings
        except voyageai.error.RateLimitError as exc:
            last_exc = exc
            if attempt == MAX_RETRIES - 1:
                break
            time.sleep(backoff)
            backoff *= 2
    raise RuntimeError(f"Voyage embedding failed after {MAX_RETRIES} retries") from last_exc


# ---------------------------------------------------------------------------
# Public: embedding
# ---------------------------------------------------------------------------

def get_embedding(text: str) -> list[float]:
    """Single text → embedding vector. No side effects.

    Raises ValueError if text is empty.
    Raises RuntimeError if the Voyage call fails after MAX_RETRIES.
    """
    if not text:
        raise ValueError("get_embedding: text is empty")
    return _embed_with_retry(_voyage_client(), [text], input_type="document")[0]


def get_embeddings_batch(
    texts: list[str], batch_size: int = DEFAULT_BATCH_SIZE
) -> list[list[float]]:
    """Embed many texts at once, batched for throughput.

    If a batch call fails terminally, falls back to embedding each text in
    that batch individually (which themselves retry MAX_RETRIES times).
    Returns vectors in the same order as `texts`.
    """
    if not texts:
        return []

    client = _voyage_client()
    total = len(texts)
    out: list[list[float]] = []

    for start in range(0, total, batch_size):
        batch = texts[start : start + batch_size]
        try:
            vectors = _embed_with_retry(client, batch, input_type="document")
        except RuntimeError:
            # Batch failed — retry each text individually so one bad text
            # doesn't sink the whole batch.
            vectors = [get_embedding(t) for t in batch]
        out.extend(vectors)
        print(f"Embedding {len(out)}/{total}...")

    return out


# ---------------------------------------------------------------------------
# Public: storage
# ---------------------------------------------------------------------------

def chunk_exists(content_hash: str) -> bool:
    """True iff a chunk with this content_hash is already in Supabase."""
    result = (
        _supabase_client()
        .table(TABLE)
        .select("id")
        .eq("content_hash", content_hash)
        .limit(1)
        .execute()
    )
    return len(result.data or []) > 0


def store_chunk(chunk: Chunk, embedding: list[float]) -> str:
    """Upsert one Chunk + embedding into Supabase. Returns the row's uuid.

    De-duplicates on a SHA-256 of the content: re-ingesting identical text
    updates source_name + metadata + updated_at on the existing row instead
    of inserting a duplicate (chunks_content_hash_idx is the unique key).

    Raises RuntimeError if the Supabase write fails or returns no row.
    """
    content_hash = hashlib.sha256(chunk.content.encode("utf-8")).hexdigest()
    row = {
        "source_type": chunk.source_type,
        "source_name": chunk.source_name,
        "content": chunk.content,
        "metadata": chunk.metadata or {},
        "embedding": embedding,
        "content_hash": content_hash,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        result = (
            _supabase_client()
            .table(TABLE)
            .upsert(row, on_conflict="content_hash")
            .execute()
        )
    except Exception as exc:
        raise RuntimeError(f"Supabase upsert failed: {exc}") from exc

    rows = result.data or []
    if not rows:
        raise RuntimeError("Supabase upsert returned no row")
    return str(rows[0].get("id") or "")


def embed_and_store(chunk: Chunk) -> str | None:
    """Convenience wrapper: embed `chunk.content`, store, return uuid.

    Skips Voyage + Supabase entirely if the content is already stored
    (matched by SHA-256 of chunk.content). Returns None in that case.
    """
    content_hash = hashlib.sha256(chunk.content.encode("utf-8")).hexdigest()
    if chunk_exists(content_hash):
        print(f"[embedder] Skipping duplicate chunk: {chunk.source_name}")
        return None
    embedding = get_embedding(chunk.content)
    return store_chunk(chunk, embedding)


# ---------------------------------------------------------------------------
# Query-side
# ---------------------------------------------------------------------------

def embed_query(text: str) -> list[float]:
    """Embed a single user query for similarity search.

    Voyage uses input_type='query' at retrieval time (vs 'document' for
    stored content) — the two are trained as an asymmetric pair so the
    distinction matters for recall.
    """
    return _embed_with_retry(_voyage_client(), [text], input_type="query")[0]
