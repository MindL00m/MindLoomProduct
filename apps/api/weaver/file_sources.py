"""Browse Google Drive and email attachments from the Files tab.

Google Drive is read live with the user's own connection, so they see exactly
what Drive shows them. Google Docs, Sheets, and Slides are exported to Word,
Excel, and PDF for previews, downloads, and copies into Files. Email
attachments are the ones MindLoom read from the user's own mailboxes
(Gmail and Outlook syncs); their originals are fetched live from the mailbox.
"""

from __future__ import annotations

import csv
import io
import json
import logging
from typing import Any

import httpx
from fastapi import HTTPException

logger = logging.getLogger(__name__)

DRIVE_API = "https://www.googleapis.com/drive/v3"
FOLDER = "application/vnd.google-apps.folder"
SHORTCUT = "application/vnd.google-apps.shortcut"
DRIVE_SCOPES = {
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/drive",
}
GOOGLE_EXPORTS = {
    "application/vnd.google-apps.document": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx",
    ),
    "application/vnd.google-apps.spreadsheet": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx",
    ),
    "application/vnd.google-apps.presentation": ("application/pdf", "pdf"),
    "application/vnd.google-apps.drawing": ("application/pdf", "pdf"),
}
_MIME_KINDS = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "text/csv": "csv",
    "text/plain": "txt",
    "text/markdown": "txt",
}
_GOOGLE_LABELS = {
    "application/vnd.google-apps.document": "Google Doc",
    "application/vnd.google-apps.spreadsheet": "Google Sheet",
    "application/vnd.google-apps.presentation": "Google Slides",
    "application/vnd.google-apps.drawing": "Google Drawing",
    FOLDER: "Folder",
}
MAX_PREVIEW_BYTES = 15_000_000
MAX_DOWNLOAD_BYTES = 50_000_000
MAX_TEXT_PREVIEW_CHARS = 200_000
_DRIVE_FIELDS = (
    "id,name,mimeType,modifiedTime,size,webViewLink,iconLink,parents,"
    "owners(displayName,emailAddress),shortcutDetails(targetId,targetMimeType)"
)


# --- Google Drive -------------------------------------------------------------------


def _drive_kind(mime: str, name: str) -> str:
    if mime == FOLDER:
        return "folder"
    if mime in GOOGLE_EXPORTS:
        return GOOGLE_EXPORTS[mime][1]
    if mime in _MIME_KINDS:
        return _MIME_KINDS[mime]
    if mime.startswith("image/"):
        return "image"
    lowered = name.lower()
    for suffix, kind in ((".pdf", "pdf"), (".docx", "docx"), (".xlsx", "xlsx"), (".csv", "csv"),
                         (".txt", "txt"), (".md", "txt"), (".pptx", "pptx")):
        if lowered.endswith(suffix):
            return kind
    return "other"


async def _drive_token(org_id: str, user_id: str) -> str:
    from weaver.google_workspace import PROVIDER_GOOGLE_WORKSPACE, _workspace_access_token
    from weaver.integrations import _get_connection

    row = await _get_connection(org_id, user_id, PROVIDER_GOOGLE_WORKSPACE)
    if row is None:
        raise HTTPException(
            status_code=400, detail="Connect Google Workspace in Apps to see your Google Drive here."
        )
    scopes = str(row.scopes or "")
    if scopes != "dev" and not set(scopes.split()) & DRIVE_SCOPES:
        raise HTTPException(
            status_code=400,
            detail="Reconnect Google Workspace in Apps to let MindLoom read your Google Drive.",
        )
    token, _account = await _workspace_access_token(org_id, user_id)
    if token.startswith("dev:"):
        raise HTTPException(
            status_code=400,
            detail="Google Workspace is connected in development mode, so there is no real Drive to show.",
        )
    return token


