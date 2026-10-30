"""Shared ingestion path for ordinary company documents."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from brain.media_text import IMAGE_EXTENSIONS, extract_text_with_ocr, no_text_reason
from core.models import (
    Conversation,
    DocumentMetadataInput,
    IncomingMessage,
    JobStatus,
    Participant,
)
from brain.pipeline import DocumentInput, run_ingestion
from keeper.visibility_acl import permissions_for_upload

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
    *IMAGE_EXTENSIONS,
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
        **{extension: "image" for extension in IMAGE_EXTENSIONS},
    }.get(suffix, "document")


MAX_PIECE_TOKENS = 600


def _table_pieces(table: str, max_tokens: int) -> list[str]:
    """A Markdown table split into row groups, each repeating the header rows."""

    from brain.chunker import _ENCODING

    lines = table.splitlines()
    header, rows = lines[:2], lines[2:]
    pieces: list[str] = []
    current: list[str] = []
    for row in rows:
        candidate = "\n".join(header + current + [row])
        if current and len(_ENCODING.encode(candidate)) > max_tokens:
            pieces.append("\n".join(header + current))
            current = []
        current.append(row)
    if current or not pieces:
        pieces.append("\n".join(header + current))
    return pieces


def document_pieces(text: str, max_tokens: int = MAX_PIECE_TOKENS) -> list[str]:
    """Split a document's text into pieces under ``max_tokens``.

    Paragraphs are kept together; a large Markdown table is split by rows with
    its header repeated, so every piece still says what each column means.
    """

    from brain.chunker import _ENCODING
    from weaver.email_content import split_for_chunks

    blocks: list[str] = []
    for block in text.split("\n\n"):
        lines = block.strip().splitlines()
        is_table = len(lines) >= 3 and lines[0].startswith("|") and lines[1].startswith("|-")
        if is_table and len(_ENCODING.encode(block)) > max_tokens:
            blocks.extend(_table_pieces(block.strip(), max_tokens))
        else:
            blocks.append(block)
    return split_for_chunks("\n\n".join(blocks), max_tokens=max_tokens)


def ensure_supported_document(filename: str) -> None:
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_DOCUMENT_EXTENSIONS:
        raise ValueError(
            "Unsupported document type. Use PDF, DOCX, PPTX, XLSX, CSV, "
            "TXT, Markdown, LOG, JSONL, or a picture (PNG, JPEG, GIF, WebP)."
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
    text, _method = await extract_text_with_ocr(filename, data)
    if not text:
        raise ValueError(no_text_reason(filename, data))
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
        # One message per piece of about 600 tokens, split at paragraphs (a page
        # boundary or a whole table where possible), so chunks stay within the
        # embedding model's limit and a long document becomes many searchable chunks.
        messages = [
            IncomingMessage(
                id=f"document:{uuid4()}",
                sender="document",
                timestamp=now + timedelta(microseconds=index),
                text=piece,
            )
            for index, piece in enumerate(document_pieces(text))
        ]
    conversation = Conversation(
        source=source,
        conversation_id=f"manual:{uuid4()}",
        title=metadata.title or filename,
        participants=[Participant(id="document", name=metadata.author or "Document")],
        messages=messages,
    )
    visible_to = permissions_for_upload(
        org_id=org_id,
        uploaded_by=uploaded_by,
        visibility=metadata.visibility,
        permissions=metadata.permissions,
    )
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
            source_location=metadata.source_location or "MindLoom manual uploads",
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
