"""Zoom connect-dev should refuse when OAuth credentials are configured."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from core.config import get_settings
from weaver.zoom_workspace import connect_zoom_dev


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_connect_zoom_dev_blocked_when_oauth_enabled(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("POSTGRES_URL", "postgresql+asyncpg://u:p@localhost/db")
    monkeypatch.setenv("NEO4J_URI", "bolt://localhost:7687")
    monkeypatch.setenv("NEO4J_USERNAME", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", "password")
    monkeypatch.setenv("ZOOM_CLIENT_ID", "zoom-client")
    monkeypatch.setenv("ZOOM_CLIENT_SECRET", "zoom-secret")
    get_settings.cache_clear()

    with pytest.raises(HTTPException) as exc:
        await connect_zoom_dev("org-1", "user-1", "a@b.com")
    assert exc.value.status_code == 400
    assert "oauth" in exc.value.detail.lower()
