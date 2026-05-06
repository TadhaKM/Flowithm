"""Shared pytest fixtures + import setup.

Run from the project root:
    pytest

External services (Anthropic, Voyage, Supabase, Slack) are mocked in
individual tests as needed. No live credentials required.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Make `brain.*`, `api.*`, `slack.*`, `ingest.*` importable without an
# editable install. Has to happen before any test module imports them.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Stub env vars so modules that read os.environ[...] at call time don't
# crash inside fixtures. Tests that exercise those code paths monkeypatch
# the actual functions; these are just safety defaults.
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("VOYAGE_API_KEY", "test-voyage-key")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-supabase-key")
