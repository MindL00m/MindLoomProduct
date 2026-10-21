"""Per-user CSV files for the LoomBot Files tab, editable from Files and Ask.

Files are private to their owner within an org. Every write keeps the prior
content in ``previous_content`` so the last change (including one made by Ask)
can be undone; undoing twice re-applies the change.

A file can also be *monitored*: newly ingested knowledge that matches its
instructions adds rows or fills cells automatically (see ``file_monitor``).
Monitoring instructions may be stored while monitoring is off; that is how Ask
records a suggestion it still needs the user to confirm.
"""

from __future__ import annotations

import csv
import io
import json
import re
from typing import Any, Literal
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import text

from core.database import get_session_factory

EditedBy = Literal["user", "loombot"]

MAX_FILE_BYTES = 1_000_000
MAX_ROWS = 10_000
MAX_COLUMNS = 200
MAX_NAME_LENGTH = 120
# Ask rewrites whole files, so it only edits files it can read in full.
ASK_MAX_FILE_CHARS = 40_000

_NAME_FORBIDDEN = re.compile(r"[\\/\x00-\x1f]")

_META_COLUMNS = """file_id, name, row_count, column_count, last_edited_by,
    previous_content IS NOT NULL AS can_undo, created_at, updated_at,
    monitor_enabled, monitor_instructions, monitor_started_at"""
MAX_MONITOR_INSTRUCTIONS = 1000


def normalize_name(name: str) -> str:
    """Trim a file name, require a .csv extension, and reject path characters."""

    cleaned = " ".join((name or "").split())
    if not cleaned:
        raise HTTPException(status_code=422, detail="File name is required.")
    if _NAME_FORBIDDEN.search(cleaned):
        raise HTTPException(status_code=422, detail="File names cannot contain slashes.")
    if not cleaned.lower().endswith(".csv"):
        cleaned += ".csv"
    if len(cleaned) > MAX_NAME_LENGTH:
        raise HTTPException(
            status_code=422,
            detail=f"File names must be {MAX_NAME_LENGTH} characters or fewer.",
        )
    return cleaned


def normalize_csv(content: str) -> tuple[str, int, int]:
    """Validate CSV text and return (canonical text, data rows, columns).

    Blank lines are dropped and quoting is re-serialised so every stored file
    parses identically in the browser and in Ask. The first record is the header.
    """

    raw = (content or "").replace("\r\n", "\n").replace("\r", "\n")
    if len(raw.encode("utf-8")) > MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail="CSV files are limited to 1 MB.")
    try:
        records = [
            row for row in csv.reader(io.StringIO(raw), strict=True)
            if any(cell.strip() for cell in row)
        ]
    except csv.Error as exc:
        raise HTTPException(status_code=422, detail=f"Invalid CSV: {exc}") from exc
    if not records:
        return "", 0, 0
    columns = max(len(row) for row in records)
    if columns > MAX_COLUMNS:
        raise HTTPException(
            status_code=422, detail=f"CSV files are limited to {MAX_COLUMNS} columns."
        )
    if len(records) - 1 > MAX_ROWS:
        raise HTTPException(
            status_code=422, detail=f"CSV files are limited to {MAX_ROWS:,} rows."
        )
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\n").writerows(
        row + [""] * (columns - len(row)) for row in records
    )
    return buffer.getvalue(), len(records) - 1, columns


