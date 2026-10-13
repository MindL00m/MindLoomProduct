"""Liveness and readiness probes for the API process."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from redis.asyncio import Redis
from sqlalchemy import text

from core.config import get_settings
from core.database import get_neo4j_driver, get_session_factory
from core.durable_jobs import WORKER_HEARTBEAT_KEY, WORKER_HEARTBEAT_STALE_SECONDS

logger = logging.getLogger(__name__)


def _parse_heartbeat(raw: str | None) -> tuple[float | None, str | None]:
    """Return (age_seconds, stamp) for a heartbeat value."""

    if not raw:
        return None, None
    try:
        stamp = datetime.fromisoformat(raw)
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - stamp.astimezone(timezone.utc)).total_seconds()
        return age, raw
    except ValueError:
        return None, raw


async def check_readiness() -> dict[str, Any]:
    """Probe Postgres, Redis, Neo4j, and worker heartbeat freshness.

    Raises :class:`HTTPException` 503 when any required dependency is down or
    the worker heartbeat is missing/stale.
    """

    checks: dict[str, Any] = {
        "postgres": "ok",
        "redis": "ok",
        "neo4j": "ok",
        "worker": "ok",
    }
    errors: list[str] = []

    try:
        factory = get_session_factory()
        async with factory() as session:
            await session.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Readiness postgres check failed: %s", exc)
        checks["postgres"] = "error"
        errors.append("postgres")

    redis: Redis | None = None
    heartbeat_age: float | None = None
    heartbeat_stamp: str | None = None
    try:
        redis = Redis.from_url(get_settings().redis_url, decode_responses=True)
        pong = await redis.ping()
        if not pong:
            raise RuntimeError("PING returned falsy")
        raw = await redis.get(WORKER_HEARTBEAT_KEY)
        heartbeat_age, heartbeat_stamp = _parse_heartbeat(raw)
        if heartbeat_age is None:
            checks["worker"] = "missing"
            errors.append("worker")
        elif heartbeat_age > WORKER_HEARTBEAT_STALE_SECONDS:
            checks["worker"] = "stale"
            errors.append("worker")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Readiness redis check failed: %s", exc)
        checks["redis"] = "error"
        if checks["worker"] == "ok":
            checks["worker"] = "unknown"
            errors.append("worker")
        errors.append("redis")
    finally:
        if redis is not None:
            await redis.aclose()

    try:
        driver = get_neo4j_driver()
        async with driver.session() as session:
            result = await session.run("RETURN 1 AS n")
            await result.single()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Readiness neo4j check failed: %s", exc)
        checks["neo4j"] = "error"
        errors.append("neo4j")

    payload: dict[str, Any] = {
        "status": "ok" if not errors else "unavailable",
        "checks": checks,
        "worker_heartbeat": heartbeat_stamp,
        "worker_heartbeat_age_seconds": heartbeat_age,
    }
    if errors:
        raise HTTPException(status_code=503, detail=payload)
    return payload
