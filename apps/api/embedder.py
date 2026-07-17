"""Chunk embedding via OpenAI ``text-embedding-3-small``."""

from __future__ import annotations

import logging

from openai import AsyncOpenAI

from config import get_settings
from models import Chunk, ChunkMetadata

logger = logging.getLogger(__name__)

_MODEL = "text-embedding-3-small"
EMBEDDING_DIMENSIONS = 1536


def _build_embedding_input(chunk: Chunk, metadata: ChunkMetadata) -> str:
    """Combine the chunk summary and raw text into a single embedding input."""

    return f"{metadata.summary}\n\n{chunk.raw_text}"


async def embed_chunk(chunk: Chunk, metadata: ChunkMetadata) -> list[float]:
    """Embed a chunk's summary + raw text and return the vector.

    Args:
        chunk: The chunk to embed.
        metadata: Extracted metadata; its summary is prepended to the input.

    Returns:
        A 1536-dimensional embedding vector as a list of floats.
    """

    settings = get_settings()
    client = AsyncOpenAI(
        api_key=settings.openai_api_key,
        timeout=settings.openai_request_timeout_seconds,
    )

    embedding_input = _build_embedding_input(chunk, metadata)
    response = await client.embeddings.create(model=_MODEL, input=embedding_input)
    vector = response.data[0].embedding
    logger.debug("Embedded chunk %s (%d dims)", chunk.chunk_id, len(vector))
    return list(vector)