def _meta(row: Any) -> dict[str, Any]:
    return {
        "file_id": row["file_id"],
        "name": row["name"],
        "row_count": int(row["row_count"]),
        "column_count": int(row["column_count"]),
        "last_edited_by": row["last_edited_by"],
        "can_undo": bool(row["can_undo"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "monitor_enabled": bool(row["monitor_enabled"]),
        "monitor_instructions": row["monitor_instructions"],
        "monitor_started_at": row["monitor_started_at"],
    }


def _with_content(row: Any) -> dict[str, Any]:
    return {**_meta(row), "content": row["content"]}


def _conflict(name: str) -> HTTPException:
    return HTTPException(status_code=409, detail=f"You already have a file named {name}.")


def _is_unique_violation(exc: Exception) -> bool:
    return "idx_loombot_files_owner_name" in str(exc) or "unique" in str(exc).lower()


async def list_files(org_id: str, user_id: str) -> list[dict[str, Any]]:
    factory = get_session_factory()
    async with factory() as session:
        rows = (await session.execute(text(f"""
            SELECT {_META_COLUMNS} FROM loombot_files
            WHERE org_id=:org AND owner_user_id=:user
            ORDER BY updated_at DESC
        """), {"org": org_id, "user": user_id})).mappings().all()
    return [_meta(row) for row in rows]


async def get_file(org_id: str, user_id: str, file_id: str) -> dict[str, Any]:
    factory = get_session_factory()
    async with factory() as session:
        row = (await session.execute(text(f"""
            SELECT {_META_COLUMNS}, content FROM loombot_files
            WHERE org_id=:org AND owner_user_id=:user AND file_id=:id
        """), {"org": org_id, "user": user_id, "id": file_id})).mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="File not found.")
    return _with_content(row)


async def find_file_by_name(org_id: str, user_id: str, name: str) -> dict[str, Any] | None:
    """Look up a file by name, case-insensitively, with or without ``.csv``."""

    cleaned = " ".join((name or "").split()).lower()
    if not cleaned:
        return None
    candidates = {cleaned, cleaned if cleaned.endswith(".csv") else f"{cleaned}.csv"}
    factory = get_session_factory()
    async with factory() as session:
        row = (await session.execute(text(f"""
            SELECT {_META_COLUMNS}, content FROM loombot_files
            WHERE org_id=:org AND owner_user_id=:user AND lower(name) = ANY(:names)
            ORDER BY updated_at DESC LIMIT 1
        """), {"org": org_id, "user": user_id, "names": sorted(candidates)})).mappings().one_or_none()
    return _with_content(row) if row is not None else None


async def create_file(
    org_id: str,
    user_id: str,
    name: str,
    content: str = "",
    *,
    edited_by: EditedBy = "user",
) -> dict[str, Any]:
    clean_name = normalize_name(name)
    body, rows, columns = normalize_csv(content)
    file_id = str(uuid4())
    factory = get_session_factory()
    try:
        async with factory() as session, session.begin():
            row = (await session.execute(text(f"""
                INSERT INTO loombot_files
                  (file_id, org_id, owner_user_id, name, content, row_count,
                   column_count, last_edited_by, created_at, updated_at)
                VALUES (:id, :org, :user, :name, :content, :rows, :columns,
                        :edited_by, now(), now())
                RETURNING {_META_COLUMNS}, content
            """), {
                "id": file_id, "org": org_id, "user": user_id, "name": clean_name,
                "content": body, "rows": rows, "columns": columns, "edited_by": edited_by,
            })).mappings().one()
    except HTTPException:
        raise
    except Exception as exc:
        if _is_unique_violation(exc):
            raise _conflict(clean_name) from exc
        raise
    return _with_content(row)


async def update_file(
    org_id: str,
    user_id: str,
    file_id: str,
    *,
    content: str | None = None,
    name: str | None = None,
    edited_by: EditedBy = "user",
) -> dict[str, Any]:
    """Replace content and/or rename. A content change becomes undoable."""

    if content is None and name is None:
        return await get_file(org_id, user_id, file_id)
    params: dict[str, Any] = {
        "org": org_id, "user": user_id, "id": file_id, "edited_by": edited_by,
    }
    sets = ["updated_at=now()", "last_edited_by=:edited_by"]
    if content is not None:
        body, rows, columns = normalize_csv(content)
        params.update(content=body, rows=rows, columns=columns)
        # Only remember the old content when it actually changes, so a no-op
        # save does not wipe out the undo point of a real edit.
        sets += [
            "previous_content=CASE WHEN content IS DISTINCT FROM :content "
            "THEN content ELSE previous_content END",
            "content=:content",
            "row_count=:rows",
            "column_count=:columns",
        ]
    if name is not None:
        params["name"] = normalize_name(name)
        sets.append("name=:name")
    factory = get_session_factory()
    try:
        async with factory() as session, session.begin():
            row = (await session.execute(text(f"""
                UPDATE loombot_files SET {", ".join(sets)}
                WHERE org_id=:org AND owner_user_id=:user AND file_id=:id
                RETURNING {_META_COLUMNS}, content
            """), params)).mappings().one_or_none()
    except HTTPException:
        raise
    except Exception as exc:
        if name is not None and _is_unique_violation(exc):
            raise _conflict(params["name"]) from exc
        raise
    if row is None:
        raise HTTPException(status_code=404, detail="File not found.")
    return _with_content(row)


async def undo_file(org_id: str, user_id: str, file_id: str) -> dict[str, Any]:
    """Swap current and previous content; calling it again redoes the change."""

    factory = get_session_factory()
    async with factory() as session, session.begin():
        found = (await session.execute(text("""
            SELECT previous_content FROM loombot_files
            WHERE org_id=:org AND owner_user_id=:user AND file_id=:id
            FOR UPDATE
        """), {"org": org_id, "user": user_id, "id": file_id})).mappings().one_or_none()
        if found is None:
            raise HTTPException(status_code=404, detail="File not found.")
        if found["previous_content"] is None:
            raise HTTPException(status_code=409, detail="There is no change to undo.")
        _, rows, columns = normalize_csv(found["previous_content"])
        row = (await session.execute(text(f"""
            UPDATE loombot_files
            SET content=previous_content, previous_content=content,
                row_count=:rows, column_count=:columns,
                last_edited_by='user', updated_at=now()
            WHERE org_id=:org AND owner_user_id=:user AND file_id=:id
            RETURNING {_META_COLUMNS}, content
        """), {
            "org": org_id, "user": user_id, "id": file_id,
            "rows": rows, "columns": columns,
        })).mappings().one()
    return _with_content(row)


async def delete_file(org_id: str, user_id: str, file_id: str) -> None:
    factory = get_session_factory()
    async with factory() as session, session.begin():
        deleted = (await session.execute(text("""
            DELETE FROM loombot_files
            WHERE org_id=:org AND owner_user_id=:user AND file_id=:id
            RETURNING file_id
        """), {"org": org_id, "user": user_id, "id": file_id})).scalar_one_or_none()
    if deleted is None:
        raise HTTPException(status_code=404, detail="File not found.")


def _headers(content: str) -> list[str]:
    try:
        return next(csv.reader(io.StringIO(content or "")), [])
    except csv.Error:
        return []


async def set_monitoring(
    org_id: str,
    user_id: str,
    file_id: str,
    *,
    enabled: bool,
    instructions: str | None = None,
) -> dict[str, Any]:
    """Turn monitoring on or off, or store suggested instructions while off.

    Enabling needs instructions (given here or stored earlier). Only knowledge
    that arrives after monitoring starts is applied.
    """

    current = await get_file(org_id, user_id, file_id)
    cleaned = " ".join((instructions or "").split())[:MAX_MONITOR_INSTRUCTIONS] or None
    final = cleaned or current.get("monitor_instructions")
    if enabled and not final:
        raise HTTPException(
            status_code=422,
            detail="Describe what should be added to this file before turning on monitoring.",
        )
    params: dict[str, Any] = {
        "org": org_id, "user": user_id, "id": file_id,
        "enabled": enabled, "instructions": final,
        "keywords": "[]", "embedding": None,
    }
    if enabled:
        from lens.status_trackers import extract_keywords

        headers = " ".join(_headers(current["content"]))
        stem = current["name"][:-4] if current["name"].lower().endswith(".csv") else current["name"]
        params["keywords"] = json.dumps(extract_keywords(final, stem.replace("-", " ").replace("_", " ")))
        try:
            from brain.embedder import embed_text

            params["embedding"] = json.dumps(
                await embed_text(f"{current['name']}\n{headers}\n{final}")
            )
        except Exception:  # noqa: BLE001 - keyword matching still works without it
            params["embedding"] = None
    factory = get_session_factory()
    async with factory() as session, session.begin():
        row = (await session.execute(text(f"""
            UPDATE loombot_files
            SET monitor_enabled=:enabled,
                monitor_instructions=:instructions,
                monitor_keywords=:keywords,
                monitor_embedding=:embedding,
                monitor_started_at=CASE
                    WHEN :enabled AND NOT monitor_enabled THEN now()
                    WHEN :enabled THEN monitor_started_at
                    ELSE NULL END,
                updated_at=now()
            WHERE org_id=:org AND owner_user_id=:user AND file_id=:id
            RETURNING {_META_COLUMNS}, content
        """), params)).mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="File not found.")
    return _with_content(row)


async def list_events(org_id: str, user_id: str, file_id: str, limit: int = 50) -> list[dict[str, Any]]:
    """Automatic changes monitoring made to a file, newest first."""

    await get_file(org_id, user_id, file_id)  # ownership check
    factory = get_session_factory()
    async with factory() as session:
        rows = (await session.execute(text("""
            SELECT event_id, summary, rows_added, cells_updated, source_chunk_ids,
                   source_label, source_url, created_at
            FROM loombot_file_events
            WHERE org_id=:org AND file_id=:id
            ORDER BY created_at DESC
            LIMIT :limit
        """), {"org": org_id, "id": file_id, "limit": limit})).mappings().all()
    return [
        {**dict(row), "source_chunk_ids": json.loads(row["source_chunk_ids"] or "[]")}
        for row in rows
    ]
