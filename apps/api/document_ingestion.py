"""Shared ingestion path for ordinary company documents."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from file_extract import extract_file_text
from models import (
    Conversation,
    DocumentMetadataInput,
    IncomingMessage,
    JobStatus,
    Participant,
)
from pipeline import DocumentInput, run_ingestion

SUPPORTED_DOCUMENT_EXTENSIONS = {
    ".pdf",
    ".docx",
    ".pptx",
    ".xlsx",
    ".csv",
    ".txt",
    ".md",
    ".log",
    ".jsonl",
}


def document_source(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    return {
        ".pdf": "pdf",
        ".docx": "word",
        ".pptx": "presentation",
        ".xlsx": "spreadsheet",
        ".csv": "csv",
        ".jsonl": "operational_log",
        ".log": "operational_log",
        ".md": "text",
        ".txt": "text",
    }.get(suffix, "document")


def ensure_supported_document(filename: str) -> None:
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_DOCUMENT_EXTENSIONS:
        raise ValueError(
            "Unsupported document type. Use PDF, DOCX, PPTX, XLSX, CSV, "
            "TXT, Markdown, LOG, or JSONL."
        )


async def ingest_document(
    *,
    data: bytes,
    filename: str,
    mime_type: str,
    org_id: str,
    uploaded_by: str,
    metadata: DocumentMetadataInput,
):
    ensure_supported_document(filename)
    text = extract_file_text(filename, data).strip()
    if not text:
        raise ValueError(
            "No readable text was found. Scanned images and media require the later OCR/media pipeline."
        )
    now = metadata.source_updated_at or datetime.now(timezone.utc)
    source = document_source(filename)
    if source in {"csv", "spreadsheet", "operational_log"}:
        lines = [line for line in text.splitlines() if line.strip()]
        messages = [
            IncomingMessage(
                id=f"document:{uuid4()}",
                sender="document",
                timestamp=now + timedelta(microseconds=index),
                text=line,
            )
            for index, line in enumerate(lines)
        ]
    else:
        messages = [
            IncomingMessage(
                id=f"document:{uuid4()}",
                sender="document",
                timestamp=now,
                text=text,
            )
        ]
    conversation = Conversation(
        source=source,
        conversation_id=f"manual:{uuid4()}",
        title=metadata.title or filename,
        participants=[Participant(id="document", name=metadata.author or "Document")],
        messages=messages,
    )
    visible_to = metadata.permissions or [f"org:{org_id}"]
    return await run_ingestion(
        conversation,
        org_id,
        document=DocumentInput(
            data=data,
            source=source,
            source_label=metadata.title or filename,
            original_filename=filename,
            mime_type=mime_type,
            visible_to=visible_to,
            uploaded_by=uploaded_by,
            title=metadata.title or filename,
            author=metadata.author,
            owners=metadata.owners,
            source_created_at=metadata.source_created_at,
            source_updated_at=metadata.source_updated_at,
            source_application=metadata.source_application or "Manual upload",
            source_location=metadata.source_location or "Loom manual uploads",
            department=metadata.department,
            project=metadata.project,
            folder_path=metadata.folder_path,
            version=metadata.version,
            contributors=metadata.contributors,
            permissions=visible_to,
            source_url=metadata.source_url,
        ),
    )


async def run_document_ingestion_background(
    *,
    job_id: str,
    data: bytes,
    filename: str,
    mime_type: str,
    org_id: str,
    uploaded_by: str,
    metadata: DocumentMetadataInput,
    job_store: dict[str, JobStatus],
) -> None:
    job = job_store[job_id]
    job.status = "processing"
    job.progress = "Extracting and indexing document"
    try:
        job.result = await ingest_document(
            data=data,
            filename=filename,
            mime_type=mime_type,
            org_id=org_id,
            uploaded_by=uploaded_by,
            metadata=metadata,
        )
        job.status = "complete"
        job.progress = "Document ingestion complete"
    except Exception as exc:  # noqa: BLE001
        job.status = "failed"
        job.progress = None
        job.error = str(exc)
