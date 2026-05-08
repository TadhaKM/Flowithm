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
from brain.logger import get_logger

load_dotenv()

log = get_logger("flowithm.embedder")

MODEL = "voyage-3"
TABLE = "chunks"
DEFAULT_BATCH_SIZE = 20
MAX_RETRIES = 3
INITIAL_BACKOFF_SECONDS = 1.0


_vc: voyageai.Client | None = None
_sc: Client | None = None


def _voyage_client() -> voyageai.Client:
    global _vc
    if _vc is None:
        _vc = voyageai.Client(api_key=os.environ["VOYAGE_API_KEY"], timeout=30)
    return _vc


def _supabase_client() -> Client:
    global _sc
    if _sc is None:
        _sc = create_client(
            os.environ["SUPABASE_URL"],
            os.environ["SUPABASE_SERVICE_KEY"],
        )
    return _sc


def _embed_with_retry(
    client: voyageai.Client, texts: list[str], input_type: str
) -> list[list[float]]:
    """Call Voyage with exponential backoff on transient errors.

    Retries on: RateLimitError (429), ConnectionError, TimeoutError,
    and server errors (5xx wrapped as generic exceptions). Auth and
    invalid-request errors propagate immediately.
    """
    backoff = INITIAL_BACKOFF_SECONDS
    last_exc: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            return client.embed(texts, model=MODEL, input_type=input_type).embeddings
        except voyageai.error.RateLimitError as exc:
            last_exc = exc
            # Honor Retry-After header if present
            retry_after = getattr(exc, "retry_after", None)
            wait = float(retry_after) if retry_after else backoff
        except (ConnectionError, TimeoutError, OSError) as exc:
            last_exc = exc
            wait = backoff
        except Exception as exc:
            # Catch server-side 5xx that the SDK may wrap generically
            err_str = str(exc).lower()
            if "500" in err_str or "502" in err_str or "503" in err_str or "504" in err_str:
                last_exc = exc
                wait = backoff
            else:
                raise
        else:
            break  # unreachable, but for clarity
        if attempt == MAX_RETRIES - 1:
            break
        log.warning("Voyage transient error, retrying",
                    extra={"attempt": attempt + 1, "wait": wait, "error": str(last_exc)})
        time.sleep(wait)
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
            # doesn't sink the whole batch. M-17: per-text try/except
            # appends a zero-vector on failure instead of aborting.
            vectors = []
            for t in batch:
                try:
                    vectors.append(get_embedding(t))
                except Exception as exc:
                    log.error("single-text embed failed, using zero-vector",
                              extra={"error": str(exc), "text_len": len(t)})
                    vectors.append([0.0] * 1024)
        out.extend(vectors)
        log.info("embedding progress", extra={"done": len(out), "total": total})

    return out


# ---------------------------------------------------------------------------
# Public: storage
# ---------------------------------------------------------------------------

def chunk_exists(content_hash: str, org_id: str | None = None) -> bool:
    """True iff a chunk with this content_hash exists for this org."""
    from brain.store import _default_org_id

    q = (
        _supabase_client()
        .table(TABLE)
        .select("id")
        .eq("content_hash", content_hash)
        .eq("org_id", org_id or _default_org_id())
        .limit(1)
    )
    result = q.execute()
    return len(result.data or []) > 0


def store_chunk(chunk: Chunk, embedding: list[float], org_id: str | None = None) -> str:
    """Upsert one Chunk + embedding into Supabase. Returns the row's uuid."""
    from brain.store import _default_org_id  # avoid circular at module load

    content_hash = hashlib.sha256(chunk.content.encode("utf-8")).hexdigest()
    row = {
        "source_type": chunk.source_type,
        "source_name": chunk.source_name,
        "content": chunk.content,
        "metadata": chunk.metadata or {},
        "embedding": embedding,
        "content_hash": content_hash,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "org_id": org_id or _default_org_id(),
    }
    try:
        result = (
            _supabase_client()
            .table(TABLE)
            .upsert(row, on_conflict="org_id,content_hash")
            .execute()
        )
    except Exception as exc:
        raise RuntimeError(f"Supabase upsert failed: {exc}") from exc

    rows = result.data or []
    if not rows:
        raise RuntimeError("Supabase upsert returned no row")
    return str(rows[0].get("id") or "")


def embed_and_store_batch(
    chunks: list[Chunk], org_id: str | None = None
) -> tuple[int, int, list[Chunk]]:
    """Batch embed + store. Returns (new_count, skipped_count, newly_embedded).

    1. Compute content_hashes for all chunks.
    2. Bulk-check which already exist (1 DB call).
    3. Embed all new chunks in batch (batched Voyage calls).
    4. Upsert new chunks (1 DB call).
    """
    if not chunks:
        return 0, 0, []

    from brain.store import _default_org_id

    org = org_id or _default_org_id()
    hashes = [
        hashlib.sha256(c.content.encode("utf-8")).hexdigest() for c in chunks
    ]

    # Bulk dedup — one DB round-trip instead of N.
    existing: set[str] = set()
    # PostgREST .in_() has a practical URL-length limit; batch in groups.
    for i in range(0, len(hashes), 500):
        batch_hashes = hashes[i : i + 500]
        resp = (
            _supabase_client()
            .table(TABLE)
            .select("content_hash")
            .eq("org_id", org)
            .in_("content_hash", batch_hashes)
            .execute()
        )
        existing.update(r["content_hash"] for r in (resp.data or []))

    new_indices = [i for i, h in enumerate(hashes) if h not in existing]
    skipped = len(chunks) - len(new_indices)

    if not new_indices:
        return 0, skipped, []

    # Batch embed — batched Voyage calls instead of N individual ones.
    new_texts = [chunks[i].content for i in new_indices]
    embeddings = get_embeddings_batch(new_texts)

    # Build rows and upsert.
    rows = []
    for idx, emb in zip(new_indices, embeddings):
        chunk = chunks[idx]
        rows.append({
            "source_type": chunk.source_type,
            "source_name": chunk.source_name,
            "content": chunk.content,
            "metadata": chunk.metadata or {},
            "embedding": emb,
            "content_hash": hashes[idx],
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "org_id": org,
        })

    try:
        _supabase_client().table(TABLE).upsert(
            rows, on_conflict="org_id,content_hash"
        ).execute()
    except Exception as exc:
        raise RuntimeError(f"Supabase batch upsert failed: {exc}") from exc

    newly_embedded = [chunks[i] for i in new_indices]
    log.info("batch embed+store", extra={
        "new": len(new_indices), "skipped": skipped, "org_id": org,
    })
    return len(new_indices), skipped, newly_embedded


def embed_and_store(chunk: Chunk, org_id: str | None = None) -> str | None:
    """Convenience wrapper: embed `chunk.content`, store, return uuid.

    Skips Voyage + Supabase entirely if the content is already stored
    (matched by SHA-256 of chunk.content). Returns None in that case.
    Note that dedup is GLOBAL on content_hash — two orgs ingesting the
    same Slack message both surface the same row. Acceptable for now;
    revisit if cross-org content-leakage becomes a concern.
    """
    content_hash = hashlib.sha256(chunk.content.encode("utf-8")).hexdigest()
    if chunk_exists(content_hash, org_id=org_id):
        log.info("skipped duplicate chunk", extra={"source_name": chunk.source_name})
        return None
    embedding = get_embedding(chunk.content)
    return store_chunk(chunk, embedding, org_id=org_id)


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
