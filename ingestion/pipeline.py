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
from dataclasses import dataclass

from chunker import chunk_messages
from embedder import embed_chunk
from extractor import extract_chunk_metadata
from models import Chunk, Conversation, IngestionResult, JobStatus
from normaliser import normalise_speakers
from storage import save_to_neo4j, save_to_postgres
from validator import validate_conversation

logger = logging.getLogger(__name__)


@dataclass
class _ChunkOutcome:
    """Internal per-chunk processing result."""

    knowledge_type: str | None
    failed: bool


async def _process_chunk(
    chunk: Chunk, source: str, source_label: str
) -> _ChunkOutcome:
    """Extract, embed, and persist a single chunk.

    Within a chunk the steps are sequential (embedding depends on the extracted
    summary); concurrency happens across chunks at the call site.
    """

    try:
        metadata = await extract_chunk_metadata(chunk)
        embedding = await embed_chunk(chunk, metadata)
        await save_to_postgres(chunk, metadata, embedding)
        await save_to_neo4j(chunk, metadata, source=source, source_label=source_label)
        logger.info("Processed chunk %s (type=%s)", chunk.chunk_id, metadata.knowledge_type)
        return _ChunkOutcome(knowledge_type=metadata.knowledge_type, failed=False)
    except Exception:  # noqa: BLE001 - we record the failure and continue with others
        logger.exception("Failed to process chunk %s", chunk.chunk_id)
        return _ChunkOutcome(knowledge_type=None, failed=True)


async def run_ingestion(conversation: Conversation) -> IngestionResult:
    """Run the full ingestion pipeline for a canonical conversation.

    Args:
        conversation: A validated-or-validatable canonical conversation.

    Returns:
        An :class:`IngestionResult` summarising the run.

    Raises:
        ValueError: If the conversation fails validation.
    """

    start = time.perf_counter()
    label = conversation.conversation_id
    logger.info("Starting ingestion for conversation '%s' (source=%s)", label, conversation.source)

    validate_conversation(conversation)
    logger.info("[%s] validated %d messages", label, len(conversation.messages))

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

    outcomes: list[_ChunkOutcome] = []
    if chunks:
        outcomes = await asyncio.gather(
            *(_process_chunk(chunk, source, source_label) for chunk in chunks)
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
        result = await run_ingestion(conversation)
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
