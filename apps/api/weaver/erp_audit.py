"""A record of every change MindLoom made, or tried to make, in NetSuite or SAP.

An ERP holds the company's books, so "who changed what, when, and through which part of
MindLoom" must be answerable later. Each create or update is written here whether it
came from an automation or from an approved card in Ask, and whether it worked.

Only the names of the fields are kept, never their values: the values live in the ERP,
and copying them here would spread customer and financial data for no benefit.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from core.database import get_session_factory

logger = logging.getLogger(__name__)

SOURCES = ("automation", "ask")
MAX_LISTED = 100
_COLUMNS = ("change_id", "org_id", "user_id", "system", "action", "target", "record_id", "field_names",
            "source", "run_id", "ok", "detail", "created_at")

# Tests swap the database for this.
_memory: list[dict[str, Any]] | None = None


def use_memory_store(enabled: bool = True) -> None:
    global _memory
    _memory = [] if enabled else None


async def record(org_id: str, user_id: str, *, system: str, action: str, target: object,
                 record_id: object = None, fields: object = None, source: str, run_id: str | None = None,
                 ok: bool, detail: object = None) -> None:
    """Write one entry. Never raises: failing to log must not undo or hide the change itself."""

    row = {
        "change_id": uuid4().hex, "org_id": org_id, "user_id": user_id, "system": system, "action": action,
        "target": str(target or "")[:400], "record_id": str(record_id)[:130] if record_id else None,
        "field_names": ", ".join(sorted(str(k) for k in fields))[:1000] if isinstance(fields, dict) else "",
        "source": source if source in SOURCES else "automation", "run_id": run_id, "ok": bool(ok),
        "detail": str(detail)[:500] if detail else None, "created_at": datetime.now(timezone.utc),
    }
    try:
        if _memory is not None:
            _memory.append(row)
            return
        from sqlalchemy import text

        async with get_session_factory()() as session:
            async with session.begin():
                await session.execute(text(
                    f"INSERT INTO erp_changes ({', '.join(_COLUMNS)}) VALUES ({', '.join(':' + c for c in _COLUMNS)})"
                ), row)
    except Exception:  # noqa: BLE001
        logger.exception("Could not record an ERP change (%s %s)", system, action)


async def list_changes(org_id: str, user_id: str, limit: int = 30) -> list[dict[str, Any]]:
    """The person's own recent changes, newest first."""

    limit = max(1, min(int(limit), MAX_LISTED))
    if _memory is not None:
        rows = [r for r in _memory if (r["org_id"], r["user_id"]) == (org_id, user_id)]
        rows = sorted(rows, key=lambda r: r["created_at"], reverse=True)[:limit]
    else:
        from sqlalchemy import text

        async with get_session_factory()() as session:
            result = await session.execute(text(
                f"SELECT {', '.join(_COLUMNS)} FROM erp_changes WHERE org_id = :org_id AND user_id = :user_id "
                "ORDER BY created_at DESC LIMIT :limit"), {"org_id": org_id, "user_id": user_id, "limit": limit})
            rows = [dict(r._mapping) for r in result]
    return [{**{k: r[k] for k in _COLUMNS if k not in {"org_id", "user_id", "created_at"}},
             "created_at": r["created_at"].isoformat()} for r in rows]
