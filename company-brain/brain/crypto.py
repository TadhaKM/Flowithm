"""AES-256-GCM encryption for connected_sources.config blobs.

Every third-party token (Slack bot_token, Notion integration_token,
Gmail credentials_json, Intercom access_token) is encrypted before
it hits the database. The key lives in the ENCRYPTION_KEY env var —
never in Supabase.

Encrypted configs are stored as ``{"_enc": "<base64(nonce ‖ ciphertext ‖ tag)>"}``
inside the existing JSONB column. The decrypt side checks for the ``_enc``
marker and passes through any legacy plaintext config unchanged, so
existing rows keep working until the next update re-encrypts them.

Generate a key:
    python -c "import secrets; print(secrets.token_hex(32))"
"""

from __future__ import annotations

import base64
import json
import os
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_ENC_MARKER = "_enc"
_NONCE_BYTES = 12


def _get_key() -> bytes:
    """Read the 256-bit key from the environment (64 hex chars)."""
    raw = os.environ.get("ENCRYPTION_KEY", "")
    if not raw:
        raise RuntimeError(
            "ENCRYPTION_KEY is not set. Generate one with:\n"
            '  python -c "import secrets; print(secrets.token_hex(32))"'
        )
    key = bytes.fromhex(raw)
    if len(key) != 32:
        raise RuntimeError(
            f"ENCRYPTION_KEY must be exactly 32 bytes (64 hex chars), got {len(key)}"
        )
    return key


def encrypt_config(config: dict[str, Any]) -> dict[str, Any]:
    """Encrypt a config dict → ``{"_enc": "<base64>"}``."""
    key = _get_key()
    aesgcm = AESGCM(key)
    nonce = os.urandom(_NONCE_BYTES)
    plaintext = json.dumps(config, separators=(",", ":")).encode("utf-8")
    ct_with_tag = aesgcm.encrypt(nonce, plaintext, None)
    blob = base64.b64encode(nonce + ct_with_tag).decode("ascii")
    return {_ENC_MARKER: blob}


def decrypt_config(config: dict[str, Any] | None) -> dict[str, Any]:
    """Decrypt ``{"_enc": "..."}`` → original dict.

    If the config is not encrypted (no ``_enc`` key), return it as-is.
    This provides backward compatibility with rows written before
    encryption was enabled — they will be encrypted on the next update.
    """
    if not isinstance(config, dict):
        return {}
    if _ENC_MARKER not in config:
        return config  # plaintext / legacy row
    key = _get_key()
    aesgcm = AESGCM(key)
    raw = base64.b64decode(config[_ENC_MARKER])
    nonce = raw[:_NONCE_BYTES]
    ct_with_tag = raw[_NONCE_BYTES:]
    plaintext = aesgcm.decrypt(nonce, ct_with_tag, None)
    return json.loads(plaintext)
