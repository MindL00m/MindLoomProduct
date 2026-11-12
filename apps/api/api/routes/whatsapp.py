"""WhatsApp connector routes: chat exports, and live chats via OpenClaw."""

import base64

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from core.durable_jobs import enqueue
from weaver.integrations import require_user_context
from core.models import DocumentMetadataInput
from weaver.whatsapp import preview_export
from weaver.whatsapp_live import (
    LiveMessagesInput,
    create_link,
    link_status,
    remove_link,
    require_link,
    store_messages,
)

from core.uploads import MAX_CHAT_EXPORT_BYTES, read_upload

router = APIRouter(prefix="/integrations/whatsapp", tags=["whatsapp"])


@router.post("/preview")
async def preview(
    file: UploadFile = File(...),
    timezone_name: str = Form(default="UTC"),
    _ctx: tuple[str, str] = Depends(require_user_context),
) -> dict:
    filename = file.filename or "whatsapp.txt"
    if not filename.lower().endswith(".txt"):
        raise HTTPException(status_code=400, detail="WhatsApp exports must be .txt files.")
    data = await read_upload(file, MAX_CHAT_EXPORT_BYTES)
    if not data:
        raise HTTPException(status_code=400, detail="WhatsApp export is empty.")
    try:
        return preview_export(data, timezone_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/import")
async def import_export(
    file: UploadFile = File(...),
    timezone_name: str = Form(default="UTC"),
    metadata_json: str = Form(default="{}"),
    ctx: tuple[str, str] = Depends(require_user_context),
) -> dict[str, str]:
    org_id, user_id = ctx
    filename = file.filename or "whatsapp.txt"
    if not filename.lower().endswith(".txt"):
        raise HTTPException(status_code=400, detail="WhatsApp exports must be .txt files.")
    data = await read_upload(file, MAX_CHAT_EXPORT_BYTES)
    if not data:
        raise HTTPException(status_code=400, detail="WhatsApp export is empty.")
    try:
        metadata = DocumentMetadataInput.model_validate_json(metadata_json)
        preview_export(data, timezone_name)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    job_id = await enqueue(
        "whatsapp_export", org_id=org_id, conversation_id=filename,
        payload={
            "filename": filename, "data": base64.b64encode(data).decode(),
            "uploaded_by": user_id, "timezone_name": timezone_name,
            "metadata": metadata.model_dump(mode="json"),
        },
    )
    return {"job_id": job_id, "status": "queued"}


# --- Live chats, forwarded by the user's OpenClaw gateway ---------------------------


@router.get("/live")
async def live_status(ctx: tuple[str, str] = Depends(require_user_context)) -> dict:
    return await link_status(*ctx)


@router.post("/live/link")
async def live_link(ctx: tuple[str, str] = Depends(require_user_context)) -> dict:
    """Create or replace the link token for the MindLoom OpenClaw plugin (shown once)."""

    return {"token": await create_link(*ctx), **await link_status(*ctx)}


@router.delete("/live")
async def live_unlink(ctx: tuple[str, str] = Depends(require_user_context)) -> dict:
    await remove_link(*ctx)
    return await link_status(*ctx)


@router.post("/live/messages")
async def live_messages(
    request: LiveMessagesInput,
    ctx: tuple[str, str] = Depends(require_link),
) -> dict[str, int]:
    """Called by the OpenClaw plugin with the link token, not by a signed-in user."""

    return await store_messages(ctx[0], ctx[1], request.messages)
