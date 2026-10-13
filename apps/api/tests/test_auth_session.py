"""Unit tests for Google ID-token auth and MindLoom access JWTs."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from keeper import session_tokens
from core.config import get_settings
from keeper.session_tokens import (
    AccessTokenClaims,
    GoogleIdentity,
    decode_access_token,
    issue_access_token,
    verify_google_id_token,
)


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_issue_and_decode_access_token(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("POSTGRES_URL", "postgresql+asyncpg://u:p@localhost/db")
    monkeypatch.setenv("NEO4J_URI", "bolt://localhost:7687")
    monkeypatch.setenv("NEO4J_USERNAME", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", "password")
    monkeypatch.setenv("SESSION_SECRET", "unit-test-session-secret")
    get_settings.cache_clear()

    token = issue_access_token(
        org_id="org-1",
        user_id="user-1",
        role="admin",
        email="admin@acme.com",
    )
    claims = decode_access_token(token)
    assert claims == AccessTokenClaims(
        user_id="user-1",
        org_id="org-1",
        role="admin",
        email="admin@acme.com",
    )


def test_decode_access_token_rejects_forged(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("POSTGRES_URL", "postgresql+asyncpg://u:p@localhost/db")
    monkeypatch.setenv("NEO4J_URI", "bolt://localhost:7687")
    monkeypatch.setenv("NEO4J_USERNAME", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", "password")
    monkeypatch.setenv("SESSION_SECRET", "unit-test-session-secret")
    get_settings.cache_clear()

    with pytest.raises(HTTPException) as exc:
        decode_access_token("not.a.real.jwt")
    assert exc.value.status_code == 401


def test_verify_google_id_token_success(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("POSTGRES_URL", "postgresql+asyncpg://u:p@localhost/db")
    monkeypatch.setenv("NEO4J_URI", "bolt://localhost:7687")
    monkeypatch.setenv("NEO4J_USERNAME", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", "password")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "client.apps.googleusercontent.com")
    get_settings.cache_clear()

    monkeypatch.setattr(
        session_tokens.google_id_token,
        "verify_oauth2_token",
        lambda token, request, audience: {
            "sub": "google-sub-1",
            "email": "Admin@Acme.com",
            "email_verified": True,
            "name": "Ada",
            "picture": "https://example.com/a.png",
        },
    )
    identity = verify_google_id_token("fake-id-token")
    assert identity == GoogleIdentity(
        sub="google-sub-1",
        email="admin@acme.com",
        name="Ada",
        picture="https://example.com/a.png",
    )


def test_verify_google_id_token_rejects_invalid(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("POSTGRES_URL", "postgresql+asyncpg://u:p@localhost/db")
    monkeypatch.setenv("NEO4J_URI", "bolt://localhost:7687")
    monkeypatch.setenv("NEO4J_USERNAME", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", "password")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "client.apps.googleusercontent.com")
    get_settings.cache_clear()

    def _raise(*_args, **_kwargs):
        raise ValueError("bad token")

    monkeypatch.setattr(session_tokens.google_id_token, "verify_oauth2_token", _raise)
    with pytest.raises(HTTPException) as exc:
        verify_google_id_token("bad")
    assert exc.value.status_code == 401


def test_dev_integrations_allowed_only_in_development(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("POSTGRES_URL", "postgresql+asyncpg://u:p@localhost/db")
    monkeypatch.setenv("NEO4J_URI", "bolt://localhost:7687")
    monkeypatch.setenv("NEO4J_USERNAME", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", "password")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ALLOW_DEV_INTEGRATIONS", "true")
    monkeypatch.setenv("SESSION_SECRET", "prod-secret")
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.dev_integrations_allowed is False

    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("ALLOW_DEV_INTEGRATIONS", "true")
    get_settings.cache_clear()
    assert get_settings().dev_integrations_allowed is True


def test_assert_dev_integrations_allowed_blocks_production(monkeypatch):
    from weaver.integrations import assert_dev_integrations_allowed

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("POSTGRES_URL", "postgresql+asyncpg://u:p@localhost/db")
    monkeypatch.setenv("NEO4J_URI", "bolt://localhost:7687")
    monkeypatch.setenv("NEO4J_USERNAME", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", "password")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ALLOW_DEV_INTEGRATIONS", "true")
    monkeypatch.setenv("SESSION_SECRET", "prod-secret")
    get_settings.cache_clear()

    with pytest.raises(HTTPException) as exc:
        assert_dev_integrations_allowed()
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_google_signin_persists_google_sub(monkeypatch):
    from keeper.auth import google_signin
    from keeper import auth as auth_mod

    identity = GoogleIdentity(
        sub="sub-99",
        email="member@acme.com",
        name="Mem Ber",
        picture=None,
    )
    org = SimpleNamespace(org_id="org-1", name="Acme")
    user = SimpleNamespace(
        user_id="user-1",
        email="member@acme.com",
        name="Mem Ber",
        photo_url=None,
        role="member",
    )
    monkeypatch.setattr(auth_mod, "get_org_by_domain", AsyncMock(return_value=org))
    upsert = AsyncMock(return_value=user)
    monkeypatch.setattr(auth_mod, "upsert_user", upsert)
    monkeypatch.setattr(
        auth_mod,
        "issue_access_token",
        lambda **kwargs: "signed-token",
    )

    session = await google_signin(identity=identity)
    assert session.access_token == "signed-token"
    assert session.org_id == "org-1"
    upsert.assert_awaited_once()
    assert upsert.await_args.kwargs["google_sub"] == "sub-99"


@pytest.mark.asyncio
async def test_require_user_context_rejects_missing_bearer():
    from keeper.auth import require_user_context

    request = MagicMock()
    request.headers = {}
    with pytest.raises(HTTPException) as exc:
        await require_user_context(request)
    assert exc.value.status_code == 401
    assert "access token" in exc.value.detail.lower()
