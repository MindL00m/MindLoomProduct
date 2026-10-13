"""Application-level Fernet encryption for OAuth tokens at rest."""

from __future__ import annotations

import logging

from cryptography.fernet import Fernet, InvalidToken

from core.config import get_settings

logger = logging.getLogger(__name__)

ENC_PREFIX = "enc:v1:"
_warned_no_key = False


def _fernet() -> Fernet | None:
    global _warned_no_key
    key = (get_settings().token_encryption_key or "").strip()
    if not key:
        if not _warned_no_key:
            logger.warning(
                "TOKEN_ENCRYPTION_KEY is unset; OAuth tokens will be stored in plaintext."
            )
            _warned_no_key = True
        return None
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_token(value: str | None) -> str | None:
    """Encrypt a token for storage. Returns plaintext when no key is configured."""

    if value is None:
        return None
    if value.startswith(ENC_PREFIX):
        return value
    fernet = _fernet()
    if fernet is None:
        return value
    return ENC_PREFIX + fernet.encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_token(value: str | None) -> str | None:
    """Decrypt a stored token. Legacy plaintext (no prefix) is returned as-is."""

    if value is None:
        return None
    if not value.startswith(ENC_PREFIX):
        return value
    fernet = _fernet()
    if fernet is None:
        raise ValueError(
            "Encrypted token found but TOKEN_ENCRYPTION_KEY is not configured."
        )
    try:
        return fernet.decrypt(value[len(ENC_PREFIX) :].encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Failed to decrypt OAuth token; check TOKEN_ENCRYPTION_KEY.") from exc


def reset_crypto_warning() -> None:
    """Reset the one-shot plaintext warning (tests)."""

    global _warned_no_key
    _warned_no_key = False