def _drive_item(item: dict[str, Any]) -> dict[str, Any]:
    mime = str(item.get("mimeType") or "")
    name = str(item.get("name") or "Untitled")
    shortcut = item.get("shortcutDetails") or {}
    if mime == SHORTCUT and shortcut.get("targetId"):
        mime = str(shortcut.get("targetMimeType") or "")
    kind = _drive_kind(mime, name)
    owner = (item.get("owners") or [{}])[0]
    return {
        "id": str(shortcut.get("targetId") or item.get("id")),
        "name": name,
        "mime_type": mime,
        "kind": kind,
        "type_label": _GOOGLE_LABELS.get(mime) or (kind.upper() if kind not in {"other", "image"} else
                                                   ("Image" if kind == "image" else "File")),
        "modified_at": item.get("modifiedTime"),
        "size_bytes": int(item["size"]) if str(item.get("size") or "").isdigit() else None,
        "web_view_link": item.get("webViewLink"),
        "owner": owner.get("displayName") or owner.get("emailAddress"),
        "can_preview": kind in {"docx", "xlsx", "pdf", "csv", "txt", "pptx", "image"},
        "can_import": kind in {"docx", "xlsx", "pdf", "csv", "txt"},
    }


def _quote(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


async def list_drive_files(
    org_id: str,
    user_id: str,
    *,
    folder_id: str | None = None,
    search: str | None = None,
    page_token: str | None = None,
) -> dict[str, Any]:
    """One page of the user's Drive: recent files, a folder's contents, or a name search."""

    token = await _drive_token(org_id, user_id)
    query = ["trashed = false"]
    cleaned = " ".join((search or "").split())[:200]
    if cleaned:
        query.append(f"name contains '{_quote(cleaned)}'")
        order = "modifiedTime desc"
    elif folder_id:
        query.append(f"'{_quote(folder_id)}' in parents")
        order = "folder,name"
    else:
        query.append(f"mimeType != '{FOLDER}'")
        order = "modifiedTime desc"
    params = {
        "q": " and ".join(query),
        "orderBy": order,
        "pageSize": "50",
        "fields": f"nextPageToken,files({_DRIVE_FIELDS})",
        "supportsAllDrives": "true",
        "includeItemsFromAllDrives": "true",
        "corpora": "allDrives",
    }
    if page_token:
        params["pageToken"] = page_token
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            f"{DRIVE_API}/files", params=params, headers={"Authorization": f"Bearer {token}"}
        )
    if response.status_code in (401, 403):
        raise HTTPException(status_code=400, detail="Google Drive refused the request. Reconnect Google Workspace in Apps.")
    if response.status_code >= 400:
        logger.warning("Drive list failed %s: %s", response.status_code, response.text[:300])
        raise HTTPException(status_code=502, detail="Could not list your Google Drive.")
    payload = response.json()
    return {
        "files": [_drive_item(item) for item in payload.get("files") or []],
        "next_page_token": payload.get("nextPageToken"),
        "folder_id": folder_id,
        "search": cleaned or None,
    }


async def _drive_metadata(client: httpx.AsyncClient, token: str, file_id: str) -> dict[str, Any]:
    response = await client.get(
        f"{DRIVE_API}/files/{file_id}",
        params={"fields": _DRIVE_FIELDS, "supportsAllDrives": "true"},
        headers={"Authorization": f"Bearer {token}"},
    )
    if response.status_code == 404:
        raise HTTPException(status_code=404, detail="That Drive file was not found.")
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail="Could not read that Drive file.")
    return response.json()


async def drive_file_bytes(
    org_id: str, user_id: str, file_id: str, *, limit: int = MAX_DOWNLOAD_BYTES
) -> tuple[bytes, dict[str, Any]]:
    """A Drive file's bytes (Google formats exported) and its item details."""

    token = await _drive_token(org_id, user_id)
    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
        meta = await _drive_metadata(client, token, file_id)
        item = _drive_item(meta)
        mime = item["mime_type"]
        if mime == FOLDER:
            raise HTTPException(status_code=400, detail="That is a folder.")
        if mime in GOOGLE_EXPORTS:
            export_mime, suffix = GOOGLE_EXPORTS[mime]
            response = await client.get(
                f"{DRIVE_API}/files/{item['id']}/export",
                params={"mimeType": export_mime},
                headers={"Authorization": f"Bearer {token}"},
            )
            item["download_name"] = item["name"] if item["name"].lower().endswith(f".{suffix}") \
                else f"{item['name']}.{suffix}"
            item["download_mime"] = export_mime
        else:
            if item["size_bytes"] and item["size_bytes"] > limit:
                raise HTTPException(status_code=413, detail="This file is too large to open here; open it in Drive.")
            response = await client.get(
                f"{DRIVE_API}/files/{item['id']}",
                params={"alt": "media", "supportsAllDrives": "true"},
                headers={"Authorization": f"Bearer {token}"},
            )
            item["download_name"] = item["name"]
            item["download_mime"] = mime or "application/octet-stream"
    if response.status_code == 403 and "exportSizeLimitExceeded" in response.text:
        raise HTTPException(status_code=413, detail="Google cannot export a file this large; open it in Drive.")
    if response.status_code >= 400:
        logger.warning("Drive download failed %s: %s", response.status_code, response.text[:300])
        raise HTTPException(status_code=502, detail="Could not download that Drive file.")
    if len(response.content) > limit:
        raise HTTPException(status_code=413, detail="This file is too large to open here; open it in Drive.")
    return response.content, item


