"""Supabase vector store interface."""
import os
from typing import Any

from dotenv import load_dotenv
from supabase import Client, create_client

load_dotenv()

TABLE = "chunks"
MATCH_FN = "match_chunks"


def get_client() -> Client:
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_KEY"]
    return create_client(url, key)


def upsert_chunks(chunks: list[dict[str, Any]]) -> None:
    client = get_client()
    client.table(TABLE).insert(chunks).execute()


def similarity_search(query_embedding: list[float], k: int = 5) -> list[dict[str, Any]]:
    client = get_client()
    result = client.rpc(
        MATCH_FN,
        {"query_embedding": query_embedding, "match_count": k},
    ).execute()
    return result.data or []


def count_chunks() -> int:
    """Number of rows in the chunks table.

    `head=True` skips returning the rows; `count="exact"` requests an exact
    server-side count via the Prefer header.
    """
    client = get_client()
    result = client.table(TABLE).select("*", count="exact", head=True).execute()
    return result.count or 0
