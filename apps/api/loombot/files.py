"""Per-user files for the LoomBot Files tab, editable from Files and Ask.

Supported kinds: CSV, TXT, Markdown and JSON (text, stored in ``content`` and
editable in the app) plus DOCX, XLSX, PDF and PPTX (binary, stored in blob
storage). Every kind can be created from text: DOCX, PDF and PPTX from Markdown
and XLSX from CSV or Markdown tables (see ``docx_builder`` and ``file_builders``). Files are private
to their owner within an org. Every change keeps the prior version
(``previous_content`` or ``previous_blob_key``) so the last change, including
one made by Ask, can be undone; undoing twice re-applies the change.

A CSV file can also be *monitored*: newly ingested knowledge that matches its
instructions adds rows or fills cells automatically (see ``file_monitor``).
Monitoring instructions may be stored while monitoring is off; that is how Ask
records a suggestion it still needs the user to confirm.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import mimetypes
import re
import zipfile
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import text

from core.database import get_session_factory

logger = logging.getLogger(__name__)

EditedBy = Literal["user", "loombot"]
FileKind = Literal[
    "csv", "txt", "md", "json", "docx", "xlsx", "pdf", "pptx",
    "png", "jpg", "jpeg", "gif", "webp", "file",
]

MAX_FILE_BYTES = 1_000_000          # text kinds (CSV, TXT)
MAX_UPLOAD_BYTES = 15_000_000       # binary kinds (DOCX, XLSX, PDF)
MAX_ROWS = 10_000
MAX_COLUMNS = 200
MAX_NAME_LENGTH = 120
# Ask rewrites whole files, so it only edits files it can read in full.
ASK_MAX_FILE_CHARS = 40_000
MAX_MONITOR_INSTRUCTIONS = 1000
LINK_TTL = timedelta(minutes=10)
_LINK_AUDIENCE = "loombot-file-link"

KINDS: dict[str, str] = {
    "csv": "text/csv",
    "txt": "text/plain",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pdf": "application/pdf",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "md": "text/markdown",
    "json": "application/json",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    # Any other type: kept as uploaded and downloadable; Ask reads it when it is text.
    "file": "application/octet-stream",
}
OTHER_KIND = "file"
# Kinds that can keep themselves updated from new knowledge (see file_monitor).
MONITORABLE_KINDS = frozenset({"csv", "xlsx"})
TEXT_KINDS = frozenset({"csv", "txt", "md", "json"})
IMAGE_KINDS = frozenset({"png", "jpg", "jpeg", "gif", "webp"})
BINARY_KINDS = frozenset({"docx", "xlsx", "pdf", "pptx", OTHER_KIND}) | IMAGE_KINDS
# Binary kinds that are built from text (Markdown, or CSV/Markdown tables for XLSX).
BUILT_KINDS = frozenset({"docx", "xlsx", "pdf", "pptx"})

_NAME_FORBIDDEN = re.compile(r"[\\/\x00-\x1f]")

_META_COLUMNS = """file_id, name, kind, mime_type, size_bytes, row_count, column_count,
    last_edited_by,
    (previous_content IS NOT NULL OR previous_blob_key IS NOT NULL) AS can_undo,
    created_at, updated_at, based_on_file_id,
    monitor_enabled, monitor_instructions, monitor_started_at,
    shared_by_user_id, shared_by_name, shared_note, shared_at, share_seen_at, is_template"""


# --- Names and kinds ----------------------------------------------------------


def kind_from_name(name: str) -> str | None:
    lowered = (name or "").strip().lower()
    for kind in KINDS:
        if kind == OTHER_KIND:
            continue
        if lowered.endswith(f".{kind}"):
            return kind
    return None


def normalize_name(name: str, kind: str = "csv") -> str:
    """Trim a file name, require the kind's extension, and reject path characters."""

    cleaned = " ".join((name or "").split())
    if not cleaned:
        raise HTTPException(status_code=422, detail="File name is required.")
    if _NAME_FORBIDDEN.search(cleaned):
        raise HTTPException(status_code=422, detail="File names cannot contain slashes.")
    if kind != OTHER_KIND and not cleaned.lower().endswith(f".{kind}"):
        cleaned += f".{kind}"
    if len(cleaned) > MAX_NAME_LENGTH:
        raise HTTPException(
            status_code=422,
            detail=f"File names must be {MAX_NAME_LENGTH} characters or fewer.",
        )
    return cleaned


def stem(name: str) -> str:
    kind = kind_from_name(name)
    return name[: -(len(kind) + 1)] if kind else name


# --- Text content ---------------------------------------------------------------


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


def normalize_txt(content: str) -> tuple[str, int, int]:
    """Normalise line endings and return (text, lines, 0)."""

    raw = (content or "").replace("\r\n", "\n").replace("\r", "\n")
    if len(raw.encode("utf-8")) > MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail="Text files are limited to 1 MB.")
    return raw, (raw.count("\n") + (0 if raw.endswith("\n") or not raw else 1)), 0


def normalize_text(kind: str, content: str) -> tuple[str, int, int]:
    if kind == "csv":
        return normalize_csv(content)
    if kind == "json" and (content or "").strip():
        try:
            json.loads(content)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"That is not valid JSON: {exc}.") from exc
    return normalize_txt(content)


def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


# --- Binary content -------------------------------------------------------------


def validate_binary(kind: str, data: bytes) -> None:
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Files are limited to 15 MB.")
    if kind == OTHER_KIND:
        if not data:
            raise HTTPException(status_code=422, detail="That file is empty.")
        return
    if kind == "pdf":
        if not data.startswith(b"%PDF"):
            raise HTTPException(status_code=422, detail="That file is not a valid PDF.")
        return
    if kind in IMAGE_KINDS:
        from brain.media_text import is_image

        if not is_image("", data):
            raise HTTPException(status_code=422, detail="That file is not a valid picture.")
        return
    required = {
        "docx": "word/document.xml", "xlsx": "xl/workbook.xml", "pptx": "ppt/presentation.xml",
    }[kind]
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if required not in archive.namelist():
                raise HTTPException(status_code=422, detail=f"That file is not a valid .{kind} file.")
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=422, detail=f"That file is not a valid .{kind} file.") from exc


