"""Durable, source-by-source synchronization reports."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import text

from database import get_session_factory


async def start_sync_run(org_id: str, provider: str, trigger: str) -> str:
    run_id = str(uuid4())
    factory = get_session_factory()
    async with factory() as session:
        async with session.begin():
            await session.execute(text("""
                INSERT INTO sync_runs
                  (run_id, org_id, provider, trigger, status, details_json,
                   started_at, created_at)
                VALUES (:id, :org, :provider, :trigger, 'running', '{}', now(), now())
            """), {"id": run_id, "org": org_id, "provider": provider, "trigger": trigger})
    return run_id


async def finish_sync_run(
    run_id: str, *, details: dict[str, dict], error: str | None = None
) -> None:
    totals = {
        "imported": sum(int(item.get("imported", 0)) for item in details.values()),
        "updated": sum(int(item.get("updated", 0)) for item in details.values()),
        "deleted": sum(int(item.get("deleted", 0)) for item in details.values()),
        "skipped": sum(int(item.get("skipped", 0)) for item in details.values()),
        "failed": sum(int(item.get("failed", 0)) for item in details.values()),
    }
    factory = get_session_factory()
    async with factory() as session:
        async with session.begin():
            await session.execute(text("""
                UPDATE sync_runs SET status=:status, details_json=:details,
                  imported=:imported, updated=:updated, deleted=:deleted,
                  skipped=:skipped, failed=:failed, error=:error,
                  completed_at=now()
                WHERE run_id=:id
            """), {
                "id": run_id, "status": "failed" if error else "complete",
                "details": json.dumps(details), "error": error, **totals,
            })


async def list_sync_runs(org_id: str, provider: str | None = None) -> list[dict]:
    factory = get_session_factory()
    query = "SELECT * FROM sync_runs WHERE org_id=:org"
    params: dict[str, object] = {"org": org_id}
    if provider:
        query += " AND provider=:provider"
        params["provider"] = provider
    query += " ORDER BY started_at DESC LIMIT 100"
    async with factory() as session:
        rows = (await session.execute(text(query), params)).mappings().all()
    return [
        {**dict(row), "details": json.loads(row["details_json"] or "{}")}
        for row in rows
    ]
