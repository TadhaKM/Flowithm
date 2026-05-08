"""Tests for brain/crypto.py — AES-256-GCM config encryption."""
import os
import pytest
from brain.crypto import decrypt_config, encrypt_config

# A valid 32-byte key (64 hex chars) for tests.
TEST_KEY = "a" * 64


@pytest.fixture(autouse=True)
def _set_key(monkeypatch):
    monkeypatch.setenv("ENCRYPTION_KEY", TEST_KEY)


# -- round-trip ---------------------------------------------------------

def test_round_trip_simple():
    cfg = {"bot_token": "xoxb-secret", "channel_ids": ["C1", "C2"]}
    encrypted = encrypt_config(cfg)
    assert "_enc" in encrypted
    assert "bot_token" not in encrypted
    assert decrypt_config(encrypted) == cfg


def test_round_trip_nested():
    cfg = {
        "credentials_json": {"client_id": "x", "client_secret": "y"},
        "label_filters": ["INBOX"],
    }
    assert decrypt_config(encrypt_config(cfg)) == cfg


def test_round_trip_empty_dict():
    assert decrypt_config(encrypt_config({})) == {}


# -- backward compat (plaintext pass-through) ---------------------------

def test_plaintext_passthrough():
    """Legacy rows without _enc should pass through unchanged."""
    legacy = {"bot_token": "xoxb-old", "channel_ids": ["C1"]}
    assert decrypt_config(legacy) == legacy


def test_none_passthrough():
    assert decrypt_config(None) == {}


def test_non_dict_passthrough():
    assert decrypt_config("not a dict") == {}  # type: ignore[arg-type]


# -- unique nonces -------------------------------------------------------

def test_nonces_differ():
    cfg = {"token": "abc"}
    a = encrypt_config(cfg)
    b = encrypt_config(cfg)
    assert a["_enc"] != b["_enc"], "each encrypt must use a unique nonce"


# -- key validation ------------------------------------------------------

def test_missing_key_raises(monkeypatch):
    monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ENCRYPTION_KEY is not set"):
        encrypt_config({"x": 1})


def test_wrong_length_key_raises(monkeypatch):
    monkeypatch.setenv("ENCRYPTION_KEY", "abcd")  # 2 bytes, not 32
    with pytest.raises(RuntimeError, match="32 bytes"):
        encrypt_config({"x": 1})


# -- tamper detection ----------------------------------------------------

def test_tampered_ciphertext_raises():
    cfg = {"secret": "value"}
    enc = encrypt_config(cfg)
    # Flip a character in the middle of the base64 blob
    blob = enc["_enc"]
    mid = len(blob) // 2
    flipped = chr(ord(blob[mid]) ^ 0x01)
    enc["_enc"] = blob[:mid] + flipped + blob[mid + 1:]
    with pytest.raises(Exception):
        decrypt_config(enc)


def test_wrong_key_raises(monkeypatch):
    cfg = {"token": "abc"}
    enc = encrypt_config(cfg)
    monkeypatch.setenv("ENCRYPTION_KEY", "b" * 64)  # different key
    with pytest.raises(Exception):
        decrypt_config(enc)