def preview_bytes(kind: str, data: bytes, name: str) -> dict[str, Any]:
    """The Files-tab preview shape for some bytes (shared by Drive and email)."""

    from loombot import files as files_service
    from loombot.docx_builder import blocks_to_markdown

    if kind == "docx":
        blocks = files_service.docx_blocks(data)
        return {"kind": "docx", "blocks": blocks, "markdown": blocks_to_markdown(blocks)}
    if kind == "xlsx":
        return {"kind": "xlsx", "sheets": files_service.xlsx_sheets(data)}
    if kind == "pdf":
        pages, _ = files_service.binary_stats("pdf", data)
        return {"kind": "pdf", "pages": pages}
    if kind == "image":
        return {"kind": "image"}
    if kind == "csv":
        text = data.decode("utf-8-sig", errors="replace")[:MAX_TEXT_PREVIEW_CHARS]
        rows = list(csv.reader(io.StringIO(text)))[:500]
        return {"kind": "xlsx", "sheets": [{"name": name, "rows": rows, "truncated": len(rows) >= 500}]}
    from brain.file_extract import extract_file_text

    text = extract_file_text(name, data)
    return {"kind": "text", "content": text[:MAX_TEXT_PREVIEW_CHARS]}


async def drive_preview(org_id: str, user_id: str, file_id: str) -> dict[str, Any]:
    data, item = await drive_file_bytes(org_id, user_id, file_id, limit=MAX_PREVIEW_BYTES)
    if not item["can_preview"]:
        return {"kind": "unsupported", "item": item}
    return {**preview_bytes(item["kind"], data, item["download_name"]), "item": item}


async def import_drive_file(org_id: str, user_id: str, file_id: str) -> dict[str, Any]:
    """Save a copy of a Drive file into the user's Files tab."""

    from loombot import files as files_service

    data, item = await drive_file_bytes(org_id, user_id, file_id, limit=files_service.MAX_UPLOAD_BYTES)
    name = item["download_name"]
    if files_service.kind_from_name(name) is None:
        raise HTTPException(
            status_code=422,
            detail="Only Docs, Sheets, Slides, PDF, Word, Excel, CSV, and text files can be saved to Files.",
        )
    existing = {f["name"] for f in await files_service.list_files(org_id, user_id)}
    return await files_service.upload_file(
        org_id, user_id, name, data, files_service.unique_name(existing, name)
    )


# --- Email attachments ----------------------------------------------------------------


async def _mailbox_accounts(org_id: str, user_id: str) -> dict[str, str]:
    """{lower-cased mailbox address: provider} for the user's connected mailboxes."""

    from weaver.google_workspace import PROVIDER_GOOGLE_WORKSPACE
    from weaver.integrations import _get_connection
    from weaver.microsoft_teams import PROVIDER_MICROSOFT_TEAMS

    accounts: dict[str, str] = {}
    for provider, label in ((PROVIDER_GOOGLE_WORKSPACE, "gmail"), (PROVIDER_MICROSOFT_TEAMS, "outlook")):
        row = await _get_connection(org_id, user_id, provider)
        if row is not None and row.account_email:
            accounts[str(row.account_email).lower()] = label
    return accounts


def split_attachment_source_id(external_id: str) -> dict[str, Any]:
    """account:message_id:index:filename (the id ingest_email_attachment registers)."""

    account, message_id, index, filename = (external_id.split(":", 3) + ["", "", "", ""])[:4]
    return {
        "account": account,
        "message_id": message_id,
        "index": int(index) if index.isdigit() else 0,
        "filename": filename,
    }


