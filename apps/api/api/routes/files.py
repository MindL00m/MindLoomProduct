"""LoomBot Files tab: the signed-in user's private files (text, Word, Excel, PDF, PowerPoint)."""

from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile
from pydantic import BaseModel, Field

from keeper.auth import require_user_context
from loombot import files as files_service

from core.rate_limit import actions_limit, outbound_limit, rate_limit

router = APIRouter(prefix="/loombot/files", tags=["loombot-files"])


class CreateFileInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    # CSV text, plain text, JSON, or Markdown (Word, PDF, PowerPoint, Markdown).
    # Excel takes CSV text or Markdown tables (one sheet per table).
    content: str = ""
    kind: Literal["csv", "txt", "md", "json", "docx", "xlsx", "pdf", "pptx"] = "csv"


class TemplateFlagInput(BaseModel):
    is_template: bool


class FillTemplateInput(BaseModel):
    # Values typed by the user; lists of objects fill line-item rows.
    values: dict = Field(default_factory=dict)
    # What LoomBot should fill in, e.g. "Invoice for the Dubai SOP shipment".
    instructions: str | None = Field(default=None, max_length=2000)
    # Files LoomBot reads the values from (a bill of lading, a contract).
    source_file_ids: list[str] = Field(default_factory=list, max_length=8)
    name: str | None = Field(default=None, max_length=200)


class MakeTemplateInput(BaseModel):
    instructions: str | None = Field(default=None, max_length=1000)


class UpdatedVersionInput(BaseModel):
    instructions: str | None = Field(default=None, max_length=1000)


def _download_response(data: bytes, meta: dict, *, inline: bool) -> Response:
    if meta.get("kind") == files_service.OTHER_KIND:
        # Files of any other type are never rendered by the browser on the API's origin.
        meta = {**meta, "mime_type": "application/octet-stream"}
        inline = False
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


@router.get("/templates")
async def list_templates(ctx: tuple[str, str] = Depends(require_user_context)) -> list[dict]:
    """The user's files marked as templates."""

    return [f for f in await files_service.list_files(*ctx) if f.get("is_template")]


class ShipmentDocumentsInput(BaseModel):
    document_types: list[
        Literal["commercial_invoice", "packing_list", "certificate_of_origin", "certificate_of_analysis"]
    ] = Field(min_length=1, max_length=4)
    # The shipment's paperwork: bill of lading, sales contract, lab report, ...
    source_file_ids: list[str] = Field(default_factory=list, max_length=8)
    # The company's own layout per document type (a template in Files), optional.
    template_file_ids: dict[str, str] = Field(default_factory=dict)
    instructions: str | None = Field(default=None, max_length=2000)


@router.post("/shipment-documents", status_code=201, dependencies=[rate_limit(actions_limit, "actions")])
async def shipment_documents(
    body: ShipmentDocumentsInput,
    ctx: tuple[str, str] = Depends(require_user_context),
) -> dict:
    """Make invoices, packing lists and certificates from the shipment's documents."""

    from loombot.shipment_docs import create_shipment_documents

    return await create_shipment_documents(
        *ctx, document_types=list(body.document_types), source_file_ids=body.source_file_ids,
        template_file_ids=body.template_file_ids, instructions=body.instructions,
    )


@router.post("", status_code=201)
async def create_file(
    body: CreateFileInput,
    ctx: tuple[str, str] = Depends(require_user_context),
) -> dict:
    return await files_service.create_document(*ctx, body.name, body.content, kind=body.kind)


@router.post("/upload", status_code=201, dependencies=[rate_limit(actions_limit, "actions")])
async def upload_file(
    file: UploadFile = File(...),
    name: str | None = Form(default=None),
    auto_rename: bool = Form(default=False),
    ctx: tuple[str, str] = Depends(require_user_context),
) -> dict:
    """Add any file from the user's computer (up to 15 MB; text files up to 1 MB).

    ``auto_rename`` gives a taken name a "(2)" suffix instead of failing, for
    multi-file and folder uploads.
    """

    data = await file.read(files_service.MAX_UPLOAD_BYTES + 1)
    return await files_service.upload_file(
        *ctx, file.filename or "upload", data, name or None, auto_rename=auto_rename
    )


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


@router.post("/{file_id}/share", dependencies=[rate_limit(outbound_limit, "outbound")])
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


@router.put("/{file_id}/template")
async def mark_template(
    file_id: str,
    body: TemplateFlagInput,
    ctx: tuple[str, str] = Depends(require_user_context),
) -> dict:
    """Mark a file as a template (or not); returns its {{placeholders}}."""

    from loombot import templates

    meta = await files_service.set_template(*ctx, file_id, body.is_template)
    if not body.is_template:
        return {"file": meta, "fields": [], "lists": {}}
    return await templates.describe_template(*ctx, file_id)


@router.get("/{file_id}/template")
async def template_fields(
    file_id: str,
    ctx: tuple[str, str] = Depends(require_user_context),
) -> dict:
    from loombot import templates

    return await templates.describe_template(*ctx, file_id)


@router.post("/{file_id}/fill", status_code=201, dependencies=[rate_limit(actions_limit, "actions")])
async def fill_template(
    file_id: str,
    body: FillTemplateInput,
    ctx: tuple[str, str] = Depends(require_user_context),
) -> dict:
    """Make a new file from a template; the template itself never changes."""

    from loombot import templates

    return await templates.fill_template_file(
        *ctx, file_id, values=body.values, instructions=body.instructions,
        source_file_ids=body.source_file_ids, name=body.name,
    )


@router.post("/{file_id}/make-template", status_code=201, dependencies=[rate_limit(actions_limit, "actions")])
async def make_template(
    file_id: str,
    body: MakeTemplateInput,
    ctx: tuple[str, str] = Depends(require_user_context),
) -> dict:
    """Copy a sample document as a template, with its deal-specific values as fields."""

    from loombot import templates

    return await templates.make_template_from_sample(*ctx, file_id, instructions=body.instructions)


@router.post("/{file_id}/extract", dependencies=[rate_limit(actions_limit, "actions")])
async def extract_zip(
    file_id: str,
    ctx: tuple[str, str] = Depends(require_user_context),
) -> dict:
    """Unpack a ZIP in Files into separate files; the ZIP itself is kept."""

    return await files_service.extract_zip_to_files(*ctx, file_id)


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


@router.post("/{file_id}/updated-version", status_code=201, dependencies=[rate_limit(actions_limit, "actions")])
async def updated_version(
    file_id: str,
    body: UpdatedVersionInput,
    ctx: tuple[str, str] = Depends(require_user_context),
) -> dict:
    """Create an updated copy of this document from MindLoom's knowledge."""

    from loombot.document_update import create_updated_version

    return await create_updated_version(*ctx, file_id, body.instructions)