def binary_stats(kind: str, data: bytes) -> tuple[int, int]:
    """(row_count, column_count) shown in the file list.

    PDF: pages. DOCX: non-empty paragraphs, tables. XLSX: non-empty rows, sheets.
    PPTX: slides. Pictures: height and width in pixels.
    """

    try:
        if kind in IMAGE_KINDS:
            from PIL import Image

            with Image.open(io.BytesIO(data)) as picture:
                return picture.height, picture.width
        if kind == "pptx":
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                slides = [
                    n for n in archive.namelist()
                    if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)
                ]
            return len(slides), 0
        if kind == "pdf":
            import fitz

            with fitz.open(stream=data, filetype="pdf") as doc:
                return doc.page_count, 0
        if kind == "docx":
            import docx

            document = docx.Document(io.BytesIO(data))
            return sum(1 for p in document.paragraphs if p.text.strip()), len(document.tables)
        if kind == "xlsx":
            import openpyxl

            workbook = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
            try:
                rows = sum(
                    1
                    for sheet in workbook.worksheets
                    for row in sheet.iter_rows(values_only=True)
                    if any(value not in (None, "") for value in row)
                )
                return rows, len(workbook.worksheets)
            finally:
                workbook.close()
    except Exception:  # noqa: BLE001 - stats are cosmetic
        logger.warning("Could not read %s stats", kind, exc_info=True)
    return 0, 0


async def _put_blob(org_id: str, file_id: str, kind: str, data: bytes) -> str:
    from core.blob_storage import get_blob_storage

    return await get_blob_storage().put(
        f"loombot-files/{org_id}/{file_id}/{uuid4().hex}.{kind}", data
    )


async def _delete_blobs(*paths: str | None) -> None:
    from core.blob_storage import get_blob_storage

    storage = get_blob_storage()
    for path in paths:
        if path:
            try:
                await storage.delete(path)
            except Exception:  # noqa: BLE001 - orphaned blobs are harmless
                logger.warning("Could not delete blob %s", path, exc_info=True)


# --- Rows -------------------------------------------------------------------------


