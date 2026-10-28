"""LoomBot Files tab: the signed-in user's private files (text, Word, Excel, PDF, PowerPoint)."""

from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile
from pydantic import BaseModel, Field

from keeper.auth import require_user_context
from loombot import files as files_service

router = APIRouter(prefix="/loombot/files", tags=["loombot-files"])


class CreateFileInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    # CSV text, plain text, JSON, or Markdown (Word, PDF, PowerPoint, Markdown).
    # Excel takes CSV text or Markdown tables (one sheet per table).
    content: str = ""
    kind: Literal["csv", "txt", "md", "json", "docx", "xlsx", "pdf", "pptx"] = "csv"


class UpdatedVersionInput(BaseModel):
    instructions: str | None = Field(default=None, max_length=1000)


def _download_response(data: bytes, meta: dict, *, inline: bool) -> Response:
    name = meta["name"]
    ascii_name = name.encode("ascii", "ignore").decode() or f"file.{meta['kind']}"
    disposition = "inline" if inline else "attachment"
    return Response(
        content=data,
        media_type=meta["mime_type"],
        headers={
            "Content-Disposition": (
                f'{disposition}; filename="{ascii_name.replace(chr(34), "")}"; '
                f"filename*=UTF-8''{quote(name)}"
            ),
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


class UpdateFileInput(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    # Full new text: CSV, plain text, or Markdown for a Word document (undoable).
    content: str | None = None


@router.get("")
async def list_files(ctx: tuple[str, str] = Depends(require_user_context)) -> list[dict]:
    return await files_service.list_files(*ctx)


@router.post("", status_code=201)
async def create_file(
    body: CreateFileInput,
    ctx: tuple[str, str] = Depends(require_user_context),
) -> dict:
    return await files_service.create_document(*ctx, body.name, body.content, kind=body.kind)


@router.post("/upload", status_code=201)
async def upload_file(
    file: UploadFile = File(...),
    name: str | None = Form(default=None),
    ctx: tuple[str, str] = Depends(require_user_context),
) -> dict:
    """Add a CSV, TXT, DOCX, XLSX, or PDF file from the user's computer."""

    data = await file.read(files_service.MAX_UPLOAD_BYTES + 1)
    return await files_service.upload_file(*ctx, file.filename or "upload", data, name or None)


@router.get("/public/{file_id}/{filename}")
async def public_file(
    file_id: str,
    filename: str,
    token: str = Query(min_length=10),
) -> Response:
    """Serve one file to a desktop app (e.g. Excel) via a short-lived signed link."""

    org_id, user_id = files_service.verify_file_link(token, file_id)
    data, meta = await files_service.get_file_bytes(org_id, user_id, file_id)
    return _download_response(data, meta, inline=False)


class ShareInput(BaseModel):
    # Email addresses; colleagues who use MindLoom are matched by their address.
    recipients: list[str] = Field(min_length=1, max_length=25)
    send_email: bool = True
    share_in_mindloom: bool = False
    message: str | None = Field(default=None, max_length=2000)


class SharesSeenInput(BaseModel):
    file_ids: list[str] | None = None


@router.get("/shares/unseen")
async def unseen_shares(ctx: tuple[str, str] = Depends(require_user_context)) -> list[dict]:
    """Files colleagues shared in MindLoom that this user has not seen yet."""

    return await files_service.unseen_shares(*ctx)


@router.post("/shares/seen")
async def shares_seen(
    body: SharesSeenInput, ctx: tuple[str, str] = Depends(require_user_context)
) -> dict:
    return {"cleared": await files_service.mark_shares_seen(*ctx, body.file_ids)}


@router.post("/{file_id}/share")
async def share_file(
    file_id: str,
    body: ShareInput,
    ctx: tuple[str, str] = Depends(require_user_context),
) -> dict:
    """Email the file as an attachment and/or copy it into colleagues' Files."""

    return await files_service.share_file(
        *ctx, file_id, body.recipients,
        send_email=body.send_email, in_mindloom=body.share_in_mindloom, message=body.message,
    )


@router.get("/{file_id}")
async def get_file(
    file_id: str,
    ctx: tuple[str, str] = Depends(require_user_context),
) -> dict:
    return await files_service.get_file(*ctx, file_id)


@router.put("/{file_id}")
async def update_file(
    file_id: str,
    body: UpdateFileInput,
    ctx: tuple[str, str] = Depends(require_user_context),
) -> dict:
    return await files_service.update_file(
        *ctx, file_id, content=body.content, name=body.name
    )


@router.post("/{file_id}/undo")
async def undo_file(
    file_id: str,
    ctx: tuple[str, str] = Depends(require_user_context),
) -> dict:
    return await files_service.undo_file(*ctx, file_id)


@router.delete("/{file_id}", status_code=204)
async def delete_file(
    file_id: str,
    ctx: tuple[str, str] = Depends(require_user_context),
) -> Response:
    await files_service.delete_file(*ctx, file_id)
    return Response(status_code=204)


class MonitoringInput(BaseModel):
    enabled: bool
    instructions: str | None = Field(default=None, max_length=1000)


@router.put("/{file_id}/monitoring")
async def set_monitoring(
    file_id: str,
    body: MonitoringInput,
    ctx: tuple[str, str] = Depends(require_user_context),
) -> dict:
    """Turn automatic updates from new knowledge on or off for a file."""

    return await files_service.set_monitoring(
        *ctx, file_id, enabled=body.enabled, instructions=body.instructions
    )


@router.get("/{file_id}/events")
async def file_events(
    file_id: str,
    ctx: tuple[str, str] = Depends(require_user_context),
) -> list[dict]:
    """Automatic changes monitoring made to this file, newest first."""

    return await files_service.list_events(*ctx, file_id)


@router.get("/{file_id}/download")
async def download_file(
    file_id: str,
    inline: bool = False,
    ctx: tuple[str, str] = Depends(require_user_context),
) -> Response:
    data, meta = await files_service.get_file_bytes(*ctx, file_id)
    return _download_response(data, meta, inline=inline)


@router.get("/{file_id}/preview")
async def preview_file(
    file_id: str,
    ctx: tuple[str, str] = Depends(require_user_context),
) -> dict:
    """Structured preview: Word blocks or Excel sheets."""

    return await files_service.preview_file(*ctx, file_id)


@router.put("/{file_id}/upload")
async def replace_file(
    file_id: str,
    file: UploadFile = File(...),
    ctx: tuple[str, str] = Depends(require_user_context),
) -> dict:
    """Replace a file with a new version of the same type (undoable)."""

    data = await file.read(files_service.MAX_UPLOAD_BYTES + 1)
    return await files_service.replace_upload(*ctx, file_id, file.filename or "", data)


@router.post("/{file_id}/open-link")
async def open_link(
    file_id: str,
    request: Request,
    ctx: tuple[str, str] = Depends(require_user_context),
) -> dict:
    """A 10-minute link a desktop app can fetch, used by "Open in Excel"."""

    from core.config import get_settings

    meta = await files_service.get_file(*ctx, file_id)
    token, expires = files_service.issue_file_link(*ctx, file_id)
    base = (get_settings().public_api_url or "").strip().rstrip("/") or str(request.base_url).rstrip("/")
    url = f"{base}/loombot/files/public/{quote(file_id)}/{quote(meta['name'])}?token={quote(token)}"
    return {"url": url, "expires_at": expires, "kind": meta["kind"]}


@router.post("/{file_id}/updated-version", status_code=201)
async def updated_version(
    file_id: str,
    body: UpdatedVersionInput,
    ctx: tuple[str, str] = Depends(require_user_context),
) -> dict:
    """Create an updated copy of this document from MindLoom's knowledge."""

    from loombot.document_update import create_updated_version

    return await create_updated_version(*ctx, file_id, body.instructions)
