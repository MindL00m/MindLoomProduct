"""Database connection pools.

This module owns the only long-lived, shared connection resources in the
application: the SQLAlchemy async engine (a pooled PostgreSQL connection pool)
and the Neo4j async driver (which maintains its own connection pool). They are
created lazily and reused across requests.
"""

from __future__ import annotations

import logging

from neo4j import AsyncDriver, AsyncGraphDatabase
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from config import get_settings

logger = logging.getLogger(__name__)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None
_neo4j_driver: AsyncDriver | None = None


def _async_postgres_url(url: str) -> str:
    """Ensure the configured PostgreSQL URL uses the asyncpg driver."""

    if url.startswith("postgresql+asyncpg://"):
        return url
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    return url


def get_engine() -> AsyncEngine:
    """Return the shared, pooled async SQLAlchemy engine."""

    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            _async_postgres_url(settings.postgres_url),
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,
            pool_recycle=1800,
        )
        logger.info("Initialised PostgreSQL async engine connection pool")
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the shared async session factory bound to the pooled engine."""

    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _session_factory


def get_neo4j_driver() -> AsyncDriver:
    """Return the shared async Neo4j driver (which pools connections internally)."""

    global _neo4j_driver
    if _neo4j_driver is None:
        settings = get_settings()
        _neo4j_driver = AsyncGraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_username, settings.neo4j_password),
        )
        logger.info("Initialised Neo4j async driver connection pool")
    return _neo4j_driver


async def close_pools() -> None:
    """Dispose of all shared connection pools (call on application shutdown)."""

    global _engine, _session_factory, _neo4j_driver
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
        logger.info("Disposed PostgreSQL engine pool")
    if _neo4j_driver is not None:
        await _neo4j_driver.close()
        _neo4j_driver = None
        logger.info("Closed Neo4j driver pool")
