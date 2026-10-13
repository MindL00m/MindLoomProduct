"""Schema bootstrap regression tests."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from core import schema as schema_mod


def test_base_schema_creates_tenancy_before_connections() -> None:
    statements = schema_mod._sql_statements(
        schema_mod._INIT_SQL_PATH.read_text(encoding="utf-8")
    )

    organizations = next(
        index
        for index, statement in enumerate(statements)
        if statement.startswith("CREATE TABLE IF NOT EXISTS organizations")
    )
    users = next(
        index
        for index, statement in enumerate(statements)
        if statement.startswith("CREATE TABLE IF NOT EXISTS users")
    )
    connections = next(
        index
        for index, statement in enumerate(statements)
        if statement.startswith("CREATE TABLE IF NOT EXISTS app_connections")
    )

    assert organizations < users < connections


@pytest.mark.asyncio
async def test_ensure_schema_locks_before_running_ddl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = AsyncMock()

    @asynccontextmanager
    async def transaction():
        yield

    session.begin = transaction

    @asynccontextmanager
    async def session_context():
        yield session

    monkeypatch.setattr(
        schema_mod,
        "get_session_factory",
        lambda: session_context,
    )

    await schema_mod.ensure_schema()

    statements = [str(call.args[0]) for call in session.execute.await_args_list]
    assert statements[0] == schema_mod._SCHEMA_LOCK_SQL
    assert statements[1].startswith("CREATE EXTENSION IF NOT EXISTS vector")
    assert any(
        statement.startswith("CREATE TABLE IF NOT EXISTS organizations")
        for statement in statements
    )
