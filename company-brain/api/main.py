"""FastAPI backend for Company Brain: /query, /skills, /health."""
# When uvicorn loads this as `main:app` (via run.sh, with /api as the working
# directory) instead of `api.main:app` from the project root, the `brain.*`
# imports below need the project root on sys.path.
if __package__ in (None, ""):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import time
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from brain.query import generate_skills_file, query_brain
from brain.store import count_chunks

load_dotenv()

CHUNK_COUNT_TTL_SECONDS = 30.0
_chunk_count_cache: dict[str, float | int | None] = {"value": None, "expires_at": 0.0}


def _cached_chunk_count() -> int:
    """count_chunks() result, refreshed at most once per CHUNK_COUNT_TTL_SECONDS.

    Concurrent /health requests during a refresh window may each hit Supabase
    once — that's fine, the worst case is a small burst on cache miss. No lock.
    """
    now = time.monotonic()
    if _chunk_count_cache["value"] is None or now >= _chunk_count_cache["expires_at"]:
        _chunk_count_cache["value"] = count_chunks()
        _chunk_count_cache["expires_at"] = now + CHUNK_COUNT_TTL_SECONDS
    return _chunk_count_cache["value"]  # type: ignore[return-value]


class QueryRequest(BaseModel):
    question: str
    top_k: int = 6


class SkillsRequest(BaseModel):
    process_name: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    count = _cached_chunk_count()
    print(f"[Company Brain] startup — {count} chunks indexed", flush=True)
    yield


app = FastAPI(title="Company Brain API", lifespan=lifespan)

# Permissive CORS for local dev. Tighten allow_origins before deploying.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/query")
def query(req: QueryRequest) -> dict:
    return query_brain(req.question, top_k=req.top_k)


@app.post("/skills")
def skills(req: SkillsRequest) -> dict:
    return generate_skills_file(req.process_name)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "chunks_indexed": _cached_chunk_count()}
