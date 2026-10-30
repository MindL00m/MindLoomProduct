"""Reading uploads with a size cap, so one request cannot exhaust memory."""

from __future__ import annotations

from fastapi import HTTPException, UploadFile

MB = 1024 * 1024
MAX_DOCUMENT_BYTES = 50 * MB      # /ingest/document, /ingest/pdf (and ZIPs of documents)
MAX_EXTRACT_BYTES = 25 * MB       # chat-only files
MAX_CHAT_EXPORT_BYTES = 20 * MB   # WhatsApp exports
MAX_MEDIA_BYTES = 25 * MB         # expert answer audio, video and pictures


async def read_upload(file: UploadFile, limit: int) -> bytes:
    """The upload's bytes, or 413 when it is larger than ``limit``."""

    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(
            status_code=413, detail=f"Files are limited to {limit // MB} MB here."
        )
    return data