def _meta(row: Any) -> dict[str, Any]:
    return {
        "file_id": row["file_id"],
        "name": row["name"],
        "kind": row["kind"] or "csv",
        "mime_type": row["mime_type"] or KINDS.get(row["kind"] or "csv"),
        "size_bytes": int(row["size_bytes"] or 0),
        "row_count": int(row["row_count"]),
        "column_count": int(row["column_count"]),
        "last_edited_by": row["last_edited_by"],
        "can_undo": bool(row["can_undo"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "based_on_file_id": row["based_on_file_id"],
        "monitor_enabled": bool(row["monitor_enabled"]),
        "monitor_instructions": row["monitor_instructions"],
        "monitor_started_at": row["monitor_started_at"],
        "shared_by_user_id": row.get("shared_by_user_id"),
        "shared_by_name": row.get("shared_by_name"),
        "shared_note": row.get("shared_note"),
        "shared_at": row.get("shared_at"),
        "share_seen": row.get("share_seen_at") is not None or row.get("shared_at") is None,
        "is_template": bool(row.get("is_template")),
    }


def _with_content(row: Any) -> dict[str, Any]:
    return {**_meta(row), "content": row["content"]}


def _conflict(name: str) -> HTTPException:
    return HTTPException(status_code=409, detail=f"You already have a file named {name}.")


def _is_unique_violation(exc: Exception) -> bool:
    return "idx_loombot_files_owner_name" in str(exc) or "unique" in str(exc).lower()


# --- Reading ------------------------------------------------------------------------


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
    """Look up a file by name, case-insensitively, with or without its extension."""

    cleaned = " ".join((name or "").split()).lower()
    if not cleaned:
        return None
    candidates = {cleaned}
    if kind_from_name(cleaned) is None:
        candidates |= {f"{cleaned}.{kind}" for kind in KINDS}
    factory = get_session_factory()
    async with factory() as session:
        row = (await session.execute(text(f"""
            SELECT {_META_COLUMNS}, content FROM loombot_files
            WHERE org_id=:org AND owner_user_id=:user AND lower(name) = ANY(:names)
            ORDER BY (kind = 'csv') DESC, updated_at DESC LIMIT 1
        """), {"org": org_id, "user": user_id, "names": sorted(candidates)})).mappings().one_or_none()
    return _with_content(row) if row is not None else None


async def get_file_bytes(org_id: str, user_id: str, file_id: str) -> tuple[bytes, dict[str, Any]]:
    """The file's current bytes and metadata (text kinds are UTF-8 encoded)."""

    factory = get_session_factory()
    async with factory() as session:
        row = (await session.execute(text(f"""
            SELECT {_META_COLUMNS}, content, blob_key FROM loombot_files
            WHERE org_id=:org AND owner_user_id=:user AND file_id=:id
        """), {"org": org_id, "user": user_id, "id": file_id})).mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="File not found.")
    meta = _meta(row)
    if meta["kind"] in TEXT_KINDS:
        return (row["content"] or "").encode("utf-8"), meta
    if not row["blob_key"]:
        raise HTTPException(status_code=404, detail="File content is missing.")
    from core.blob_storage import get_blob_storage

    return await get_blob_storage().get(row["blob_key"]), meta


# --- Writing ------------------------------------------------------------------------


async def _insert(params: dict[str, Any], clean_name: str) -> dict[str, Any]:
    factory = get_session_factory()
    try:
        async with factory() as session, session.begin():
            row = (await session.execute(text(f"""
                INSERT INTO loombot_files
                  (file_id, org_id, owner_user_id, name, kind, mime_type, size_bytes,
                   content, blob_key, row_count, column_count, last_edited_by,
                   based_on_file_id, created_at, updated_at)
                VALUES (:id, :org, :user, :name, :kind, :mime, :size, :content, :blob,
                        :rows, :columns, :edited_by, :based_on, now(), now())
                RETURNING {_META_COLUMNS}, content
            """), params)).mappings().one()
    except HTTPException:
        raise
    except Exception as exc:
        if _is_unique_violation(exc):
            raise _conflict(clean_name) from exc
        raise
    return _with_content(row)


async def create_file(
    org_id: str,
    user_id: str,
    name: str,
    content: str = "",
    *,
    edited_by: EditedBy = "user",
    kind: str = "csv",
    based_on: str | None = None,
) -> dict[str, Any]:
    """Create a CSV (default) or TXT file from text."""

    if kind not in TEXT_KINDS:
        raise HTTPException(status_code=422, detail="Only CSV and TXT files can be created from text.")
    clean_name = normalize_name(name, kind)
    body, rows, columns = normalize_text(kind, content)
    return await _insert({
        "id": str(uuid4()), "org": org_id, "user": user_id, "name": clean_name,
        "kind": kind, "mime": KINDS[kind], "size": len(body.encode("utf-8")),
        "content": body, "blob": None, "rows": rows, "columns": columns,
        "edited_by": edited_by, "based_on": based_on,
    }, clean_name)


async def create_binary_file(
    org_id: str,
    user_id: str,
    name: str,
    kind: str,
    data: bytes,
    *,
    edited_by: EditedBy = "user",
    based_on: str | None = None,
    mime_type: str | None = None,
) -> dict[str, Any]:
    """Create a binary file (Office, PDF, picture, or any other type) from bytes."""

    if kind not in BINARY_KINDS:
        raise HTTPException(status_code=422, detail=f"Unsupported file type: {kind}.")
    validate_binary(kind, data)
    clean_name = normalize_name(name, kind)
    file_id = str(uuid4())
    blob = await _put_blob(org_id, file_id, kind, data)
    rows, columns = binary_stats(kind, data)
    try:
        return await _insert({
            "id": file_id, "org": org_id, "user": user_id, "name": clean_name,
            "kind": kind, "mime": mime_type or KINDS[kind], "size": len(data), "content": "",
            "blob": blob, "rows": rows, "columns": columns, "edited_by": edited_by,
            "based_on": based_on,
        }, clean_name)
    except Exception:
        await _delete_blobs(blob)
        raise


WRITABLE_KINDS = TEXT_KINDS | BUILT_KINDS


def build_binary(kind: str, content: str, name: str) -> bytes:
    """Bytes for a DOCX, PDF or PPTX from Markdown, or an XLSX from CSV/Markdown tables."""

    if len((content or "").encode("utf-8")) > MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail="Document text is limited to 1 MB.")
    title = stem(name)
    if kind == "docx":
        from loombot.docx_builder import markdown_to_docx

        return markdown_to_docx(content or "", title=title)
    from loombot import file_builders

    if kind == "xlsx":
        return file_builders.text_to_xlsx(content or "")
    if kind == "pdf":
        return file_builders.markdown_to_pdf(content or "", title=title)
    if kind == "pptx":
        return file_builders.markdown_to_pptx(content or "", title=title)
    raise HTTPException(status_code=422, detail=f"Cannot build a .{kind} file from text.")


async def create_document(
    org_id: str,
    user_id: str,
    name: str,
    content: str = "",
    *,
    kind: str = "csv",
    edited_by: EditedBy = "user",
    based_on: str | None = None,
) -> dict[str, Any]:
    """Create a text file (CSV, TXT, Markdown, JSON), or build a DOCX, PDF or PPTX
    from Markdown, or an XLSX from CSV text or Markdown tables."""

    if kind in TEXT_KINDS:
        return await create_file(
            org_id, user_id, name, content, edited_by=edited_by, kind=kind, based_on=based_on
        )
    if kind not in BUILT_KINDS:
        raise HTTPException(
            status_code=422,
            detail="New files can be CSV, TXT, Markdown, JSON, Word, Excel, PDF, or PowerPoint.",
        )
    clean_name = normalize_name(name, kind)
    data = build_binary(kind, content, clean_name)
    return await create_binary_file(
        org_id, user_id, clean_name, kind, data, edited_by=edited_by, based_on=based_on
    )


async def upload_file(
    org_id: str,
    user_id: str,
    filename: str,
    data: bytes,
    name: str | None = None,
    *,
    auto_rename: bool = False,
) -> dict[str, Any]:
    """Create a file from an upload; the type comes from the file extension.

    Known types are validated and previewed. Any other type is kept as uploaded
    (kind ``file``) so it can be downloaded, shared and attached. With
    ``auto_rename`` a taken name becomes "name (2).ext" instead of a conflict, which
    multi-file and folder uploads rely on.
    """

    final_name = " ".join((name or filename or "upload").split()) or "upload"
    kind = kind_from_name(final_name) or kind_from_name(filename) or OTHER_KIND
    if auto_rename:
        existing = {f["name"] for f in await list_files(org_id, user_id)}
        final_name = unique_name(existing, final_name)
    if kind in TEXT_KINDS:
        if len(data) > MAX_FILE_BYTES:
            raise HTTPException(status_code=413, detail="CSV and text files are limited to 1 MB.")
        return await create_file(org_id, user_id, final_name, _decode_text(data), kind=kind)
    mime = None
    if kind == OTHER_KIND:
        mime = mimetypes.guess_type(final_name)[0] or "application/octet-stream"
    return await create_binary_file(org_id, user_id, final_name, kind, data, mime_type=mime)


