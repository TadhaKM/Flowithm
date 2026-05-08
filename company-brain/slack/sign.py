"""HMAC signing for Slack action_value blobs.

Slack interactive button payloads are user-replayable: a workspace
member can copy a button's `value` from the rendered message, edit it,
and submit a forged interaction. Without authentication, an action
handler that trusts the value blob (parses ids out of it and runs a
mutation) lets that user target arbitrary records.

`sign_action(payload)` packs the payload as `<b64body>.<b64sig>`; the
matching `verify_action(blob)` returns the payload or None on any
signature mismatch / parse failure. Compact (well under Slack's 2000-
char value limit), URL-safe, no external deps.

Signing key is `FLOWITHM_ACTION_SECRET` if set, otherwise `ADMIN_TOKEN`
(already required for the FastAPI admin gate, so no new env required).
L-4: `iat` is now stamped into the payload and blobs older than 24h are
rejected.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any

ACTION_TTL_SECONDS = 86400  # 24 hours


def _key() -> bytes:
    secret = (
        os.environ.get("FLOWITHM_ACTION_SECRET")
        or os.environ.get("ADMIN_TOKEN")
        or ""
    )
    if not secret:
        # Fail closed — refuse to sign or verify when no secret is set.
        # Production deploys without ADMIN_TOKEN are already broken in
        # other places; this just makes the failure obvious.
        raise RuntimeError(
            "No signing secret available. Set FLOWITHM_ACTION_SECRET or "
            "ADMIN_TOKEN before issuing or verifying Slack action values."
        )
    return secret.encode("utf-8")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def sign_action(payload: Any) -> str:
    """Return a signed-blob suitable for the Slack button `value` field.
    Stamps iat (issued-at) into the payload for TTL enforcement on verify."""
    # Wrap the payload with an iat timestamp for TTL.
    wrapped = {"p": payload, "iat": int(time.time())}
    body = json.dumps(wrapped, separators=(",", ":")).encode("utf-8")
    sig = hmac.new(_key(), body, hashlib.sha256).digest()
    return f"{_b64(body)}.{_b64(sig)}"


def verify_action(blob: str | None) -> Any | None:
    """Verify a signed blob; return parsed payload or None on failure."""
    if not blob or "." not in blob:
        return None
    try:
        body_b64, sig_b64 = blob.split(".", 1)
        body = _b64decode(body_b64)
        sig = _b64decode(sig_b64)
        expected = hmac.new(_key(), body, hashlib.sha256).digest()
        if not hmac.compare_digest(sig, expected):
            return None
        parsed = json.loads(body)
        # L-4: reject blobs older than ACTION_TTL_SECONDS.
        if isinstance(parsed, dict) and "iat" in parsed:
            if time.time() - parsed["iat"] > ACTION_TTL_SECONDS:
                return None
            return parsed.get("p")
        # Backward compat: blobs signed before the TTL change have no iat.
        return parsed
    except Exception:
        return None
