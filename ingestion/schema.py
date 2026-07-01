"""Lightweight schema upgrades for databases initialised before newer tables existed."""

from __future__ import annotations

import logging

from sqlalchemy import text

from database import get_session_factory

logger = logging.getLogger(__name__)

_APP_CONNECTIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS app_connections (
    connection_id  TEXT PRIMARY KEY,
    org_id         TEXT        NOT NULL REFERENCES organizations (org_id) ON DELETE CASCADE,
    user_id        TEXT        NOT NULL REFERENCES users (user_id) ON DELETE CASCADE,
    provider       TEXT        NOT NULL,
    account_email  TEXT,
    access_token   TEXT        NOT NULL,
    refresh_token  TEXT,
    token_expiry   TIMESTAMPTZ,
    scopes         TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (org_id, user_id, provider)
)
"""

_APP_CONNECTIONS_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_app_connections_org_user ON app_connections (org_id, user_id)
"""


async def ensure_schema() -> None:
    """Apply idempotent DDL for tables added after first deploy."""

    session_factory = get_session_factory()
    async with session_factory() as session:
        async with session.begin():
            await session.execute(text(_APP_CONNECTIONS_TABLE_SQL))
            await session.execute(text(_APP_CONNECTIONS_INDEX_SQL))
    logger.info("Schema check complete (app_connections)")
