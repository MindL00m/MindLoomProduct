"""Unit tests for shared connector OAuth helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from core.config import get_settings
from weaver.integrations import _oauth_states, _pop_oauth_state, _store_oauth_state


@pytest.fixture(autouse=True)
def _clear_settings():
    get_settings.cache_clear()
    _oauth_states.clear()
    yield
    get_settings.cache_clear()
    _oauth_states.clear()


def _base_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("POSTGRES_URL", "postgresql+asyncpg://u:p@localhost/db")
    monkeypatch.setenv("NEO4J_URI", "bolt://localhost:7687")
    monkeypatch.setenv("NEO4J_USERNAME", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", "password")
    monkeypatch.setenv("APP_ENV", "development")


def test_oauth_state_round_trip_via_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    _base_env(monkeypatch)
    get_settings.cache_clear()

    store: dict[str, str] = {}
    fake = MagicMock()

    def setex(key: str, _ttl: int, value: str) -> None:
        store[key] = value

    def getdel(key: str) -> str | None:
        return store.pop(key, None)

    fake.setex.side_effect = setex
    fake.getdel.side_effect = getdel

    monkeypatch.setattr("core.redis_client.get_redis_sync", lambda: fake)

    state = _store_oauth_state("org-1", "user-1")
    org_id, user_id = _pop_oauth_state(state)
    assert org_id == "org-1"
    assert user_id == "user-1"
    assert f"loom:oauth_state:{state}" not in store


def test_oauth_state_expired_memory_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    _base_env(monkeypatch)
    get_settings.cache_clear()

    # Force Redis failure so we use the in-memory dict.
    monkeypatch.setattr(
        "core.redis_client.get_redis_sync",
        MagicMock(side_effect=ConnectionError("redis down")),
    )

    state = _store_oauth_state("org-1", "user-1")
    _oauth_states[state] = (
        "org-1",
        "user-1",
        datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    with pytest.raises(Exception) as exc:
        _pop_oauth_state(state)
    assert "expired" in str(exc.value.detail).lower()


def test_oauth_state_missing_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _base_env(monkeypatch)
    get_settings.cache_clear()

    store: dict[str, str] = {}
    fake = MagicMock()
    fake.setex.side_effect = lambda k, t, v: store.__setitem__(k, v)
    fake.getdel.side_effect = lambda k: store.pop(k, None)
    monkeypatch.setattr("core.redis_client.get_redis_sync", lambda: fake)

    with pytest.raises(Exception) as exc:
        _pop_oauth_state("does-not-exist")
    assert "invalid or expired" in str(exc.value.detail).lower()

