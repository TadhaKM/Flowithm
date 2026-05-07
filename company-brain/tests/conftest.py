"""Shared pytest fixtures + import setup.

Run from the project root:
    pytest

External services (Anthropic, Voyage, Supabase, Slack) are mocked in
individual tests as needed. No live credentials required.
"""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path
from typing import Any

import pytest

# Make `brain.*`, `api.*`, `slack.*`, `ingest.*` importable without an
# editable install. Has to happen before any test module imports them.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Stub env vars so modules that read os.environ[...] at call time don't
# crash inside fixtures. Tests that exercise those code paths monkeypatch
# the actual functions; these are just safety defaults.
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-test")
os.environ.setdefault("VOYAGE_API_KEY", "test-voyage-key")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-supabase-key")
os.environ.setdefault("ADMIN_TOKEN", "test-admin-token")
# Multi-tenancy default org — matches the seed UUID in brain/schema.sql so
# helpers that fall back to _default_org_id() in tests resolve cleanly.
os.environ.setdefault("ORG_ID", "00000000-0000-0000-0000-000000000001")


# ---------------------------------------------------------------------------
# Sample data fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def org_id() -> str:
    return "00000000-0000-0000-0000-000000000001"


@pytest.fixture
def valid_api_key() -> str:
    """Plaintext key whose first 12 chars match the prefix the
    fake_api_key_row fixture below returns from find_api_keys_by_prefix."""
    return "fb_live_abcdefghijklmnopqrstuvwxyz123456"


@pytest.fixture
def sample_skill() -> dict[str, Any]:
    return {
        "process": "Customer refund handling",
        "trigger": "Customer requests refund",
        "steps": [
            {
                "step": 1,
                "action": "Check purchase date",
                "logic": "If > 30 days, deny unless VIP",
                "owner": "Support agent",
                "notes": None,
            },
            {
                "step": 2,
                "action": "Apply decision rules",
                "logic": None,
                "owner": "Support agent",
                "notes": None,
            },
        ],
        "decision_rules": [
            "If customer is VIP, always approve",
            "If defective product, full refund no questions",
        ],
        "approvals": ["Refunds over $500 need manager approval"],
        "exceptions": ["Enterprise customers always approved"],
        "sources_summary": "Extracted from Slack and Notion",
    }


@pytest.fixture
def sample_chunks() -> list:
    """Two Chunks worth of fake source material — Slack thread + Notion page."""
    from brain.ingestors import Chunk
    return [
        Chunk(
            source_type="slack",
            source_name="customer-success",
            content=(
                "Sarah: we approved the refund for Acme Corp this week, "
                "they're an enterprise customer so always approve regardless "
                "of the standard 30 day window."
            ),
            metadata={"channel": "customer-success", "author": "Sarah", "timestamp": "100.0"},
        ),
        Chunk(
            source_type="notion",
            source_name="Refund Policy",
            content=(
                "Standard refund window is 30 days from the date of purchase. "
                "Enterprise customers are exempt from the 30 day window and "
                "may request a refund at any time."
            ),
            metadata={"page_title": "Refund Policy", "section": "Refund Policy"},
        ),
    ]


# ---------------------------------------------------------------------------
# Voyage / Anthropic / Supabase helpers
# ---------------------------------------------------------------------------

def _deterministic_vector(text: str, dim: int = 1024) -> list[float]:
    """Same text → same vector. SHA-256 → repeated to fill the vector.
    Numerically meaningless, but stable, normalised, and free."""
    h = hashlib.sha256(text.encode("utf-8")).digest()
    seed = [(b - 128) / 128.0 for b in h]  # 32 floats in [-1, 1)
    out = []
    while len(out) < dim:
        out.extend(seed)
    out = out[:dim]
    # L2-normalise so cosine similarity behaves sensibly.
    norm = sum(x * x for x in out) ** 0.5 or 1.0
    return [x / norm for x in out]


