"""WhatsApp text-export preview and ingestion."""

from __future__ import annotations

from datetime import timezone
from hashlib import sha256
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from models import (
    Conversation,
    DocumentMetadataInput,
    IncomingMessage,
    IngestionResult,
    JobStatus,
    Participant,
)
from parser import parse_whatsapp_export
from pipeline import DocumentInput
from source_registry import ingest_external_source


def decode_export(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("The WhatsApp export could not be decoded.")


def parse_timezone(name: str):
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown timezone: {name}") from exc


def preview_export(data: bytes, timezone_name: str) -> dict:
    messages = parse_whatsapp_export(decode_export(data))
    if not messages:
        raise ValueError(
            "No WhatsApp messages were found. Export the chat without media as a .txt file."
        )
    zone = parse_timezone(timezone_name)
    speakers = sorted({message.speaker for message in messages})
    return {
        "message_count": len(messages),
        "participant_count": len(speakers),
        "participants": speakers,
        "first_message_at": messages[0].timestamp.replace(tzinfo=zone).isoformat(),
        "last_message_at": messages[-1].timestamp.replace(tzinfo=zone).isoformat(),
        "sample_messages": [
            {
                "speaker": message.speaker,
                "timestamp": message.timestamp.replace(tzinfo=zone).isoformat(),
                "text": message.body[:300],
            }
            for message in messages[:5]
        ],
        "ignored_notice": (
            "WhatsApp system notices, deleted-message markers, and media placeholders are excluded."
        ),
    }


async def ingest_whatsapp_export(
    *,
    data: bytes,
    filename: str,
    org_id: str,
    uploaded_by: str,
    timezone_name: str,
    metadata: DocumentMetadataInput,
) -> IngestionResult:
    parsed = parse_whatsapp_export(decode_export(data))
    if not parsed:
        raise ValueError("No WhatsApp messages were found in this export.")
    zone = parse_timezone(timezone_name)
    speakers = list(dict.fromkeys(message.speaker for message in parsed))
    participant_ids = {name: f"whatsapp:{index}" for index, name in enumerate(speakers)}
    messages = [
        IncomingMessage(
            id=f"wa:{index}:{sha256(f'{message.timestamp}:{message.speaker}:{message.body}'.encode()).hexdigest()[:16]}",
            sender=participant_ids[message.speaker],
            timestamp=message.timestamp.replace(tzinfo=zone).astimezone(timezone.utc),
            text=message.body,
        )
        for index, message in enumerate(parsed)
    ]
    title = metadata.title or filename.removesuffix(".txt")
    conversation = Conversation(
        source="whatsapp_export",
        conversation_id=f"whatsapp:{sha256(data).hexdigest()}",
        title=title,
        participants=[
            Participant(id=participant_ids[name], name=name) for name in speakers
        ],
        messages=messages,
    )
    visible_to = metadata.permissions or [f"org:{org_id}"]
    document = DocumentInput(
            data=data, source="whatsapp_export", source_label=title,
            original_filename=filename, mime_type="text/plain",
            visible_to=visible_to, uploaded_by=uploaded_by, title=title,
            author=metadata.author, owners=metadata.owners,
            source_created_at=messages[0].timestamp,
            source_updated_at=messages[-1].timestamp,
            source_application="WhatsApp",
            source_location=metadata.source_location or "WhatsApp chat export",
            department=metadata.department, project=metadata.project,
            folder_path=metadata.folder_path, version=metadata.version or "export",
            contributors=speakers, permissions=visible_to,
            source_url=metadata.source_url,
        )
    result = await ingest_external_source(
        org_id=org_id,
        provider="whatsapp_export",
        # Keeping the same source location or filename makes a later export a
        # replacement version of this chat instead of duplicating old messages.
        external_id=metadata.source_location or filename,
        version=metadata.version or messages[-1].timestamp.isoformat(),
        conversation=conversation,
        document=document,
    )
    return result or IngestionResult(
        total_messages=len(messages),
        total_chunks=0,
        chunks_by_type={},
        failed_chunks=0,
        duration_seconds=0,
    )


async def run_whatsapp_ingestion_background(
    *,
    job_id: str,
    data: bytes,
    filename: str,
    org_id: str,
    uploaded_by: str,
    timezone_name: str,
    metadata: DocumentMetadataInput,
    job_store: dict[str, JobStatus],
) -> None:
    job = job_store[job_id]
    job.status = "processing"
    job.progress = "Parsing and ingesting WhatsApp export"
    try:
        job.result = await ingest_whatsapp_export(
            data=data, filename=filename, org_id=org_id,
            uploaded_by=uploaded_by, timezone_name=timezone_name, metadata=metadata,
        )
        job.status = "complete"
        job.progress = "WhatsApp export ingestion complete"
    except Exception as exc:  # noqa: BLE001
        job.status = "failed"
        job.progress = None
        job.error = str(exc)