async def update_file(
    org_id: str,
    user_id: str,
    file_id: str,
    *,
    content: str | None = None,
    name: str | None = None,
    edited_by: EditedBy = "user",
) -> dict[str, Any]:
    """Replace text content and/or rename. A content change becomes undoable."""

    if content is None and name is None:
        return await get_file(org_id, user_id, file_id)
    current = await get_file(org_id, user_id, file_id)
    kind = current["kind"]
    if content is not None and kind == "docx":
        if len(content.encode("utf-8")) > MAX_FILE_BYTES:
            raise HTTPException(status_code=413, detail="Document text is limited to 1 MB.")
        from loombot.docx_builder import blocks_to_markdown, markdown_to_docx

        existing, _ = await get_file_bytes(org_id, user_id, file_id)
        unchanged = blocks_to_markdown(docx_blocks(existing)).strip() == content.strip()
        if not unchanged:
            # Only a real change replaces the file, so a no-op save keeps the undo point.
            current = await _replace_binary(
                org_id, user_id, file_id, "docx",
                markdown_to_docx(content, title=stem(current["name"])), edited_by=edited_by,
            )
        if name is None:
            return current
        content = None
    if content is not None and kind in BUILT_KINDS:
        # Rebuild an Excel, PDF or PowerPoint file from new text (undoable).
        current = await _replace_binary(
            org_id, user_id, file_id, kind, build_binary(kind, content, current["name"]),
            edited_by=edited_by,
        )
        if name is None:
            return current
        content = None
    params: dict[str, Any] = {
        "org": org_id, "user": user_id, "id": file_id, "edited_by": edited_by,
    }
    sets = ["updated_at=now()", "last_edited_by=:edited_by"]
    if content is not None:
        if kind not in TEXT_KINDS:
            raise HTTPException(
                status_code=422,
                detail=f"{current['name']} is a .{kind} file; upload a new version instead.",
            )  # DOCX text edits were handled above.
        body, rows, columns = normalize_text(kind, content)
        params.update(content=body, rows=rows, columns=columns, size=len(body.encode("utf-8")))
        # Only remember the old content when it actually changes, so a no-op
        # save does not wipe out the undo point of a real edit.
        sets += [
            "previous_content=CASE WHEN content IS DISTINCT FROM :content "
            "THEN content ELSE previous_content END",
            "content=:content",
            "row_count=:rows",
            "column_count=:columns",
            "size_bytes=:size",
        ]
    if name is not None:
        params["name"] = normalize_name(name, kind)
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


async def replace_upload(
    org_id: str, user_id: str, file_id: str, filename: str, data: bytes
) -> dict[str, Any]:
    """Replace a file with a newly uploaded version of the same type (undoable)."""

    current = await get_file(org_id, user_id, file_id)
    kind = current["kind"]
    if (kind_from_name(filename) or OTHER_KIND) != kind:
        raise HTTPException(
            status_code=422, detail=f"Upload a .{kind} file to replace {current['name']}."
        )
    if kind in TEXT_KINDS:
        if len(data) > MAX_FILE_BYTES:
            raise HTTPException(status_code=413, detail="CSV and text files are limited to 1 MB.")
        return await update_file(org_id, user_id, file_id, content=_decode_text(data))
    return await _replace_binary(org_id, user_id, file_id, kind, data, edited_by="user")


async def _replace_binary(
    org_id: str, user_id: str, file_id: str, kind: str, data: bytes, *, edited_by: EditedBy
) -> dict[str, Any]:
    """Swap in new bytes for a binary file, keeping the old version for undo."""

    validate_binary(kind, data)
    blob = await _put_blob(org_id, file_id, kind, data)
    rows, columns = binary_stats(kind, data)
    factory = get_session_factory()
    async with factory() as session, session.begin():
        old = (await session.execute(text("""
            SELECT previous_blob_key FROM loombot_files
            WHERE org_id=:org AND owner_user_id=:user AND file_id=:id FOR UPDATE
        """), {"org": org_id, "user": user_id, "id": file_id})).scalar_one_or_none()
        row = (await session.execute(text(f"""
            UPDATE loombot_files
            SET previous_blob_key=blob_key, blob_key=:blob, size_bytes=:size,
                row_count=:rows, column_count=:columns,
                last_edited_by=:edited_by, updated_at=now()
            WHERE org_id=:org AND owner_user_id=:user AND file_id=:id
            RETURNING {_META_COLUMNS}, content
        """), {
            "org": org_id, "user": user_id, "id": file_id, "blob": blob,
            "size": len(data), "rows": rows, "columns": columns, "edited_by": edited_by,
        })).mappings().one_or_none()
    if row is None:
        await _delete_blobs(blob)
        raise HTTPException(status_code=404, detail="File not found.")
    await _delete_blobs(old)
    return _with_content(row)