@pytest.fixture
def mock_voyage(monkeypatch):
    """Patches brain.embedder so every embedding call returns a deterministic
    1024-dim vector keyed off the input text. Same input → same vector,
    cosine-similarity-friendly.

    Returns a dict {"call_count": N} so tests can assert how many times
    the underlying client was hit.
    """
    counter = {"get_embedding": 0, "get_embeddings_batch": 0}

    def fake_get_embedding(text: str) -> list[float]:
        if not text:
            raise ValueError("get_embedding: text is empty")
        counter["get_embedding"] += 1
        return _deterministic_vector(text)

    def fake_get_embeddings_batch(texts, batch_size=20):
        counter["get_embeddings_batch"] += 1
        return [_deterministic_vector(t) for t in texts]

    def fake_embed_query(text: str) -> list[float]:
        return _deterministic_vector(text)

    monkeypatch.setattr("brain.embedder.get_embedding", fake_get_embedding)
    monkeypatch.setattr("brain.embedder.get_embeddings_batch", fake_get_embeddings_batch)
    monkeypatch.setattr("brain.embedder.embed_query", fake_embed_query)
    # Patch local bindings in modules that did `from brain.embedder import …`.
    monkeypatch.setattr("brain.drift.get_embedding", fake_get_embedding, raising=False)
    monkeypatch.setattr("brain.drift.get_embeddings_batch", fake_get_embeddings_batch, raising=False)
    monkeypatch.setattr("brain.query.embed_query", fake_embed_query, raising=False)
    monkeypatch.setattr("api.agent.get_embedding", fake_get_embedding, raising=False)
    return counter


# Module-scoped Supabase fake — controllable from tests via the rows_by_table dict.

class _FakeQuery:
    """Chainable fake of the postgrest builder. Records the operation chain
    on the parent client so assertions can inspect what happened."""

    def __init__(self, client: "_FakeClient", table: str):
        self.client = client
        self.table = table
        self._filters: list[tuple] = []
        self._action: str | None = None
        self._payload: Any = None
        self._select_cols: str | None = None
        self._count_mode: str | None = None
        self._head_only: bool = False
        self._limit: int | None = None
        self._range: tuple[int, int] | None = None
        self._order_by: tuple | None = None

    # --- builder methods (return self) ---
    def select(self, cols="*", count=None, head=False):
        self._select_cols = cols
        self._count_mode = count
        self._head_only = head
        return self

    def insert(self, payload):
        self._action, self._payload = "insert", payload
        return self

    def update(self, payload):
        self._action, self._payload = "update", payload
        return self

    def upsert(self, payload, on_conflict=None):
        self._action, self._payload = "upsert", payload
        return self

    def delete(self):
        self._action = "delete"
        return self

    def eq(self, col, val):
        self._filters.append(("eq", col, val))
        return self

    def neq(self, col, val):
        self._filters.append(("neq", col, val))
        return self

    def gte(self, col, val):
        self._filters.append(("gte", col, val))
        return self

    def lt(self, col, val):
        self._filters.append(("lt", col, val))
        return self

    def in_(self, col, val):
        self._filters.append(("in", col, val))
        return self

    def ilike(self, col, val):
        self._filters.append(("ilike", col, val))
        return self

    def is_(self, col, val):
        self._filters.append(("is", col, val))
        return self

    @property
    def not_(self):
        # supabase-py uses .not_.is_("col", "null") style.
        return _FakeNotProxy(self)

    def order(self, col, desc=False):
        self._order_by = (col, desc)
        return self

    def limit(self, n):
        self._limit = n
        return self

    def range(self, start, end):
        self._range = (start, end)
        return self

    def single(self):
        self._limit = 1
        return self

    def execute(self):
        # Record this call.
        self.client.calls.append({
            "table": self.table,
            "action": self._action or "select",
            "payload": self._payload,
            "filters": list(self._filters),
        })
        rows = self.client.rows_by_table.get(self.table, [])
        # Apply filters (best-effort).
        rows = [r for r in rows if _row_matches(r, self._filters)]
        # For count="exact" we still need .count populated.
        result = type("Result", (), {})()
        if self._action == "insert":
            payload = self._payload if isinstance(self._payload, list) else [self._payload]
            inserted = []
            for p in payload:
                row = dict(p)
                row.setdefault("id", f"fake-id-{len(self.client.rows_by_table.get(self.table, []))}")
                self.client.rows_by_table.setdefault(self.table, []).append(row)
                inserted.append(row)
            result.data = inserted
            result.count = None
        elif self._action == "update":
            updated = []
            for r in rows:
                r.update(self._payload or {})
                updated.append(r)
            result.data = updated
            result.count = None
        elif self._action == "upsert":
            payload = self._payload if isinstance(self._payload, list) else [self._payload]
            upserted = []
            for p in payload:
                row = dict(p)
                row.setdefault("id", f"fake-id-{len(self.client.rows_by_table.get(self.table, []))}")
                self.client.rows_by_table.setdefault(self.table, []).append(row)
                upserted.append(row)
            result.data = upserted
            result.count = None
        elif self._action == "delete":
            self.client.rows_by_table[self.table] = [
                r for r in self.client.rows_by_table.get(self.table, [])
                if not _row_matches(r, self._filters)
            ]
            result.data = rows
            result.count = None
        else:
            # select
            if self._head_only:
                result.data = []
            else:
                result.data = rows[: self._limit] if self._limit else rows
            result.count = len(rows) if self._count_mode == "exact" else None
        return result


