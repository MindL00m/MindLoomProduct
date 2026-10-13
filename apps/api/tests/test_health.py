"""Tests for worker heartbeat and readiness probes."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from core.durable_jobs import WORKER_HEARTBEAT_KEY, touch_worker_heartbeat
from core import health as health_mod


@pytest.mark.asyncio
async def test_touch_worker_heartbeat_sets_key_with_ttl() -> None:
    redis = AsyncMock()
    stamp = await touch_worker_heartbeat(redis)
    redis.set.assert_awaited_once()
    args, kwargs = redis.set.await_args
    assert args[0] == WORKER_HEARTBEAT_KEY
    assert args[1] == stamp
    assert kwargs.get("ex") == 60
    datetime.fromisoformat(stamp)


@pytest.mark.asyncio
async def test_check_readiness_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    session = AsyncMock()
    session.execute = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)

    class SessionCM:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *args):
            return False

    class FakeFactory:
        def __call__(self):
            return SessionCM()

    monkeypatch.setattr(health_mod, "get_session_factory", lambda: FakeFactory())

    fresh = datetime.now(timezone.utc).isoformat()
    redis = AsyncMock()
    redis.ping = AsyncMock(return_value=True)
    redis.get = AsyncMock(return_value=fresh)
    redis.aclose = AsyncMock()

    class FakeRedis:
        @staticmethod
        def from_url(*_a, **_k):
            return redis

    monkeypatch.setattr(health_mod, "Redis", FakeRedis)
    monkeypatch.setattr(
        health_mod,
        "get_settings",
        lambda: SimpleNamespace(redis_url="redis://localhost:6379/0"),
    )

    neo_result = AsyncMock()
    neo_result.single = AsyncMock(return_value={"n": 1})
    neo_session = AsyncMock()
    neo_session.run = AsyncMock(return_value=neo_result)
    neo_session.__aenter__ = AsyncMock(return_value=neo_session)
    neo_session.__aexit__ = AsyncMock(return_value=False)
    driver = MagicMock()
    driver.session = MagicMock(return_value=neo_session)
    monkeypatch.setattr(health_mod, "get_neo4j_driver", lambda: driver)

    payload = await health_mod.check_readiness()
    assert payload["status"] == "ok"
    assert payload["checks"]["postgres"] == "ok"
    assert payload["checks"]["redis"] == "ok"
    assert payload["checks"]["neo4j"] == "ok"
    assert payload["checks"]["worker"] == "ok"


@pytest.mark.asyncio
async def test_check_readiness_fails_when_redis_down(monkeypatch: pytest.MonkeyPatch) -> None:
    session = AsyncMock()
    session.execute = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)

    class SessionCM:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *args):
            return False

    class FakeFactory:
        def __call__(self):
            return SessionCM()

    monkeypatch.setattr(health_mod, "get_session_factory", lambda: FakeFactory())

    class BoomRedis:
        @staticmethod
        def from_url(*_a, **_k):
            raise ConnectionError("redis down")

    monkeypatch.setattr(health_mod, "Redis", BoomRedis)
    monkeypatch.setattr(
        health_mod,
        "get_settings",
        lambda: SimpleNamespace(redis_url="redis://localhost:6379/0"),
    )

    neo_result = AsyncMock()
    neo_result.single = AsyncMock(return_value={"n": 1})
    neo_session = AsyncMock()
    neo_session.run = AsyncMock(return_value=neo_result)
    neo_session.__aenter__ = AsyncMock(return_value=neo_session)
    neo_session.__aexit__ = AsyncMock(return_value=False)
    driver = MagicMock()
    driver.session = MagicMock(return_value=neo_session)
    monkeypatch.setattr(health_mod, "get_neo4j_driver", lambda: driver)

    with pytest.raises(HTTPException) as exc:
        await health_mod.check_readiness()
    assert exc.value.status_code == 503
    detail = exc.value.detail
    assert detail["checks"]["redis"] == "error"


@pytest.mark.asyncio
async def test_check_readiness_fails_on_stale_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    session = AsyncMock()
    session.execute = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)

    class SessionCM:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *args):
            return False

    class FakeFactory:
        def __call__(self):
            return SessionCM()

    monkeypatch.setattr(health_mod, "get_session_factory", lambda: FakeFactory())

    stale = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
    redis = AsyncMock()
    redis.ping = AsyncMock(return_value=True)
    redis.get = AsyncMock(return_value=stale)
    redis.aclose = AsyncMock()

    class FakeRedis:
        @staticmethod
        def from_url(*_a, **_k):
            return redis

    monkeypatch.setattr(health_mod, "Redis", FakeRedis)
    monkeypatch.setattr(
        health_mod,
        "get_settings",
        lambda: SimpleNamespace(redis_url="redis://localhost:6379/0"),
    )

    neo_result = AsyncMock()
    neo_result.single = AsyncMock(return_value={"n": 1})
    neo_session = AsyncMock()
    neo_session.run = AsyncMock(return_value=neo_result)
    neo_session.__aenter__ = AsyncMock(return_value=neo_session)
    neo_session.__aexit__ = AsyncMock(return_value=False)
    driver = MagicMock()
    driver.session = MagicMock(return_value=neo_session)
    monkeypatch.setattr(health_mod, "get_neo4j_driver", lambda: driver)

    with pytest.raises(HTTPException) as exc:
        await health_mod.check_readiness()
    assert exc.value.status_code == 503
    assert exc.value.detail["checks"]["worker"] == "stale"