async def undo_file(org_id: str, user_id: str, file_id: str) -> dict[str, Any]:
    """Swap current and previous versions; calling it again redoes the change."""

    factory = get_session_factory()
    async with factory() as session, session.begin():
        found = (await session.execute(text("""
            SELECT kind, previous_content, previous_blob_key FROM loombot_files
            WHERE org_id=:org AND owner_user_id=:user AND file_id=:id
            FOR UPDATE
        """), {"org": org_id, "user": user_id, "id": file_id})).mappings().one_or_none()
        if found is None:
            raise HTTPException(status_code=404, detail="File not found.")
        kind = found["kind"] or "csv"
        params = {"org": org_id, "user": user_id, "id": file_id}
        if kind in TEXT_KINDS:
            if found["previous_content"] is None:
                raise HTTPException(status_code=409, detail="There is no change to undo.")
            body, rows, columns = normalize_text(kind, found["previous_content"])
            swap = "content=previous_content, previous_content=content, size_bytes=:size"
            params["size"] = len(body.encode("utf-8"))
        else:
            if found["previous_blob_key"] is None:
                raise HTTPException(status_code=409, detail="There is no change to undo.")
            from core.blob_storage import get_blob_storage

            data = await get_blob_storage().get(found["previous_blob_key"])
            rows, columns = binary_stats(kind, data)
            swap = "blob_key=previous_blob_key, previous_blob_key=blob_key, size_bytes=:size"
            params["size"] = len(data)
        row = (await session.execute(text(f"""
            UPDATE loombot_files
            SET {swap}, row_count=:rows, column_count=:columns,
                last_edited_by='user', updated_at=now()
            WHERE org_id=:org AND owner_user_id=:user AND file_id=:id
            RETURNING {_META_COLUMNS}, content
        """), {**params, "rows": rows, "columns": columns})).mappings().one()
    return _with_content(row)


async def delete_file(org_id: str, user_id: str, file_id: str) -> None:
    factory = get_session_factory()
    async with factory() as session, session.begin():
        deleted = (await session.execute(text("""
            DELETE FROM loombot_files
            WHERE org_id=:org AND owner_user_id=:user AND file_id=:id
            RETURNING blob_key, previous_blob_key
        """), {"org": org_id, "user": user_id, "id": file_id})).mappings().one_or_none()
    if deleted is None:
        raise HTTPException(status_code=404, detail="File not found.")
    async with factory() as session, session.begin():
        await session.execute(text(
            "DELETE FROM workspace_files WHERE org_id=:org AND file_id=:id"
        ), {"org": org_id, "id": file_id})
    await _delete_blobs(deleted["blob_key"], deleted["previous_blob_key"])


def unique_name(existing: set[str], wanted: str) -> str:
    """``wanted``, or ``wanted (2).ext`` etc. when the name is taken (case-insensitive)."""

    taken = {name.lower() for name in existing}
    if wanted.lower() not in taken:
        return wanted
    base, dot, extension = wanted.rpartition(".")
    if not dot or not base or " " in extension or len(extension) > 10:
        base, extension = wanted, ""
    for n in range(2, 100):
        candidate = f"{base} ({n}).{extension}" if extension else f"{base} ({n})"
        if candidate.lower() not in taken:
            return candidate
    tag = uuid4().hex[:6]
    return f"{base} {tag}.{extension}" if extension else f"{base} {tag}"


# --- Sharing ------------------------------------------------------------------------

MAX_SHARE_RECIPIENTS = 25
MAX_EMAIL_ATTACHMENT_BYTES = 20_000_000  # Gmail's 25 MB limit, less MIME overhead


async def share_in_mindloom(
    org_id: str,
    user_id: str,
    file_id: str,
    recipient_user_ids: list[str],
    *,
    note: str | None = None,
) -> dict[str, Any]:
    """Copy a file into colleagues' Files, marked with who shared it.

    Each recipient gets an independent copy (later edits do not sync either way)
    and a short notification in their Files tab until they see it.
    """

    wanted = [uid for uid in dict.fromkeys(str(u).strip() for u in recipient_user_ids) if uid]
    wanted = [uid for uid in wanted if uid != user_id]
    if not wanted:
        raise HTTPException(status_code=400, detail="Choose someone other than yourself to share with.")
    if len(wanted) > MAX_SHARE_RECIPIENTS:
        raise HTTPException(status_code=400, detail=f"Share with at most {MAX_SHARE_RECIPIENTS} people at once.")
    data, meta = await get_file_bytes(org_id, user_id, file_id)
    clean_note = " ".join((note or "").split())[:300] or None
    factory = get_session_factory()
    async with factory() as session:
        people = (await session.execute(text("""
            SELECT user_id, coalesce(name, email) AS name, email FROM users
            WHERE org_id=:org AND user_id = ANY(:ids)
        """), {"org": org_id, "ids": wanted})).mappings().all()
        sharer = (await session.execute(text("""
            SELECT coalesce(name, email) FROM users WHERE org_id=:org AND user_id=:user
        """), {"org": org_id, "user": user_id})).scalar_one_or_none() or "A colleague"
    found = {str(row["user_id"]): dict(row) for row in people}
    shared: list[dict[str, Any]] = []
    for recipient_id in wanted:
        person = found.get(recipient_id)
        if person is None:
            continue
        existing = {f["name"] for f in await list_files(org_id, recipient_id)}
        name = unique_name(existing, meta["name"])
        new_id = str(uuid4())
        kind = meta["kind"]
        blob = None
        if kind in TEXT_KINDS:
            body, rows, columns = normalize_text(kind, data.decode("utf-8"))
            content, size = body, len(body.encode("utf-8"))
        else:
            blob = await _put_blob(org_id, new_id, kind, data)
            rows, columns = meta["row_count"], meta["column_count"]
            content, size = "", len(data)
        async with factory() as session, session.begin():
            await session.execute(text("""
                INSERT INTO loombot_files
                  (file_id, org_id, owner_user_id, name, kind, mime_type, size_bytes,
                   content, blob_key, row_count, column_count, last_edited_by,
                   shared_by_user_id, shared_by_name, shared_note, shared_at,
                   created_at, updated_at)
                VALUES (:id, :org, :owner, :name, :kind, :mime, :size, :content, :blob,
                        :rows, :columns, 'user', :by, :by_name, :note, now(), now(), now())
            """), {
                "id": new_id, "org": org_id, "owner": recipient_id, "name": name,
                "kind": kind, "mime": KINDS[kind], "size": size, "content": content,
                "blob": blob, "rows": rows, "columns": columns, "by": user_id,
                "by_name": sharer, "note": clean_note,
            })
        shared.append({"user_id": recipient_id, "name": person["name"], "email": person["email"],
                       "file_name": name})
    return {"shared": shared, "not_found": [uid for uid in wanted if uid not in found]}