class _FakeNotProxy:
    def __init__(self, query: _FakeQuery):
        self.query = query

    def is_(self, col, val):
        self.query._filters.append(("not_is", col, val))
        return self.query


def _row_matches(row: dict, filters: list[tuple]) -> bool:
    for op, col, val in filters:
        rv = row.get(col)
        if op == "eq" and rv != val: return False
        if op == "neq" and rv == val: return False
        if op == "gte" and (rv is None or rv < val): return False
        if op == "lt" and (rv is None or rv >= val): return False
        if op == "in" and rv not in val: return False
        if op == "ilike" and (not isinstance(rv, str) or val.lower() != rv.lower()):
            return False
        if op == "is" and val == "null" and rv is not None: return False
        if op == "not_is" and val == "null" and rv is None: return False
    return True


class _FakeClient:
    def __init__(self, rows_by_table: dict[str, list[dict]] | None = None):
        self.rows_by_table = rows_by_table or {}
        self.calls: list[dict] = []

    def table(self, name: str):
        return _FakeQuery(self, name)

    def rpc(self, name: str, args: dict | None = None):
        # RPC calls return a stub query whose execute() reads from
        # rpc_results[name] (settable by tests).
        client = self
        rpc_name = name
        rpc_args = args or {}

        class _RpcQuery:
            def execute(_self):
                client.calls.append({"action": "rpc", "name": rpc_name, "args": rpc_args})
                result = type("Result", (), {})()
                result.data = client.__dict__.get("rpc_results", {}).get(rpc_name, [])
                result.count = None
                return result

        return _RpcQuery()


@pytest.fixture
def mock_supabase(monkeypatch):
    """Returns a controllable fake Supabase client. Tests populate
    `client.rows_by_table['skills'] = [{...}]` before calling production
    code, then read `client.calls` afterwards to assert what happened.

    Modules that did `from brain.store import get_client` at module top
    bound the symbol locally, so we patch every such binding by name —
    a plain monkeypatch on `brain.store.get_client` wouldn't reach them.
    """
    fake = _FakeClient()
    factory = lambda: fake  # noqa: E731
    monkeypatch.setattr("brain.store.get_client", factory)
    monkeypatch.setattr("brain.embedder._supabase_client", factory)
    monkeypatch.setattr("brain.staleness.get_client", factory, raising=False)
    monkeypatch.setattr("brain.drift.get_client", factory, raising=False)
    return fake


@pytest.fixture
def mock_anthropic(monkeypatch):
    """Patches brain.anthropic_client.messages_create so every call returns
    a fake Message whose .content[0].text is settable per-test.

    Tests do `mock_anthropic['response_text'] = '{"conflicts": []}'` to
    control what the next call returns. `mock_anthropic['calls']` records
    every invocation for assertions.
    """
    state: dict[str, Any] = {
        "response_text": "{}",
        "stop_reason": "end_turn",
        "calls": [],
    }

    class _FakeBlock:
        def __init__(self, text: str):
            self.text = text
            self.type = "text"

    class _FakeMessage:
        def __init__(self, text: str, stop_reason: str):
            self.content = [_FakeBlock(text)]
            self.stop_reason = stop_reason

    def fake_messages_create(client, **kwargs):
        state["calls"].append(kwargs)
        return _FakeMessage(state["response_text"], state["stop_reason"])

    # Patch the wrapper everywhere it's imported. Each module did a
    # local `from brain.anthropic_client import messages_create`, so we
    # patch each binding individually.
    monkeypatch.setattr("brain.anthropic_client.messages_create", fake_messages_create)
    monkeypatch.setattr("brain.drift.messages_create", fake_messages_create, raising=False)
    monkeypatch.setattr("brain.query.messages_create", fake_messages_create, raising=False)
    return state


@pytest.fixture
def test_client(monkeypatch):
    """FastAPI TestClient with the global API + cached chunk count stubbed
    so /health probes don't try to reach Supabase by default."""
    from fastapi.testclient import TestClient
    from api.main import app
    return TestClient(app)
