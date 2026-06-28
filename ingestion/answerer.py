"""Answer generation over retrieved context via OpenAI ``gpt-4o-mini``.

Builds a cited context string from the retrieved chunks, asks the model to
answer strictly from that context, and derives a confidence/routing decision
from the response.
"""

from __future__ import annotations

import logging

from openai import AsyncOpenAI

from config import get_settings
from models import QueryResponse, RetrievalResult

logger = logging.getLogger(__name__)

_MODEL = "gpt-4o-mini"

_SYSTEM_PROMPT = """\
You are the Company Brain, an AI that answers questions strictly from 
company knowledge. 

Rules:
- Answer only from the provided context. 
- Never invent facts not present in the context.
- Always cite the chunk_id of every source you use in your answer 
  using the format [SOURCE: chunk_id].
- If the context does not contain enough information to answer 
  confidently, say so explicitly.
- Be concise. One to three sentences unless the question requires more.

Context:
{context_string}"""

# Phrases in the answer that signal the model could not answer confidently.
_LOW_CONFIDENCE_MARKERS = (
    "don't have enough information",
    "cannot answer",
    "not mentioned",
)


def _build_context(retrieval: RetrievalResult) -> str:
    """Render the retrieved chunks into a cited context string."""

    blocks = []
    for chunk in retrieval.chunks:
        speakers = ", ".join(chunk.speakers)
        blocks.append(
            f"[SOURCE: {chunk.chunk_id} | {speakers} | {chunk.start_time}]\n"
            f"{chunk.raw_text}\n"
            "---"
        )
    return "\n".join(blocks)


async def generate_answer(question: str, retrieval: RetrievalResult) -> QueryResponse:
    """Generate an answer for ``question`` from retrieved context.

    Args:
        question: The user's natural-language question.
        retrieval: The vector + graph retrieval result for the question.

    Returns:
        A :class:`QueryResponse` with the answer, sources, confidence, and
        routing metadata.
    """

    if not retrieval.chunks:
        return QueryResponse(
            answer="I don't have enough information to answer this question.",
            sources=[],
            expert=retrieval.experts[0] if retrieval.experts else None,
            confidence="low",
            routed=True,
            routed_reason="No relevant chunks found in the knowledge base.",
        )

    context_string = _build_context(retrieval)

    settings = get_settings()
    client = AsyncOpenAI(
        api_key=settings.openai_api_key,
        timeout=settings.openai_request_timeout_seconds,
    )

    response = await client.chat.completions.create(
        model=_MODEL,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT.format(context_string=context_string)},
            {"role": "user", "content": question},
        ],
        temperature=0,
    )
    answer = (response.choices[0].message.content or "").strip()

    lowered = answer.lower()
    if any(marker in lowered for marker in _LOW_CONFIDENCE_MARKERS):
        confidence = "low"
        routed = True
    elif len(retrieval.chunks) == 1:
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

    return QueryResponse(
        answer=answer,
        sources=retrieval.chunks,
        expert=expert,
        confidence=confidence,
        routed=routed,
        routed_reason=routed_reason,
    )