async def _attachment_rows(
    org_id: str, accounts: list[str], *, document_id: str | None = None, limit: int = 300
) -> list[dict[str, Any]]:
    from sqlalchemy import text

    from core.database import get_session_factory

    if not accounts:
        return []
    conditions = " OR ".join(f"starts_with(external_id, :a{i})" for i in range(len(accounts)))
    params: dict[str, Any] = {
        "org": org_id, "limit": limit,
        **{f"a{i}": f"{account}:" for i, account in enumerate(accounts)},
    }
    only = ""
    if document_id:
        only = "AND document_id = :doc"
        params["doc"] = document_id
    factory = get_session_factory()
    async with factory() as session:
        rows = (await session.execute(text(f"""
            SELECT external_id, document_id, created_at FROM external_sources
            WHERE org_id=:org AND provider='email_attachment' AND status='active'
              AND document_id IS NOT NULL AND ({conditions}) {only}
            ORDER BY created_at DESC LIMIT :limit
        """), params)).mappings().all()
    return [dict(row) for row in rows]


_DOCUMENTS_CYPHER = """
MATCH (d:Document {org_id: $org_id})
WHERE d.document_id IN $ids
RETURN d.document_id AS document_id, d.title AS title, d.author AS author,
       d.source_location AS source_location, d.source_created_at AS sent_at,
       d.source_application AS application, d.source_url AS url,
       d.storage_path AS storage_path
"""


async def _documents(org_id: str, ids: list[str]) -> dict[str, dict[str, Any]]:
    from core.database import get_neo4j_driver
    from loombot.data_tools import jsonable

    if not ids:
        return {}

    async def read(tx):  # type: ignore[no-untyped-def]
        result = await tx.run(_DOCUMENTS_CYPHER, org_id=org_id, ids=ids)
        return [record.data() async for record in result]

    async with get_neo4j_driver().session() as session:
        records = await session.execute_read(read)
    return {str(r["document_id"]): jsonable(r) for r in records}


def _attachment_item(row: dict[str, Any], doc: dict[str, Any], provider: str) -> dict[str, Any]:
    from loombot import files as files_service
    from weaver.email_content import attachment_kind

    parts = split_attachment_source_id(str(row["external_id"]))
    filename = str(doc.get("title") or parts["filename"] or "attachment")
    location = str(doc.get("source_location") or "")
    subject = location.split(": ", 1)[1] if ": " in location else ""
    return {
        "id": str(row["document_id"]),
        "filename": filename,
        "kind": attachment_kind(filename) or "other",
        "file_kind": files_service.kind_from_name(filename),
        "from": doc.get("author"),
        "subject": subject,
        "sent_at": doc.get("sent_at") or row.get("created_at"),
        "provider": provider,
        "account": parts["account"],
        "message_id": parts["message_id"],
        "email_url": doc.get("url"),
    }


async def list_email_attachments(
    org_id: str, user_id: str, *, search: str | None = None
) -> dict[str, Any]:
    """Attachments MindLoom read from the user's own mailboxes, newest first."""

    accounts = await _mailbox_accounts(org_id, user_id)
    rows = await _attachment_rows(org_id, list(accounts))
    docs = await _documents(org_id, [str(r["document_id"]) for r in rows])
    needle = " ".join((search or "").lower().split())
    items = []
    for row in rows:
        doc = docs.get(str(row["document_id"]))
        if doc is None:
            continue
        item = _attachment_item(row, doc, accounts.get(split_attachment_source_id(row["external_id"])["account"], "gmail"))
        haystack = f"{item['filename']} {item['subject']} {item['from']}".lower()
        if needle and needle not in haystack:
            continue
        items.append(item)
    return {
        "attachments": items,
        "mailboxes": sorted(accounts),
        "note": None if accounts else "Connect Gmail or Outlook in Apps to see files from your email here.",
    }


