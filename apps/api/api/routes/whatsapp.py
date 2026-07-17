"""WhatsApp export connector routes."""

import base64

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from durable_jobs import enqueue
from integrations import require_admin_context
from models import DocumentMetadataInput
from whatsapp import preview_export

router = APIRouter(prefix="/integrations/whatsapp", tags=["whatsapp"])


@router.post("/preview")
async def preview(
    file: UploadFile = File(...),
    timezone_name: str = Form(default="UTC"),
    _ctx: tuple[str, str] = Depends(require_admin_context),
) -> dict:
    filename = file.filename or "whatsapp.txt"
    if not filename.lower().endswith(".txt"):
        raise HTTPException(status_code=400, detail="WhatsApp exports must be .txt files.")
    data = await file.read()
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
    ctx: tuple[str, str] = Depends(require_admin_context),
) -> dict[str, str]:
    org_id, user_id = ctx
    filename = file.filename or "whatsapp.txt"
    if not filename.lower().endswith(".txt"):
        raise HTTPException(status_code=400, detail="WhatsApp exports must be .txt files.")
    data = await file.read()
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
