"""Message chunker.

Groups a flat list of messages into topically-coherent :class:`Chunk` objects
using three boundary rules:

1. A time gap larger than ``CHUNK_GAP_MINUTES`` between consecutive messages.
2. A topic shift: a *new* speaker introducing a named entity that has not
   appeared in the last five messages.
3. A hard token ceiling of ``CHUNK_MAX_TOKENS`` (cl100k_base), splitting at the
   nearest message boundary before the limit is exceeded.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import timedelta

import tiktoken

from config import get_settings
from models import Chunk, IncomingMessage, Message

logger = logging.getLogger(__name__)

_ENCODING = tiktoken.get_encoding("cl100k_base")

# Candidate named entities: capitalised tokens / runs of capitalised tokens.
_ENTITY_RE = re.compile(r"\b[A-Z][a-zA-Z0-9]+(?:\s+[A-Z][a-zA-Z0-9]+)*\b")

# Common words that are capitalised purely because they start a sentence and
# should not be treated as named entities.
_ENTITY_STOPWORDS = {
    "i", "i'm", "i'll", "i've", "the", "a", "an", "this", "that", "these", "those",
    "we", "you", "he", "she", "it", "they", "them", "yes", "no", "ok", "okay",
    "hi", "hey", "hello", "thanks", "thank", "please", "what", "when", "where",
    "who", "why", "how", "can", "could", "would", "should", "did", "do", "does",
    "is", "are", "was", "were", "will", "and", "but", "so", "if", "then", "let",
    "let's", "good", "morning", "evening", "sure", "great", "got", "have", "has",
}


def _extract_entities(text: str) -> set[str]:
    """Heuristically extract named-entity candidates from text (lower-cased)."""

    entities: set[str] = set()
    for match in _ENTITY_RE.findall(text):
        token = match.strip().lower()
        if token and token not in _ENTITY_STOPWORDS and not token.isdigit():
            entities.add(token)
    return entities


def _message_line(message: IncomingMessage) -> str:
    """Render a message as a single 'Speaker: body' line."""

    return f"{message.sender}: {message.text}"


def _raw_text(messages: list[IncomingMessage]) -> str:
    """Concatenate messages into the chunk's raw text representation."""

    return "\n".join(_message_line(message) for message in messages)


def _count_tokens(messages: list[IncomingMessage]) -> int:
    """Count cl100k_base tokens for the rendered messages."""

    return len(_ENCODING.encode(_raw_text(messages)))


def _distinct_speakers(messages: list[IncomingMessage]) -> list[str]:
    """Return distinct speakers in first-seen order."""

    seen: list[str] = []
    for message in messages:
        if message.sender not in seen:
            seen.append(message.sender)
    return seen


def _build_chunk(messages: list[IncomingMessage]) -> Chunk:
    """Materialise a :class:`Chunk` from an ordered list of messages."""

    return Chunk(
        chunk_id=str(uuid.uuid4()),
        messages=[
            Message(speaker=message.sender, timestamp=message.timestamp, body=message.text)
            for message in messages
        ],
        speakers=_distinct_speakers(messages),
        start_time=messages[0].timestamp,
        end_time=messages[-1].timestamp,
        raw_text=_raw_text(messages),
    )


def _is_topic_shift(
    message: IncomingMessage, recent: list[IncomingMessage], previous_speaker: str
) -> bool:
    """Detect a topic shift introduced by a new speaker.

    Returns ``True`` when the current message is from a different speaker than
    the previous message *and* mentions a named entity that does not appear in
    the (up to) last five messages.
    """

    if message.sender == previous_speaker:
        return False

    recent_entities: set[str] = set()
    for prior in recent:
        recent_entities |= _extract_entities(prior.text)

    new_entities = _extract_entities(message.text) - recent_entities
    return bool(new_entities)


def chunk_messages(messages: list[IncomingMessage]) -> list[Chunk]:
    """Group messages into chunks according to gap, topic, and token rules.

    Args:
        messages: Ordered, speaker-normalised messages.

    Returns:
        Ordered list of :class:`Chunk` objects.
    """

    if not messages:
        return []

    settings = get_settings()
    gap_limit = timedelta(minutes=settings.chunk_gap_minutes)
    max_tokens = settings.chunk_max_tokens

    chunks: list[Chunk] = []
    current: list[IncomingMessage] = []

    def flush() -> None:
        if current:
            chunks.append(_build_chunk(current))
            current.clear()

    for message in messages:
        if not current:
            current.append(message)
            continue

        previous = current[-1]

        # Rule 1: time gap.
        if message.timestamp - previous.timestamp > gap_limit:
            flush()
            current.append(message)
            continue

        # Rule 2: topic shift by a new speaker.
        if _is_topic_shift(message, current[-5:], previous.sender):
            flush()
            current.append(message)
            continue

        # Rule 3: token ceiling -> split at the message boundary before the limit.
        if _count_tokens(current + [message]) > max_tokens:
            flush()
            current.append(message)
            continue

        current.append(message)

    flush()

    logger.info("Chunked %d messages into %d chunks", len(messages), len(chunks))
    return chunks