async def share_file(
    org_id: str,
    user_id: str,
    file_id: str,
    recipients: list[str],
    *,
    send_email: bool = True,
    in_mindloom: bool = False,
    message: str | None = None,
) -> dict[str, Any]:
    """Share a file by email (attached, from the sharer's Gmail) and/or in MindLoom.

    "In MindLoom" copies the file into the Files tab of every recipient who is a
    signed-in member of the organization; others can only receive it by email.
    """

    from keeper.review_workflows import parse_recipients

    if not send_email and not in_mindloom:
        raise HTTPException(status_code=400, detail="Choose email, Share in MindLoom, or both.")
    addresses = parse_recipients(", ".join(recipients))
    meta = await get_file(org_id, user_id, file_id)
    factory = get_session_factory()
    async with factory() as session:
        members = (await session.execute(text("""
            SELECT user_id, coalesce(name, email) AS name, lower(email) AS email FROM users
            WHERE org_id=:org AND lower(email) = ANY(:emails)
        """), {"org": org_id, "emails": [a.lower() for a in addresses]})).mappings().all()
        sharer = (await session.execute(text("""
            SELECT coalesce(name, email) FROM users WHERE org_id=:org AND user_id=:user
        """), {"org": org_id, "user": user_id})).scalar_one_or_none() or "A colleague"
    by_email = {str(m["email"]): dict(m) for m in members if str(m["user_id"]) != user_id}
    result: dict[str, Any] = {"emailed": [], "shared_in_mindloom": [], "not_in_mindloom": []}

    if in_mindloom:
        result["not_in_mindloom"] = [a for a in addresses if a.lower() not in by_email]
        if by_email:
            shared = await share_in_mindloom(
                org_id, user_id, file_id, [m["user_id"] for m in by_email.values()], note=message,
            )
            result["shared_in_mindloom"] = shared["shared"]
    if send_email:
        from shuttle.notification_delivery import email_send_provider, send_user_email

        provider = await email_send_provider(org_id, user_id)
        if provider is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Connect Google Workspace or Microsoft 365 to share by email, "
                    "or use Share in MindLoom."
                ),
            )
        attachments = await files_as_attachments(org_id, user_id, [file_id])
        note = (message or "").strip()
        body = f"{sharer} shared {meta['name']} with you. It is attached to this email."
        if note:
            body += f"\n\n{note}"
        try:
            # One email per person, so recipients do not see who else got the file.
            for address in addresses:
                await send_user_email(
                    org_id=org_id, user_id=user_id, recipient=address,
                    subject=f"{sharer} shared {meta['name']} with you", body=body,
                    attachments=attachments, provider=provider,
                )
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        result["emailed"] = addresses
    return result


async def unseen_shares(org_id: str, user_id: str) -> list[dict[str, Any]]:
    """Files colleagues shared with this user that they have not seen yet."""

    factory = get_session_factory()
    async with factory() as session:
        rows = (await session.execute(text("""
            SELECT file_id, name, kind, shared_by_name, shared_note, shared_at
            FROM loombot_files
            WHERE org_id=:org AND owner_user_id=:user
              AND shared_at IS NOT NULL AND share_seen_at IS NULL
            ORDER BY shared_at DESC LIMIT 20
        """), {"org": org_id, "user": user_id})).mappings().all()
    return [dict(row) for row in rows]


async def mark_shares_seen(org_id: str, user_id: str, file_ids: list[str] | None = None) -> int:
    """Clear share notifications (all of them, or just ``file_ids``)."""

    factory = get_session_factory()
    params: dict[str, Any] = {"org": org_id, "user": user_id}
    only = ""
    if file_ids:
        only = "AND file_id = ANY(:ids)"
        params["ids"] = [str(f) for f in file_ids]
    async with factory() as session, session.begin():
        result = await session.execute(text(f"""
            UPDATE loombot_files SET share_seen_at=now()
            WHERE org_id=:org AND owner_user_id=:user
              AND shared_at IS NOT NULL AND share_seen_at IS NULL {only}
        """), params)
    return int(result.rowcount or 0)


async def files_as_attachments(
    org_id: str, user_id: str, file_ids: list[str]
) -> list[tuple[str, bytes, str]]:
    """(file name, bytes, MIME type) for each of the user's files, for an email."""

    attachments: list[tuple[str, bytes, str]] = []
    total = 0
    for file_id in dict.fromkeys(str(f) for f in file_ids if str(f).strip()):
        data, meta = await get_file_bytes(org_id, user_id, file_id)
        total += len(data)
        if total > MAX_EMAIL_ATTACHMENT_BYTES:
            raise HTTPException(
                status_code=413, detail="Attachments are limited to 20 MB per email."
            )
        attachments.append((meta["name"], data, meta["mime_type"]))
    return attachments


