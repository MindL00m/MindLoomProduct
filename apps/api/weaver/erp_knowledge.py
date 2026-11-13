"""Importing NetSuite and SAP data into knowledge.

An ERP holds far more than belongs in a knowledge base, so nothing is imported by
default. The person names what they want as an *import*: a title and one read, a SuiteQL
query for NetSuite or an OData record set for SAP ("Customers", "Open sales orders").
MindLoom runs the read, turns the rows into plain-text pages of 50 rows, and ingests each
page like any other document, so Ask can find and cite them.

Imports refresh once a day and on request. A page whose rows did not change is not
ingested again (``ingest_external_source`` compares content), and pages that no longer
exist after a refresh are removed. Each import is visible to the person who made it, or
to the whole organization if they chose that.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from fastapi import HTTPException

from core.database import get_session_factory

logger = logging.getLogger(__name__)

PROVIDERS = ("netsuite", "sap")
LABELS = {"netsuite": "NetSuite", "sap": "SAP"}
VISIBILITIES = ("me", "org")
ROWS_PER_PAGE = 50
DEFAULT_MAX_ROWS = 500
MAX_ROWS = 5000
MAX_IMPORTS = 20
MAX_VALUE_CHARS = 300
REFRESH_EVERY = timedelta(hours=24)
_COLUMNS = ("import_id", "org_id", "user_id", "provider", "name", "query", "options", "max_rows",
            "visibility", "pages", "last_run_at", "last_status", "last_error", "last_rows", "created_at")

# Tests swap the database for this.
_memory: dict[str, dict[str, Any]] | None = None


def use_memory_store(enabled: bool = True) -> None:
    global _memory
    _memory = {} if enabled else None


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- Storage --------------------------------------------------------------------------


async def _execute(sql: str, **params: Any) -> list[dict[str, Any]]:
    from sqlalchemy import text

    async with get_session_factory()() as session:
        async with session.begin():
            result = await session.execute(text(sql), params)
            return [dict(row._mapping) for row in result] if result.returns_rows else []


async def _rows(org_id: str, user_id: str) -> list[dict[str, Any]]:
    if _memory is not None:
        found = [dict(r) for r in _memory.values() if (r["org_id"], r["user_id"]) == (org_id, user_id)]
        return sorted(found, key=lambda r: r["created_at"])
    return await _execute(
        f"SELECT {', '.join(_COLUMNS)} FROM erp_imports WHERE org_id = :org_id AND user_id = :user_id "
        "ORDER BY created_at", org_id=org_id, user_id=user_id)


async def _row(org_id: str, user_id: str, import_id: str) -> dict[str, Any]:
    found = next((r for r in await _rows(org_id, user_id) if r["import_id"] == import_id), None)
    if found is None:
        raise HTTPException(status_code=404, detail="That import no longer exists.")
    return found


async def _insert(row: dict[str, Any]) -> None:
    if _memory is not None:
        _memory[row["import_id"]] = dict(row)
        return
    names = ", ".join(_COLUMNS)
    await _execute(f"INSERT INTO erp_imports ({names}) VALUES ({', '.join(':' + c for c in _COLUMNS)})", **row)


async def _update(import_id: str, **values: Any) -> None:
    if _memory is not None:
        if import_id in _memory:
            _memory[import_id].update(values)
        return
    sets = ", ".join(f"{name} = :{name}" for name in values)
    await _execute(f"UPDATE erp_imports SET {sets} WHERE import_id = :import_id", import_id=import_id, **values)


async def _delete(import_id: str) -> None:
    if _memory is not None:
        _memory.pop(import_id, None)
        return
    await _execute("DELETE FROM erp_imports WHERE import_id = :import_id", import_id=import_id)


async def _due() -> list[dict[str, Any]]:
    cutoff = _now() - REFRESH_EVERY
    if _memory is not None:
        return [dict(r) for r in _memory.values() if r["last_run_at"] is None or r["last_run_at"] < cutoff]
    return await _execute(
        f"SELECT {', '.join(_COLUMNS)} FROM erp_imports WHERE last_run_at IS NULL OR last_run_at < :cutoff "
        "ORDER BY last_run_at NULLS FIRST LIMIT 50", cutoff=cutoff)


# --- The person's list of imports -----------------------------------------------------


def _view(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "import_id": row["import_id"], "provider": row["provider"], "name": row["name"], "query": row["query"],
        "options": json.loads(row["options"] or "{}"), "max_rows": row["max_rows"], "visibility": row["visibility"],
        "last_run_at": row["last_run_at"].isoformat() if row["last_run_at"] else None,
        "last_status": row["last_status"], "last_error": row["last_error"], "last_rows": row["last_rows"],
    }


async def list_imports(org_id: str, user_id: str) -> list[dict[str, Any]]:
    return [_view(row) for row in await _rows(org_id, user_id)]


async def create_import(org_id: str, user_id: str, *, provider: object, name: object, query: object,
                        options: object = None, max_rows: object = None, visibility: object = "me") -> dict[str, Any]:
    """Save an import after checking its read is one MindLoom would run. It is not run here."""

    from weaver import netsuite, sap

    kind, title = str(provider or "").strip().lower(), " ".join(str(name or "").split())[:120]
    shown = str(visibility or "me").strip().lower()
    if kind not in PROVIDERS or shown not in VISIBILITIES or not title:
        raise HTTPException(status_code=422, detail="Give the system, a name and who may see the import.")
    try:
        cap = max(1, min(int(max_rows or DEFAULT_MAX_ROWS), MAX_ROWS))
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="The row limit must be a number.") from None
    text = str(query or "").strip()
    chosen: dict[str, str] = {}
    if kind == "netsuite":
        text = netsuite.check_query(text)
        if not await netsuite.is_connected(org_id, user_id):
            raise HTTPException(status_code=409, detail="Connect NetSuite first.")
    else:
        row = await sap._get_row(org_id, user_id)
        if row is None:
            raise HTTPException(status_code=409, detail="Connect SAP first.")
        text = sap.normalize_path(text, getattr(row, "system", None) or "s4hana", getattr(row, "api_root", None))
        chosen = {k: v for k, v in sap._query(options, "", None, "business_one").items() if k not in {"$top", "$skip"}}
    if len(await _rows(org_id, user_id)) >= MAX_IMPORTS:
        raise HTTPException(status_code=409, detail=f"You can keep up to {MAX_IMPORTS} imports. Delete one first.")
    row = {
        "import_id": uuid4().hex, "org_id": org_id, "user_id": user_id, "provider": kind, "name": title,
        "query": text, "options": json.dumps(chosen), "max_rows": cap, "visibility": shown, "pages": 0,
        "last_run_at": None, "last_status": None, "last_error": None, "last_rows": None, "created_at": _now(),
    }
    await _insert(row)
    return _view(row)


def _external_id(import_id: str, page: int) -> str:
    return f"erp:{import_id}:{page}"


async def _forget_pages(row: dict[str, Any], start: int) -> None:
    from brain.source_registry import mark_external_source_deleted

    for page in range(start, int(row.get("pages") or 0)):
        await mark_external_source_deleted(row["org_id"], row["provider"], _external_id(row["import_id"], page))


async def delete_import(org_id: str, user_id: str, import_id: str) -> dict[str, bool]:
    """Remove the import and what it put into knowledge."""

    row = await _row(org_id, user_id, import_id)
    await _forget_pages(row, 0)
    await _delete(import_id)
    return {"deleted": True}


async def delete_for(org_id: str, user_id: str, provider: str) -> None:
    """Disconnecting a system removes its imports and their knowledge."""

    for row in await _rows(org_id, user_id):
        if row["provider"] == provider:
            await _forget_pages(row, 0)
            await _delete(row["import_id"])


# --- Rows into pages ------------------------------------------------------------------


def _cell(value: Any) -> str:
    if value is None or value == "" or value == [] or value == {}:
        return ""
    if isinstance(value, (dict, list)):
        value = json.dumps(value, default=str, ensure_ascii=False)
    return re.sub(r"\s+", " ", str(value)).strip()[:MAX_VALUE_CHARS]


def render_pages(name: str, provider: str, rows: list[dict[str, Any]]) -> list[str]:
    """Plain-text pages of ``ROWS_PER_PAGE`` rows. The same rows always give the same text,
    so an unchanged page is recognized on the next refresh."""

    pages = []
    for start in range(0, len(rows), ROWS_PER_PAGE):
        chunk = rows[start:start + ROWS_PER_PAGE]
        lines = [f"{name} ({LABELS[provider]}), rows {start + 1} to {start + len(chunk)} of {len(rows)}", ""]
        for row in chunk:
            cells = [f"{key}: {text}" for key, text in ((k, _cell(v)) for k, v in row.items()) if text]
            if cells:
                lines.append("- " + "; ".join(cells))
        pages.append("\n".join(lines) + "\n")
    return pages


async def _fetch(row: dict[str, Any]) -> list[dict[str, Any]]:
    from weaver import netsuite, sap

    if row["provider"] == "netsuite":
        return await netsuite.query_all(row["org_id"], row["user_id"], row["query"], max_rows=row["max_rows"])
    return await sap.read_all(row["org_id"], row["user_id"], row["query"], json.loads(row["options"] or "{}"),
                              max_rows=row["max_rows"])


async def _ingest(row: dict[str, Any], page: int, text: str, total: int) -> bool:
    from brain.pipeline import DocumentInput
    from brain.source_registry import ingest_external_source
    from core.models import Conversation, IncomingMessage, Participant

    label = LABELS[row["provider"]]
    title = row["name"] if total == 1 else f"{row['name']} ({page + 1} of {total})"
    external_id = _external_id(row["import_id"], page)
    visible_to = [f"org:{row['org_id']}"] if row["visibility"] == "org" else [f"user:{row['user_id']}"]
    result = await ingest_external_source(
        org_id=row["org_id"], provider=row["provider"], external_id=external_id, version=None,
        conversation=Conversation(
            source=row["provider"], conversation_id=external_id, title=title,
            participants=[Participant(id="erp", name=label)],
            messages=[IncomingMessage(id=external_id, sender="erp", timestamp=_now(), text=text)],
        ),
        document=DocumentInput(
            data=text.encode(), source=row["provider"], source_label=title,
            original_filename=f"{external_id.replace(':', '-')}.txt", mime_type="text/plain",
            visible_to=visible_to, title=title, author=label, source_application=label,
        ),
    )
    return result is not None


async def run_import(org_id: str, user_id: str, import_id: str) -> dict[str, Any]:
    """Read the rows and bring knowledge up to date. Returns counts; records failures on the import."""

    row = await _row(org_id, user_id, import_id)
    try:
        rows = await _fetch(row)
        pages = render_pages(row["name"], row["provider"], rows)
        # Remember the highest page that may exist before writing any, so that a run that
        # fails halfway still knows what to remove when the import is deleted.
        row["pages"] = max(int(row.get("pages") or 0), len(pages))
        await _update(import_id, pages=row["pages"])
        changed = 0
        for index, text in enumerate(pages):
            changed += bool(await _ingest(row, index, text, len(pages)))
        await _forget_pages(row, len(pages))
    except Exception as exc:  # noqa: BLE001 - shown on the import, retried at the next refresh
        detail = str(getattr(exc, "detail", "") or exc)[:500]
        logger.warning("ERP import %s failed: %s", import_id, detail)
        await _update(import_id, last_run_at=_now(), last_status="error", last_error=detail)
        return {"ok": False, "error": detail}
    await _update(import_id, last_run_at=_now(), last_status="ok", last_error=None, last_rows=len(rows), pages=len(pages))
    return {"ok": True, "rows": len(rows), "pages": len(pages), "pages_changed": changed}


async def run_due_imports() -> int:
    """The daily refresh: every import not run in the last 24 hours. One failing does not stop the rest."""

    count = 0
    for row in await _due():
        await run_import(row["org_id"], row["user_id"], row["import_id"])
        count += 1
    return count
