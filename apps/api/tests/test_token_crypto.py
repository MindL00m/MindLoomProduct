"""Tests for Fernet OAuth token encryption."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from core.config import get_settings
from core.token_crypto import (
    ENC_PREFIX,
    decrypt_token,
    encrypt_token,
    reset_crypto_warning,
)


@pytest.fixture(autouse=True)
def _clear(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("POSTGRES_URL", "postgresql+asyncpg://u:p@localhost/db")
    monkeypatch.setenv("NEO4J_URI", "bolt://localhost:7687")
    monkeypatch.setenv("NEO4J_USERNAME", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", "password")
    get_settings.cache_clear()
    reset_crypto_warning()
    yield
    get_settings.cache_clear()
    reset_crypto_warning()


def test_encrypt_decrypt_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", key)
    get_settings.cache_clear()

    cipher = encrypt_token("secret-access-token")
    assert cipher is not None
    assert cipher.startswith(ENC_PREFIX)
    assert decrypt_token(cipher) == "secret-access-token"


def test_legacy_plaintext_read(monkeypatch: pytest.MonkeyPatch) -> None:
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", key)
    get_settings.cache_clear()

    assert decrypt_token("legacy-plaintext-token") == "legacy-plaintext-token"


def test_skip_encryption_when_key_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "")
    get_settings.cache_clear()
    assert encrypt_token("plain") == "plain"