# --- Monitoring ---------------------------------------------------------------------


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
    that arrives after monitoring starts is applied. CSV files and Excel workbooks
    (their first sheet) can be monitored.
    """

    current = await get_file(org_id, user_id, file_id)
    if current["kind"] not in MONITORABLE_KINDS and (enabled or instructions):
        raise HTTPException(
            status_code=422,
            detail="Automatic updates are available for CSV files and Excel workbooks.",
        )
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

        if current["kind"] == "xlsx":
            from loombot.file_monitor import xlsx_matrix

            data, _ = await get_file_bytes(org_id, user_id, file_id)
            table = xlsx_matrix(data)
            headers = " ".join(table[0]) if table else ""
            if not table:
                raise HTTPException(
                    status_code=422,
                    detail="Add a header row to the first sheet before turning on automatic updates.",
                )
        else:
            headers = " ".join(_headers(current["content"]))
        base = stem(current["name"])
        params["keywords"] = json.dumps(extract_keywords(final, base.replace("-", " ").replace("_", " ")))
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


# --- Reading content for Ask and previews ---------------------------------------------


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat() if value.time() == datetime.min.time() else value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def docx_blocks(data: bytes) -> list[dict[str, Any]]:
    """Headings, paragraphs, and tables of a Word document, in order."""

    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    from loombot.docx_builder import paragraph_markdown

    document = docx.Document(io.BytesIO(data))
    blocks: list[dict[str, Any]] = []
    for element in document.element.body.iterchildren():
        tag = element.tag.rsplit("}", 1)[-1]
        if tag == "p":
            paragraph = Paragraph(element, document)
            value = paragraph.text.strip()
            if not value:
                continue
            style = (paragraph.style.name if paragraph.style is not None else "") or ""
            match = re.match(r"heading\s*(\d)", style.strip().lower())
            if match or style.lower() == "title":
                blocks.append({"type": "heading", "level": int(match.group(1)) if match else 1, "text": value})
                continue
            # "markdown" keeps bold, italics, and links so editing as text keeps them.
            formatted = paragraph_markdown(paragraph)
            if style.lower().startswith("list"):
                item = {"type": "list_item", "text": value}
                if style.lower().startswith("list number"):
                    item["ordered"] = True
            else:
                item = {"type": "paragraph", "text": value}
            if formatted and formatted != value:
                item["markdown"] = formatted
            blocks.append(item)
        elif tag == "tbl":
            table = Table(element, document)
            blocks.append({
                "type": "table",
                "rows": [[cell.text.strip() for cell in row.cells] for row in table.rows],
            })
    return blocks


def xlsx_sheets(data: bytes, *, max_rows: int = 500, max_cols: int = 50) -> list[dict[str, Any]]:
    """Each sheet's cell values (computed values where Excel saved them)."""

    import openpyxl

    workbook = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    try:
        sheets = []
        for sheet in workbook.worksheets:
            rows: list[list[str]] = []
            truncated = False
            for index, row in enumerate(sheet.iter_rows(values_only=True)):
                if index >= max_rows:
                    truncated = True
                    break
                values = [_cell_text(v) for v in row[:max_cols]]
                if len(row) > max_cols:
                    truncated = True
                rows.append(values)
            while rows and not any(rows[-1]):
                rows.pop()
            width = max((len(r) for r in rows), default=0)
            while width and not any(r[width - 1] if len(r) >= width else "" for r in rows):
                width -= 1
            sheets.append({
                "name": sheet.title,
                "rows": [r[:width] + [""] * (width - len(r[:width])) for r in rows],
                "truncated": truncated,
            })
        return sheets
    finally:
        workbook.close()


# Vision transcriptions of pictures and scanned PDFs, keyed by file version.
_OCR_CACHE: dict[tuple[str, str], str] = {}
_OCR_CACHE_SIZE = 64


async def _text_with_ocr(file: dict[str, Any], data: bytes) -> str:
    """A PDF's text, or a picture's or scanned PDF's text read by the vision model."""

    from brain.media_text import extract_text_with_ocr, no_text_reason, pdf_is_scanned

    if file["kind"] == "pdf" and not pdf_is_scanned(data):
        return pdf_text(data)
    key = (str(file.get("file_id")), str(file.get("updated_at")))
    if key in _OCR_CACHE:
        return _OCR_CACHE[key]
    extracted, method = await extract_text_with_ocr(file["name"], data)
    result = extracted or f"({no_text_reason(file['name'], data)})"
    if method == "ocr":
        result = f"(Read from the {'scan' if file['kind'] == 'pdf' else 'picture'} with OCR.)\n{result}"
        if len(_OCR_CACHE) >= _OCR_CACHE_SIZE:
            _OCR_CACHE.pop(next(iter(_OCR_CACHE)))
        _OCR_CACHE[key] = result
    return result


async def set_template(org_id: str, user_id: str, file_id: str, is_template: bool) -> dict[str, Any]:
    """Mark or unmark a file as a template."""

    factory = get_session_factory()
    async with factory() as session, session.begin():
        row = (await session.execute(text(f"""
            UPDATE loombot_files SET is_template=:flag
            WHERE org_id=:org AND owner_user_id=:user AND file_id=:id
            RETURNING {_META_COLUMNS}
        """), {"org": org_id, "user": user_id, "id": file_id, "flag": is_template})).mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="File not found.")
    return _meta(row)


async def extract_zip_to_files(org_id: str, user_id: str, file_id: str) -> dict[str, Any]:
    """Unpack a ZIP in Files into separate files (names made unique; the ZIP stays)."""

    from brain.archives import ArchiveError, read_archive

    data, meta = await get_file_bytes(org_id, user_id, file_id)
    try:
        contents = read_archive(data)
    except ArchiveError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    created: list[dict[str, Any]] = []
    skipped = list(contents.skipped)
    for entry in contents.entries:
        try:
            created.append(await upload_file(org_id, user_id, entry.name, entry.data, auto_rename=True))
        except HTTPException as exc:
            skipped.append({"path": entry.path, "reason": str(exc.detail)})
    return {
        "archive": meta["name"],
        "created": [{k: v for k, v in f.items() if k != "content"} for f in created],
        "skipped": skipped,
    }


