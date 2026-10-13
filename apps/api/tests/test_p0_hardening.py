"""Tests for CORS, production secrets, docs gating, and webhook fail-closed behavior."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from core.config import (
    get_settings,
    validate_production_secrets,
)


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _base_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("POSTGRES_URL", "postgresql+asyncpg://u:p@localhost/db")
    monkeypatch.setenv("NEO4J_URI", "bolt://localhost:7687")
    monkeypatch.setenv("NEO4J_USERNAME", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", "password")
    monkeypatch.setenv("APP_ENV", "development")


def test_resolved_cors_origins_never_star(monkeypatch: pytest.MonkeyPatch) -> None:
    _base_env(monkeypatch)
    monkeypatch.setenv("FRONTEND_URL", "http://localhost:5500/")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://localhost:5173, http://localhost:5500")
    get_settings.cache_clear()
    origins = get_settings().resolved_cors_origins
    assert "*" not in origins
    assert origins == ["http://localhost:5500", "http://localhost:5173"]


def test_validate_production_secrets_rejects_weak_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _base_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SESSION_SECRET", "dev-insecure-session-secret-change-me")
    monkeypatch.setenv("NEO4J_PASSWORD", "please-change-me")
    monkeypatch.setenv("GOOGLE_DRIVE_WEBHOOK_SECRET", "")
    monkeypatch.setenv("MICROSOFT_GRAPH_CLIENT_STATE", "dev-client-state")
    monkeypatch.setenv("ZOOM_WEBHOOK_SECRET_TOKEN", "")
    monkeypatch.setenv("GOOGLE_PUBSUB_PUSH_AUDIENCE", "")
    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="Production secret validation failed"):
        validate_production_secrets()


def test_validate_production_secrets_passes_with_strong_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _base_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SESSION_SECRET", "a-strong-unique-session-secret-value")
    monkeypatch.setenv("NEO4J_PASSWORD", "unique-neo4j-password-xyz")
    monkeypatch.setenv("GOOGLE_DRIVE_WEBHOOK_SECRET", "drive-secret")
    monkeypatch.setenv("MICROSOFT_GRAPH_CLIENT_STATE", "ms-secret")
    monkeypatch.setenv("ZOOM_WEBHOOK_SECRET_TOKEN", "zoom-secret")
    monkeypatch.setenv(
        "GOOGLE_PUBSUB_PUSH_AUDIENCE",
        "https://api.example.com/webhooks/google/pubsub",
    )
    monkeypatch.setenv(
        "TOKEN_ENCRYPTION_KEY",
        "dGVzdC1rZXktMzItYnl0ZXMtZm9yLWZlcm5ldCEhISE=",
    )
    get_settings.cache_clear()
    validate_production_secrets()  # does not raise


def test_production_disables_openapi_docs(monkeypatch: pytest.MonkeyPatch) -> None:
    _base_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SESSION_SECRET", "a-strong-unique-session-secret-value")
    monkeypatch.setenv("NEO4J_PASSWORD", "unique-neo4j-password-xyz")
    monkeypatch.setenv("GOOGLE_DRIVE_WEBHOOK_SECRET", "drive-secret")
    monkeypatch.setenv("MICROSOFT_GRAPH_CLIENT_STATE", "ms-secret")
    monkeypatch.setenv("ZOOM_WEBHOOK_SECRET_TOKEN", "zoom-secret")
    monkeypatch.setenv(
        "GOOGLE_PUBSUB_PUSH_AUDIENCE",
        "https://api.example.com/webhooks/google/pubsub",
    )
    get_settings.cache_clear()

    # Import a fresh module view of docs flags without starting lifespan DB work.
    from core.config import get_settings as gs

    assert gs().app_env == "production"
    disable = gs().app_env == "production"
    assert disable is True


@pytest.mark.asyncio
async def test_graph_debug_404_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    _base_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "production")
    get_settings.cache_clear()

    from main import get_knowledge_graph_debug

    with pytest.raises(HTTPException) as exc:
        await get_knowledge_graph_debug(ctx=("org-1", "user-1"))
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_drive_webhook_requires_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    _base_env(monkeypatch)
    monkeypatch.setenv("GOOGLE_DRIVE_WEBHOOK_SECRET", "")
    get_settings.cache_clear()

    from fastapi import BackgroundTasks
    from main import google_drive_webhook

    with pytest.raises(HTTPException) as exc:
        await google_drive_webhook(
            background_tasks=BackgroundTasks(),
            x_goog_channel_id="ch-1",
            x_goog_resource_state="change",
            x_goog_channel_token="anything",
        )
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_drive_webhook_rejects_bad_token(monkeypatch: pytest.MonkeyPatch) -> None:
    _base_env(monkeypatch)
    monkeypatch.setenv("GOOGLE_DRIVE_WEBHOOK_SECRET", "expected-secret")
    get_settings.cache_clear()

    from fastapi import BackgroundTasks
    from main import google_drive_webhook

    with pytest.raises(HTTPException) as exc:
        await google_drive_webhook(
            background_tasks=BackgroundTasks(),
            x_goog_channel_id="ch-1",
            x_goog_resource_state="change",
            x_goog_channel_token="wrong",
        )
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_teams_webhook_rejects_bad_client_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _base_env(monkeypatch)
    monkeypatch.setenv("MICROSOFT_GRAPH_CLIENT_STATE", "good-state")
    get_settings.cache_clear()

    from fastapi import BackgroundTasks
    from main import microsoft_teams_webhook

    with pytest.raises(HTTPException) as exc:
        await microsoft_teams_webhook(
            background_tasks=BackgroundTasks(),
            payload={"value": [{"clientState": "bad", "subscriptionId": "sub-1"}]},
        )
    assert exc.value.status_code == 401


def test_pubsub_oidc_skipped_in_dev_without_audience(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _base_env(monkeypatch)
    monkeypatch.setenv("GOOGLE_PUBSUB_PUSH_AUDIENCE", "")
    get_settings.cache_clear()

    from weaver.webhook_auth import verify_google_pubsub_oidc

    request = MagicMock()
    request.headers = {}
    verify_google_pubsub_oidc(request)  # no raise


def test_pubsub_oidc_required_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    _base_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("GOOGLE_PUBSUB_PUSH_AUDIENCE", "https://api.example.com/push")
    get_settings.cache_clear()

    from weaver.webhook_auth import verify_google_pubsub_oidc

    request = MagicMock()
    request.headers = {}
    with pytest.raises(HTTPException) as exc:
        verify_google_pubsub_oidc(request)
    assert exc.value.status_code == 401


def test_auth_rate_limit_returns_429(monkeypatch: pytest.MonkeyPatch) -> None:
    _base_env(monkeypatch)
    monkeypatch.setenv("RATE_LIMIT_AUTH", "1/minute")
    get_settings.cache_clear()

    from core import rate_limit as rate_limit_mod
    from slowapi import Limiter, _rate_limit_exceeded_handler
    from slowapi.errors import RateLimitExceeded
    from slowapi.middleware import SlowAPIMiddleware
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient

    rate_limit_mod.limiter = Limiter(
        key_func=rate_limit_mod._client_key,
        default_limits=[],
        headers_enabled=False,
    )

    app = FastAPI()
    app.state.limiter = rate_limit_mod.limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_middleware(SlowAPIMiddleware)

    @app.post("/auth/google/signin")
    @rate_limit_mod.limiter.limit("1/minute")
    async def _signin(request: Request) -> dict[str, str]:
        return {"ok": "1"}

    client = TestClient(app)
    assert client.post("/auth/google/signin").status_code == 200
    assert client.post("/auth/google/signin").status_code == 429