async def _owned_attachment(org_id: str, user_id: str, document_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    accounts = await _mailbox_accounts(org_id, user_id)
    rows = await _attachment_rows(org_id, list(accounts), document_id=document_id, limit=1)
    if not rows:
        raise HTTPException(status_code=404, detail="That email attachment was not found.")
    docs = await _documents(org_id, [document_id])
    doc = docs.get(document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="That email attachment was not found.")
    parts = split_attachment_source_id(rows[0]["external_id"])
    return _attachment_item(rows[0], doc, accounts.get(parts["account"], "gmail")), doc


async def email_attachment_detail(org_id: str, user_id: str, document_id: str) -> dict[str, Any]:
    """The text MindLoom read from an attachment, with where it came from."""

    from core.blob_storage import get_blob_storage

    item, doc = await _owned_attachment(org_id, user_id, document_id)
    record: dict[str, Any] = {}
    if doc.get("storage_path"):
        try:
            record = json.loads(await get_blob_storage().get(str(doc["storage_path"])))
        except Exception:  # noqa: BLE001 - metadata still shows without the text
            logger.warning("Could not read attachment record %s", document_id, exc_info=True)
    text = str(record.get("text") or "")
    return {
        **item,
        "text": text[:MAX_TEXT_PREVIEW_CHARS],
        "text_truncated": len(text) > MAX_TEXT_PREVIEW_CHARS,
        "read_with": record.get("read_with"),
        "size_bytes": record.get("size_bytes"),
    }


async def _outlook_attachment_bytes(
    org_id: str, user_id: str, message_id: str, filename: str
) -> bytes:
    from weaver.microsoft_teams import _teams_token

    token, _account = await _teams_token(org_id, user_id)
    headers = {"Authorization": f"Bearer {token}"}
    base = f"https://graph.microsoft.com/v1.0/me/messages/{message_id}/attachments"
    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
        listed = await client.get(base, headers=headers, params={"$select": "id,name"})
        if listed.status_code >= 400:
            raise HTTPException(status_code=502, detail="Outlook did not return this email's attachments.")
        match = next(
            (a for a in listed.json().get("value") or [] if str(a.get("name")) == filename), None
        )
        if match is None:
            raise HTTPException(status_code=404, detail="The attachment is no longer in that email.")
        raw = await client.get(f"{base}/{match['id']}/$value", headers=headers)
    if raw.status_code >= 400:
        raise HTTPException(status_code=502, detail="Could not download the attachment from Outlook.")
    return raw.content


async def email_attachment_original(
    org_id: str, user_id: str, document_id: str
) -> tuple[bytes, dict[str, Any]]:
    """The original attached file, fetched live from the mailbox."""

    from loombot import data_tools
    from weaver.email_content import attachment_kind

    item, _doc = await _owned_attachment(org_id, user_id, document_id)
    if item["provider"] == "outlook":
        data = await _outlook_attachment_bytes(org_id, user_id, item["message_id"], item["filename"])
    else:
        found = await data_tools.email_attachment_bytes(
            org_id, user_id, message_id=item["message_id"], filename=item["filename"]
        )
        if isinstance(found, dict):
            raise HTTPException(status_code=404, detail=str(found.get("error") or "Attachment not found."))
        _meta, _name, data = found
    kind = attachment_kind(item["filename"]) or "other"
    item["download_mime"] = {
        "pdf": "application/pdf",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "csv": "text/csv",
        "text": "text/plain",
        "html": "text/html",
        "image": "image/" + (item["filename"].rsplit(".", 1)[-1].lower().replace("jpg", "jpeg")),
    }.get(kind, "application/octet-stream")
    return data, item


async def import_email_attachment(org_id: str, user_id: str, document_id: str) -> dict[str, Any]:
    """Save an attachment to Files: the original when Files supports it, otherwise its text."""

    from loombot import files as files_service

    item, _doc = await _owned_attachment(org_id, user_id, document_id)
    existing = {f["name"] for f in await files_service.list_files(org_id, user_id)}
    if item["file_kind"]:
        data, _ = await email_attachment_original(org_id, user_id, document_id)
        return await files_service.upload_file(
            org_id, user_id, item["filename"], data,
            files_service.unique_name(existing, item["filename"]),
        )
    detail = await email_attachment_detail(org_id, user_id, document_id)
    if not detail["text"].strip():
        raise HTTPException(status_code=422, detail="This attachment has no readable text to save.")
    name = files_service.unique_name(existing, f"{item['filename'].rsplit('.', 1)[0]}.txt")
    return await files_service.create_file(
        org_id, user_id, name, detail["text"][: files_service.MAX_FILE_BYTES // 2], kind="txt"
    )