def zip_text(name: str, data: bytes, *, max_chars: int = 120_000) -> str:
    """The text of every readable file in a ZIP, each under its path."""

    from brain.archives import read_archive
    from brain.file_extract import extract_file_text

    readable = (".pdf", ".docx", ".pptx", ".xlsx", ".csv", ".txt", ".md", ".log", ".json",
                ".jsonl", ".html", ".htm", ".xml")
    contents = read_archive(data, accept=lambda path: path.lower().endswith(readable))
    parts: list[str] = []
    size = 0
    for entry in contents.entries:
        try:
            text_part = extract_file_text(entry.name, entry.data).strip()
        except Exception:  # noqa: BLE001 - one bad member must not hide the others
            continue
        if not text_part:
            continue
        block = f"[{entry.path}]\n{text_part}"
        parts.append(block)
        size += len(block)
        if size >= max_chars:
            break
    listed = ", ".join(s["path"] for s in contents.skipped[:20])
    if listed:
        parts.append(f"(Not read from {name}: {listed})")
    return "\n\n".join(parts)[:max_chars] or f"({name} has no readable documents.)"


def other_file_text(name: str, data: bytes) -> str:
    """Text of a file of another type when it is text (HTML, XML, code, logs), or the
    documents inside a ZIP archive."""

    from brain.archives import ArchiveError, is_zip

    if is_zip(name, data):
        try:
            return zip_text(name, data)
        except ArchiveError as exc:
            return f"({name}: {exc})"
    if b"\x00" in data[:4096]:
        return f"({name} is a binary file; it can be downloaded or attached but not read.)"
    from brain.file_extract import extract_file_text

    return extract_file_text(name, data)


def pptx_text(data: bytes) -> str:
    from loombot.file_builders import pptx_slides

    out: list[str] = []
    for slide in pptx_slides(data):
        out.append(f"[Slide {slide['number']}] {slide['title']}".rstrip())
        out.extend(slide["lines"])
        if slide["notes"]:
            out.append(f"Notes: {slide['notes']}")
    return "\n".join(out)


def pdf_text(data: bytes) -> str:
    import fitz

    with fitz.open(stream=data, filetype="pdf") as doc:
        return "\n\n".join(
            f"[Page {i + 1}]\n{page.get_text('text').strip()}" for i, page in enumerate(doc)
        )


async def read_text_for_ask(org_id: str, user_id: str, file: dict[str, Any]) -> str:
    """Readable text of any file kind, for Ask to answer from."""

    kind = file["kind"]
    if kind in TEXT_KINDS:
        return file.get("content") or ""
    data, _ = await get_file_bytes(org_id, user_id, file["file_id"])
    if kind in IMAGE_KINDS or kind == "pdf":
        return await _text_with_ocr(file, data)
    if kind == "docx":
        from loombot.docx_builder import blocks_to_markdown

        return blocks_to_markdown(docx_blocks(data))
    if kind == "pptx":
        return pptx_text(data)
    if kind == OTHER_KIND:
        return other_file_text(file["name"], data)
    out: list[str] = []
    for sheet in xlsx_sheets(data, max_rows=2000):
        out.append(f"[Sheet: {sheet['name']}]")
        buffer = io.StringIO()
        csv.writer(buffer, lineterminator="\n").writerows(sheet["rows"])
        out.append(buffer.getvalue().rstrip())
    return "\n\n".join(out)


async def preview_file(org_id: str, user_id: str, file_id: str) -> dict[str, Any]:
    """Structured preview for the Files tab (DOCX blocks or XLSX sheets)."""

    data, meta = await get_file_bytes(org_id, user_id, file_id)
    if meta["kind"] == "docx":
        from loombot.docx_builder import blocks_to_markdown

        blocks = docx_blocks(data)
        return {"kind": "docx", "blocks": blocks, "markdown": blocks_to_markdown(blocks)}
    if meta["kind"] == "xlsx":
        return {"kind": "xlsx", "sheets": xlsx_sheets(data)}
    if meta["kind"] == "pdf":
        rows, _ = binary_stats("pdf", data)
        return {"kind": "pdf", "pages": rows}
    if meta["kind"] == "pptx":
        from loombot.file_builders import pptx_slides

        return {"kind": "pptx", "slides": pptx_slides(data)}
    if meta["kind"] in IMAGE_KINDS:
        return {"kind": "image", "height": meta["row_count"], "width": meta["column_count"]}
    if meta["kind"] == OTHER_KIND:
        from brain.archives import ArchiveError, is_zip, list_archive

        if is_zip(meta["name"], data):
            try:
                return {"kind": "zip", "entries": list_archive(data)}
            except ArchiveError:
                pass
        text_content = None if b"\x00" in data[:4096] else data[:200_000].decode("utf-8", "replace")
        return {"kind": "file", "mime_type": meta["mime_type"], "text": text_content}
    return {"kind": meta["kind"], "content": data.decode("utf-8")}


# --- Signed links (for opening a file in Excel) -------------------------------------


def issue_file_link(org_id: str, user_id: str, file_id: str) -> tuple[str, datetime]:
    """A short-lived token that lets a desktop app fetch one file without a session."""

    import jwt

    from core.config import get_settings

    expires = datetime.now(timezone.utc) + LINK_TTL
    token = jwt.encode(
        {"sub": user_id, "org": org_id, "fid": file_id, "aud": _LINK_AUDIENCE, "exp": expires},
        get_settings().resolved_session_secret,
        algorithm="HS256",
    )
    return token, expires


def verify_file_link(token: str, file_id: str) -> tuple[str, str]:
    """Return (org_id, user_id) for a valid link token for ``file_id``."""

    import jwt

    from core.config import get_settings

    try:
        payload = jwt.decode(
            token, get_settings().resolved_session_secret,
            algorithms=["HS256"], audience=_LINK_AUDIENCE,
        )
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status_code=401, detail="This link has expired. Open the file again.") from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail="Invalid file link.") from exc
    if payload.get("fid") != file_id or not payload.get("org") or not payload.get("sub"):
        raise HTTPException(status_code=401, detail="Invalid file link.")
    return str(payload["org"]), str(payload["sub"])
