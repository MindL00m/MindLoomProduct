"""Answer generation over retrieved context via OpenAI ``gpt-4o-mini``.

Builds a cited context string from the retrieved chunks, asks the model to
answer strictly from that context, and derives a confidence/routing decision
from the response.
"""

from __future__ import annotations

import asyncio
import logging

from openai import AsyncOpenAI

from config import get_settings
from documents import CitationNotFoundError, DocumentRepository, get_citation
from models import ChatMessage, ChunkResult, Citation, EphemeralDocument, QueryResponse, RetrievalResult

logger = logging.getLogger(__name__)

_MODEL = "gpt-4o-mini"

_SYSTEM_PROMPT = """\
You are the Company Brain, an AI that answers questions strictly from 
company knowledge. 

Rules:
- Answer only from the provided context. 
- Never invent facts not present in the context.
- Always cite the chunk_id of every knowledge-graph source you use in your answer 
  using the format [SOURCE: chunk_id].
- For chat-only attached files (marked EPHEMERAL), cite using 
  [EPHEMERAL: document_id].
- If the context does not contain enough information to answer 
  confidently, say so explicitly.
- Be concise. One to three sentences unless the question requires more.
- You may use the earlier conversation turns to understand follow-up
  questions, but every factual claim must still come from the context below.

Context:
{context_string}"""

# Cap how many prior turns are replayed to the model, to bound prompt size.
_MAX_HISTORY_MESSAGES = 12

# Phrases in the answer that signal the model could not answer confidently.
_LOW_CONFIDENCE_MARKERS = (
    "don't have enough information",
    "cannot answer",
    "not mentioned",
)


async def _attach_citations(
    chunks: list[ChunkResult],
    org_id: str,
    repository: DocumentRepository | None = None,
) -> None:
    """Populate each chunk's ``citation`` from its source document, in place.

    Best-effort: a chunk without a linked document (or any lookup error) simply
    keeps ``citation=None`` so answering never fails on missing provenance.
    """

    async def _fetch(chunk: ChunkResult) -> None:
        try:
            chunk.citation = await get_citation(
                chunk.chunk_id, org_id=org_id, repository=repository
            )
        except CitationNotFoundError:
            chunk.citation = None
        except Exception:  # noqa: BLE001 - citations are non-critical metadata
            logger.warning("Citation lookup failed for chunk %s", chunk.chunk_id)
            chunk.citation = None

    if chunks:
        await asyncio.gather(*(_fetch(chunk) for chunk in chunks))


def _build_context(
    retrieval: RetrievalResult, ephemeral: list[EphemeralDocument]
) -> str:
    """Render retrieved chunks and chat-only attachments into a context string."""

    blocks: list[str] = []
    for doc in ephemeral:
        blocks.append(
            f"[EPHEMERAL: {doc.document_id} | {doc.filename}]\n{doc.text}\n---"
        )
    for chunk in retrieval.chunks:
        speakers = ", ".join(chunk.speakers)
        blocks.append(
            f"[SOURCE: {chunk.chunk_id} | {speakers} | {chunk.start_time}]\n"
            f"{chunk.raw_text}\n"
            "---"
        )
    return "\n".join(blocks)


def _ephemeral_sources(docs: list[EphemeralDocument]) -> list[ChunkResult]:
    """Build synthetic source rows for chat-only attachments."""

    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    sources: list[ChunkResult] = []
    for doc in docs:
        excerpt = doc.text if len(doc.text) <= 600 else doc.text[:600] + "…"
        sources.append(
            ChunkResult(
                chunk_id=doc.document_id,
                raw_text=excerpt,
                summary=f"Attached file: {doc.filename}",
                speakers=[],
                start_time=now,
                end_time=now,
                knowledge_type="attached",
                confidence="high",
                similarity_score=1.0,
                citation=Citation(
                    chunk_id=doc.document_id,
                    document_id=doc.document_id,
                    source="chat_attachment",
                    source_label=f"{doc.filename} (this chat only)",
                    original_filename=doc.filename,
                ),
            )
        )
    return sources


def _history_messages(history: list[ChatMessage]) -> list[dict[str, str]]:
    """Render prior conversation turns as OpenAI chat messages (most recent)."""

    recent = history[-_MAX_HISTORY_MESSAGES:]
    return [{"role": turn.role, "content": turn.content} for turn in recent]


async def generate_answer(
    question: str,
    retrieval: RetrievalResult,
    history: list[ChatMessage] | None = None,
    org_id: str = "",
    ephemeral_documents: list[EphemeralDocument] | None = None,
) -> QueryResponse:
    """Generate an answer for ``question`` from retrieved context.

    Args:
        question: The user's natural-language question.
        retrieval: The vector + graph retrieval result for the question.
        history: Prior conversation turns (oldest first) for follow-up memory.

    Returns:
        A :class:`QueryResponse` with the answer, sources, confidence, and
        routing metadata.
    """

    ephemeral = ephemeral_documents or []

    if not retrieval.chunks and not ephemeral:
        return QueryResponse(
            answer="I don't have enough information to answer this question.",
            sources=[],
            expert=retrieval.experts[0] if retrieval.experts else None,
            confidence="low",
            routed=True,
            routed_reason="No relevant chunks found in the knowledge base.",
        )

    context_string = _build_context(retrieval, ephemeral)

    settings = get_settings()
    client = AsyncOpenAI(
        api_key=settings.openai_api_key,
        timeout=settings.openai_request_timeout_seconds,
    )

    messages: list[dict[str, str]] = [
        {"role": "system", "content": _SYSTEM_PROMPT.format(context_string=context_string)},
        *_history_messages(history or []),
        {"role": "user", "content": question},
    ]
    response = await client.chat.completions.create(
        model=_MODEL,
        messages=messages,  # type: ignore[arg-type]
        temperature=0,
    )
    answer = (response.choices[0].message.content or "").strip()

    lowered = answer.lower()
    if any(marker in lowered for marker in _LOW_CONFIDENCE_MARKERS):
        confidence = "low"
        routed = True
    elif len(retrieval.chunks) + len(ephemeral) == 1:
        confidence = "medium"
        routed = False
    else:
        confidence = "high"
        routed = False

    expert = retrieval.experts[0] if (routed and retrieval.experts) else None
    routed_reason = (
        "Answer confidence was low; routing to the most relevant expert."
        if routed
        else None
    )

    await _attach_citations(retrieval.chunks, org_id)

    graph_sources = list(retrieval.chunks)
    chat_sources = _ephemeral_sources(ephemeral)
    all_sources = graph_sources + chat_sources

    return QueryResponse(
        answer=answer,
        sources=all_sources,
        expert=expert,
        confidence=confidence,
        routed=routed,
        routed_reason=routed_reason,
    )
