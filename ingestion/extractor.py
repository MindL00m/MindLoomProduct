"""LLM-based metadata extraction for chunks.

Makes a single ``gpt-4o-mini`` call per chunk that returns strict JSON matching
:class:`ChunkMetadata`. On a parse failure it retries once with the validation
error fed back into the prompt; a second failure downgrades the chunk to
``knowledge_type="noise"``.
"""

from __future__ import annotations

import logging

from openai import AsyncOpenAI
from pydantic import ValidationError

from config import get_settings
from models import Chunk, ChunkMetadata

logger = logging.getLogger(__name__)

_MODEL = "gpt-4o-mini"

_SCHEMA_INSTRUCTIONS = """\
You are an information-extraction engine for an organisational knowledge graph.
Given a chunk of WhatsApp conversation, return ONLY a single valid JSON object
(no preamble, no markdown, no code fences) with exactly these keys:

{
  "entities": [string],            // people, projects, systems, tools, dates mentioned
  "knowledge_type": "decision" | "question_answer" | "problem_report" | "status_update" | "noise",
  "ownership": [                    // one entry per person/topic relationship you can infer
    {
      "person": string,
      "topic": string,
      "signal_type": "asked" | "answered" | "owns" | "mentioned"
    }
  ],
  "confidence": "high" | "medium" | "low",
  "confidence_reason": string,     // short justification for the confidence value
  "summary": string                // exactly one sentence describing the chunk
}

Rules:
- Output must be parseable JSON and nothing else.
- Use the exact literal values listed for knowledge_type, signal_type, and confidence.
- If the chunk is small talk or has no useful knowledge, use knowledge_type "noise".
- "ownership" may be an empty list if no relationships are inferable.
"""


def _build_user_prompt(chunk: Chunk) -> str:
    """Render the user-facing prompt body for a chunk."""

    return (
        f"Conversation chunk (participants: {', '.join(chunk.speakers)}):\n"
        f"-----\n{chunk.raw_text}\n-----\n"
        "Extract the metadata JSON now."
    )


def _build_messages(chunk: Chunk, prior_error: str | None) -> list[dict[str, str]]:
    """Assemble the chat messages, optionally including a previous parse error."""

    user_content = _build_user_prompt(chunk)
    if prior_error is not None:
        user_content += (
            "\n\nYour previous response could not be parsed. "
            f"Fix it and return ONLY corrected JSON. Validation error:\n{prior_error}"
        )
    return [
        {"role": "system", "content": _SCHEMA_INSTRUCTIONS},
        {"role": "user", "content": user_content},
    ]


def _noise_fallback(reason: str) -> ChunkMetadata:
    """Build a safe 'noise' metadata object used when extraction fails."""

    return ChunkMetadata(
        entities=[],
        knowledge_type="noise",
        ownership=[],
        confidence="low",
        confidence_reason=reason,
        summary="Metadata extraction failed; chunk marked as noise.",
    )


async def _call_model(client: AsyncOpenAI, chunk: Chunk, prior_error: str | None) -> str:
    """Make one chat-completion call and return the raw response content."""

    response = await client.chat.completions.create(
        model=_MODEL,
        messages=_build_messages(chunk, prior_error),  # type: ignore[arg-type]
        temperature=0,
        response_format={"type": "json_object"},
    )
    return response.choices[0].message.content or ""


async def extract_chunk_metadata(chunk: Chunk) -> ChunkMetadata:
    """Extract structured metadata for a single chunk via one (or one retry) LLM call.

    Args:
        chunk: The chunk to analyse.

    Returns:
        A validated :class:`ChunkMetadata`. Falls back to a ``noise`` record if
        the model cannot produce valid JSON after one retry.
    """

    settings = get_settings()
    client = AsyncOpenAI(
        api_key=settings.openai_api_key,
        timeout=settings.openai_request_timeout_seconds,
    )

    prior_error: str | None = None
    for attempt in range(2):
        try:
            raw = await _call_model(client, chunk, prior_error)
            return ChunkMetadata.model_validate_json(raw)
        except ValidationError as exc:
            prior_error = str(exc)
            logger.warning(
                "Chunk %s metadata validation failed on attempt %d: %s",
                chunk.chunk_id,
                attempt + 1,
                exc,
            )
        except Exception as exc:  # noqa: BLE001 - network/JSON/SDK errors all retry once
            prior_error = str(exc)
            logger.warning(
                "Chunk %s extraction call failed on attempt %d: %s",
                chunk.chunk_id,
                attempt + 1,
                exc,
            )

    logger.error(
        "Chunk %s extraction failed after retry; marking as noise. Last error: %s",
        chunk.chunk_id,
        prior_error,
    )
    return _noise_fallback(f"Extraction failed after retry: {prior_error}")
