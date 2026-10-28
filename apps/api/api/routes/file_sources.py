"""Files tab sources beyond the user's own files: Google Drive and email attachments."""

from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Response

from keeper.auth import require_user_context
from weaver import file_sources

router = APIRouter(prefix="/loombot/sources", tags=["loombot-files"])


_INLINE_SAFE = {"application/pdf", "image/png", "image/jpeg", "image/gif", "image/webp"}


def _file_response(data: bytes, name: str, mime: str, *, inline: bool) -> Response:
    ascii_name = name.encode("ascii", "ignore").decode().replace('"', "") or "file"
    # Only types a browser shows passively may be inline; HTML, SVG and the like are
    # always downloaded so a file from someone's mailbox or Drive can never run as a page.
    if (mime or "").lower() not in _INLINE_SAFE:
        mime, inline = "application/octet-stream", False
    return Response(
        content=data,
        media_type=mime or "application/octet-stream",
        headers={
            "Content-Disposition": (
                f'{"inline" if inline else "attachment"}; filename="{ascii_name}"; '
                f"filename*=UTF-8''{quote(name)}"
            ),
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/drive")
async def drive_files(
    folder_id: str | None = Query(default=None, max_length=200),
    search: str | None = Query(default=None, max_length=200),
    page_token: str | None = Query(default=None, max_length=2000),
    ctx: tuple[str, str] = Depends(require_user_context),
) -> dict:
    """Recent Drive files, one folder's contents ("root" for My Drive), or a name search."""

    return await file_sources.list_drive_files(
        *ctx, folder_id=folder_id, search=search, page_token=page_token
    )


@router.get("/drive/{file_id}/preview")
async def drive_preview(file_id: str, ctx: tuple[str, str] = Depends(require_user_context)) -> dict:
    return await file_sources.drive_preview(*ctx, file_id)


@router.get("/drive/{file_id}/download")
async def drive_download(
    file_id: str, inline: bool = False, ctx: tuple[str, str] = Depends(require_user_context)
) -> Response:
    data, item = await file_sources.drive_file_bytes(*ctx, file_id)
    return _file_response(data, item["download_name"], item["download_mime"], inline=inline)


@router.post("/drive/{file_id}/import", status_code=201)
async def drive_import(file_id: str, ctx: tuple[str, str] = Depends(require_user_context)) -> dict:
    """Save a copy of a Drive file into the user's Files."""

    return await file_sources.import_drive_file(*ctx, file_id)


@router.get("/email-attachments")
async def email_attachments(
    search: str | None = Query(default=None, max_length=200),
    ctx: tuple[str, str] = Depends(require_user_context),
) -> dict:
    """Files MindLoom read from the user's own Gmail and Outlook, newest first."""

    return await file_sources.list_email_attachments(*ctx, search=search)


@router.get("/email-attachments/{document_id}")
async def email_attachment(
    document_id: str, ctx: tuple[str, str] = Depends(require_user_context)
) -> dict:
    return await file_sources.email_attachment_detail(*ctx, document_id)


@router.get("/email-attachments/{document_id}/download")
async def email_attachment_download(
    document_id: str, inline: bool = False, ctx: tuple[str, str] = Depends(require_user_context)
) -> Response:
    """The original attachment, fetched live from the mailbox."""

    data, item = await file_sources.email_attachment_original(*ctx, document_id)
    return _file_response(data, item["filename"], item["download_mime"], inline=inline)


@router.post("/email-attachments/{document_id}/import", status_code=201)
async def email_attachment_import(
    document_id: str, ctx: tuple[str, str] = Depends(require_user_context)
) -> dict:
    return await file_sources.import_email_attachment(*ctx, document_id)
