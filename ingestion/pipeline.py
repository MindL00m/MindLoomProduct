"""Ingestion pipeline orchestrator.

Operates on the connector-agnostic :class:`Conversation` format: validate ->
normalise -> chunk -> (extract + embed) -> store. Per-chunk extraction,
embedding, and persistence run concurrently across chunks via
:func:`asyncio.gather`. WhatsApp-specific parsing lives outside this module.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

from blob_storage import BlobStorage, get_blob_storage
from chunker import chunk_messages
from documents import (
    DocumentRepository,
    Neo4jDocumentRepository,
    compute_chunk_locators,
    link_chunk_to_document,
    store_document,
)
from embedder import embed_chunk
from extractor import extract_chunk_metadata
from models import Chunk, Conversation, DerivedFrom, IngestionResult, JobStatus
from normaliser import normalise_speakers
from pdf_chunker import chunk_pdf
from storage import save_to_neo4j, save_to_postgres
from validator import validate_conversation

logger = logging.getLogger(__name__)


@dataclass
class DocumentInput:
    """The raw source document an ingestion run derives its chunks from.

    Supplied by the caller for real file uploads; for conversation-only ingests
    the pipeline synthesises one from the canonical conversation (see
    :func:`_document_from_conversation`).
    """

    data: bytes
    source: str
    source_label: str
    original_filename: str | None = None
    mime_type: str = "text/plain"
    visible_to: list[str] = field(default_factory=list)
    uploaded_by: str | None = None


@dataclass
class _ChunkOutcome:
    """Internal per-chunk processing result."""

    knowledge_type: str | None
    failed: bool


def _document_from_conversation(conversation: Conversation) -> DocumentInput:
    """Synthesise a :class:`DocumentInput` from a conversation payload.

    The blob is the canonical JSON of the conversation, so re-posting the exact
    same conversation hashes identically and de-duplicates.
    """

    return DocumentInput(
        data=conversation.model_dump_json().encode("utf-8"),
        source=conversation.source,
        source_label=conversation.title or conversation.source,
        original_filename=None,
        mime_type="application/json",
        visible_to=[],
    )


async def _process_chunk(
    chunk: Chunk,
    source: str,
    source_label: str,
    document_id: str,
    locator: DerivedFrom,
    visible_to: list[str],
    org_id: str,
    repository: DocumentRepository,
) -> _ChunkOutcome:
    """Extract, embed, and persist a single chunk, then link it to its document.

    Within a chunk the steps are sequential (embedding depends on the extracted
    summary, and the DERIVED_FROM edge must be created after the Chunk node
    exists); concurrency happens across chunks at the call site. The chunk
    inherits the document's ``visible_to`` visibility.
    """

    try:
        metadata = await extract_chunk_metadata(chunk)
        embedding = await embed_chunk(chunk, metadata)
        await save_to_postgres(chunk, metadata, embedding, org_id)
        await save_to_neo4j(
            chunk,
            metadata,
            org_id,
            source=source,
            source_label=source_label,
            visible_to=visible_to,
        )
        await link_chunk_to_document(
            chunk.chunk_id,
            document_id,
            locator,
            org_id=org_id,
            repository=repository,
        )
        logger.info("Processed chunk %s (type=%s)", chunk.chunk_id, metadata.knowledge_type)
        return _ChunkOutcome(knowledge_type=metadata.knowledge_type, failed=False)
    except Exception:  # noqa: BLE001 - we record the failure and continue with others
        logger.exception("Failed to process chunk %s", chunk.chunk_id)
        return _ChunkOutcome(knowledge_type=None, failed=True)


async def run_ingestion(
    conversation: Conversation,
    org_id: str,
    *,
    document: DocumentInput | None = None,
    repository: DocumentRepository | None = None,
    storage: BlobStorage | None = None,
) -> IngestionResult:
    """Run the full ingestion pipeline for a canonical conversation.

    Intake first writes the source document to blob storage and creates (or
    reuses, via content-hash de-dup) its ``Document`` node. Each chunk is then
    linked to that document with a source-appropriate locator as it is created.

    Re-ingesting the exact same document is a storage no-op (de-dup), and is
    additionally skipped entirely if the document already has chunks — so an
    accidental re-run never produces duplicate chunks.

    Args:
        conversation: A validated-or-validatable canonical conversation.
        document: The raw source document; synthesised from the conversation
            when omitted.
        repository: Document graph repository (defaults to Neo4j).
        storage: Blob storage backend (defaults to the configured one).

    Returns:
        An :class:`IngestionResult` summarising the run.

    Raises:
        ValueError: If the conversation fails validation.
    """

    start = time.perf_counter()
    label = conversation.conversation_id
    repository = repository or Neo4jDocumentRepository()
    storage = storage or get_blob_storage()
    logger.info("Starting ingestion for conversation '%s' (source=%s)", label, conversation.source)

    validate_conversation(conversation)
    logger.info("[%s] validated %d messages", label, len(conversation.messages))

    # --- Intake: persist the raw document + Document node before chunking. ---
    document = document or _document_from_conversation(conversation)
    store_result = await store_document(
        org_id=org_id,
        data=document.data,
        source=document.source,
        source_label=document.source_label,
        mime_type=document.mime_type,
        original_filename=document.original_filename,
        uploaded_by=document.uploaded_by,
        visible_to=document.visible_to,
        repository=repository,
        storage=storage,
    )
    doc = store_result.document
    logger.info(
        "[%s] document %s (%s, deduped=%s)",
        label,
        doc.document_id,
        doc.source,
        store_result.deduped,
    )

    # Idempotent re-run guard: if this document already has chunks, don't chunk
    # again (avoids duplicates on accidental re-uploads). Storage was already a
    # no-op via de-dup above.
    existing_chunks = await repository.count_chunks_for_document(doc.document_id)
    if existing_chunks > 0:
        logger.info(
            "[%s] document %s already has %d chunk(s); skipping chunking",
            label,
            doc.document_id,
            existing_chunks,
        )
        return IngestionResult(
            total_messages=len(conversation.messages),
            total_chunks=existing_chunks,
            chunks_by_type={},
            failed_chunks=0,
            duration_seconds=round(time.perf_counter() - start, 3),
        )

    messages, participants, name_mapping = normalise_speakers(
        conversation.messages, conversation.participants
    )
    logger.info(
        "[%s] normalised speakers: %d participants, %d name merges",
        label,
        len(participants),
        sum(1 for original, canonical in name_mapping.items() if original != canonical),
    )

    # Resolve sender ids to display names so chunks (and thus the knowledge graph
    # Person nodes) are keyed on human-readable names rather than opaque ids.
    id_to_name = {participant.id: participant.name for participant in participants}
    display_messages = [
        message.model_copy(update={"sender": id_to_name.get(message.sender, message.sender)})
        for message in messages
    ]

    chunks = chunk_messages(display_messages)
    logger.info("[%s] produced %d chunks", label, len(chunks))

    source = conversation.source
    source_label = conversation.title or conversation.source

    # Per-chunk source locators (char offsets / page / row range), positionally
    # aligned with chunks. Computed from the document source type.
    locators = compute_chunk_locators(chunks, doc.source)

    outcomes: list[_ChunkOutcome] = []
    if chunks:
        outcomes = await asyncio.gather(
            *(
                _process_chunk(
                    chunk,
                    source,
                    source_label,
                    doc.document_id,
                    locator,
                    doc.visible_to,
                    org_id,
                    repository,
                )
                for chunk, locator in zip(chunks, locators)
            )
        )

    chunks_by_type: Counter[str] = Counter(
        outcome.knowledge_type
        for outcome in outcomes
        if outcome.knowledge_type is not None
    )
    failed_chunks = sum(1 for outcome in outcomes if outcome.failed)

    duration = time.perf_counter() - start
    result = IngestionResult(
        total_messages=len(messages),
        total_chunks=len(chunks),
        chunks_by_type=dict(chunks_by_type),
        failed_chunks=failed_chunks,
        duration_seconds=round(duration, 3),
    )
    logger.info(
        "[%s] ingestion complete: %d messages, %d chunks, %d failed in %.2fs",
        label,
        result.total_messages,
        result.total_chunks,
        result.failed_chunks,
        result.duration_seconds,
    )
    return result


async def run_ingestion_background(
    job_id: str,
    conversation: Conversation,
    org_id: str,
    job_store: dict[str, JobStatus],
) -> None:
    """Run :func:`run_ingestion` as a background job, updating ``job_store``.

    Updates the job's status as it progresses and records the result on success
    or the error message on failure. Never raises; failures are captured into
    the job record.

    Args:
        job_id: Identifier of the job entry to update.
        conversation: The conversation to ingest.
        job_store: In-memory mapping of job_id -> :class:`JobStatus` (v1 has no
            durable job store).
    """

    job = job_store.get(job_id)
    if job is None:
        job = JobStatus(
            job_id=job_id,
            status="processing",
            conversation_id=conversation.conversation_id,
        )
        job_store[job_id] = job

    job.status = "processing"
    job.progress = "Running ingestion pipeline"
    logger.info("Job %s: processing conversation '%s'", job_id, conversation.conversation_id)

    try:
        result = await run_ingestion(conversation, org_id)
        job.status = "complete"
        job.progress = "Ingestion complete"
        job.result = result
        job.error = None
        logger.info("Job %s: complete", job_id)
    except Exception as exc:  # noqa: BLE001 - record failure into job state
        job.status = "failed"
        job.progress = None
        job.error = str(exc)
        logger.exception("Job %s: failed", job_id)


async def run_pdf_ingestion(
    data: bytes,
    org_id: str,
    *,
    source_label: str,
    original_filename: str | None = None,
    visible_to: list[str] | None = None,
    uploaded_by: str | None = None,
    repository: DocumentRepository | None = None,
    storage: BlobStorage | None = None,
) -> IngestionResult:
    """Ingest a PDF: store the file, structure-chunk it, classify, and link.

    Mirrors :func:`run_ingestion` but sources chunks from :func:`chunk_pdf`
    (heading/paragraph aware) instead of a conversation. Each chunk records a
    page + character span via ``DERIVED_FROM`` and inherits the document's
    visibility. Re-uploading the same PDF de-dups storage and, if the document
    already has chunks, skips re-chunking.

    Args:
        data: Raw PDF bytes.
        source_label: Human-readable label for citations (e.g. the filename).
        original_filename: Original upload filename, if any.
        visible_to: Group names permitted to see the document and its chunks.
        uploaded_by: person_id of the uploader, if known.
        repository: Document graph repository (defaults to Neo4j).
        storage: Blob storage backend (defaults to the configured one).

    Returns:
        An :class:`IngestionResult` summarising the run (``total_messages`` is 0
        for PDFs).
    """

    start = time.perf_counter()
    repository = repository or Neo4jDocumentRepository()
    storage = storage or get_blob_storage()
    logger.info("Starting PDF ingestion for '%s'", source_label)

    store_result = await store_document(
        org_id=org_id,
        data=data,
        source="pdf",
        source_label=source_label,
        mime_type="application/pdf",
        original_filename=original_filename,
        uploaded_by=uploaded_by,
        visible_to=visible_to or [],
        repository=repository,
        storage=storage,
    )
    doc = store_result.document
    logger.info("PDF document %s (deduped=%s)", doc.document_id, store_result.deduped)

    existing_chunks = await repository.count_chunks_for_document(doc.document_id)
    if existing_chunks > 0:
        logger.info(
            "PDF document %s already has %d chunk(s); skipping chunking",
            doc.document_id,
            existing_chunks,
        )
        return IngestionResult(
            total_messages=0,
            total_chunks=existing_chunks,
            chunks_by_type={},
            failed_chunks=0,
            duration_seconds=round(time.perf_counter() - start, 3),
        )

    pdf_chunks = chunk_pdf(data)
    logger.info("PDF %s produced %d chunks", doc.document_id, len(pdf_chunks))

    now = datetime.now(timezone.utc)
    chunks: list[Chunk] = []
    locators: list[DerivedFrom] = []
    for pdf_chunk in pdf_chunks:
        chunks.append(
            Chunk(
                chunk_id=str(uuid4()),
                messages=[],
                speakers=[],
                start_time=now,
                end_time=now,
                raw_text=pdf_chunk.text,
            )
        )
        locators.append(
            DerivedFrom(
                char_start=pdf_chunk.char_start,
                char_end=pdf_chunk.char_end,
                page_start=pdf_chunk.page_start,
                page_end=pdf_chunk.page_end,
            )
        )

    outcomes: list[_ChunkOutcome] = []
    if chunks:
        outcomes = await asyncio.gather(
            *(
                _process_chunk(
                    chunk,
                    "pdf",
                    source_label,
                    doc.document_id,
                    locator,
                    doc.visible_to,
                    org_id,
                    repository,
                )
                for chunk, locator in zip(chunks, locators)
            )
        )

    chunks_by_type: Counter[str] = Counter(
        outcome.knowledge_type
        for outcome in outcomes
        if outcome.knowledge_type is not None
    )
    failed_chunks = sum(1 for outcome in outcomes if outcome.failed)

    result = IngestionResult(
        total_messages=0,
        total_chunks=len(chunks),
        chunks_by_type=dict(chunks_by_type),
        failed_chunks=failed_chunks,
        duration_seconds=round(time.perf_counter() - start, 3),
    )
    logger.info(
        "PDF ingestion complete: %d chunks, %d failed in %.2fs",
        result.total_chunks,
        result.failed_chunks,
        result.duration_seconds,
    )
    return result


async def run_pdf_ingestion_background(
    job_id: str,
    data: bytes,
    filename: str,
    org_id: str,
    visible_to: list[str],
    job_store: dict[str, JobStatus],
) -> None:
    """Run :func:`run_pdf_ingestion` as a background job, updating ``job_store``."""

    job = job_store.get(job_id)
    if job is None:
        job = JobStatus(job_id=job_id, status="processing", conversation_id=filename)
        job_store[job_id] = job

    job.status = "processing"
    job.progress = "Chunking and classifying PDF"
    logger.info("Job %s: processing PDF '%s'", job_id, filename)

    try:
        result = await run_pdf_ingestion(
            data,
            org_id,
            source_label=filename or "PDF upload",
            original_filename=filename,
            visible_to=visible_to,
        )
        job.status = "complete"
        job.progress = "Ingestion complete"
        job.result = result
        job.error = None
        logger.info("Job %s: complete", job_id)
    except Exception as exc:  # noqa: BLE001 - record failure into job state
        job.status = "failed"
        job.progress = None
        job.error = str(exc)
        logger.exception("Job %s: failed", job_id)
