"""WhatsApp export (.txt) parser.

Handles the two common WhatsApp export line formats, concatenates multi-line
messages onto their owning message, and discards media / system messages.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

from models import Message

logger = logging.getLogger(__name__)

# Format 1: [DD/MM/YYYY, HH:MM:SS] Speaker Name: message body
_BRACKET_HEADER = re.compile(
    r"^\[(?P<date>\d{1,2}/\d{1,2}/\d{2,4}),\s*"
    r"(?P<time>\d{1,2}:\d{2}(?::\d{2})?(?:\s*[APap][Mm])?)\]\s*"
    r"(?P<rest>.*)$"
)

# Format 2: DD/MM/YYYY, HH:MM:SS - Speaker Name: message body
_DASH_HEADER = re.compile(
    r"^(?P<date>\d{1,2}/\d{1,2}/\d{2,4}),\s*"
    r"(?P<time>\d{1,2}:\d{2}(?::\d{2})?(?:\s*[APap][Mm])?)\s*-\s*"
    r"(?P<rest>.*)$"
)

# Full message bodies (lower-cased) that mark a media placeholder to drop.
# WhatsApp always emits these as the entire body of their own timestamped line,
# so we match the whole body rather than a substring to avoid dropping real
# messages that merely mention these words.
_MEDIA_MARKERS = frozenset(
    {
        "<media omitted>",
        "media omitted",
        "image omitted",
        "video omitted",
        "audio omitted",
        "sticker omitted",
        "gif omitted",
        "document omitted",
        "contact card omitted",
        "this message was deleted",
        "you deleted this message",
    }
)

# Substrings that identify WhatsApp system notices (matched case-insensitively).
_SYSTEM_MARKERS = (
    "messages and calls are end-to-end encrypted",
    "your security code with",
    "changed the subject",
    "changed this group's icon",
    "changed the group description",
    "changed their phone number",
    "created group",
    "you joined using this group's invite link",
    "deleted this message",
    "this message was deleted",
    "missed voice call",
    "missed video call",
)

# Speaker-less system events such as "Soham added Amey", "Amey left".
_SYSTEM_EVENT_VERBS = re.compile(
    r"\b(added|removed|left|joined|created|changed|pinned|turned on|turned off|"
    r"now an admin|no longer an admin)\b",
    re.IGNORECASE,
)

_TIME_FORMATS = (
    "%H:%M:%S",
    "%H:%M",
    "%I:%M:%S %p",
    "%I:%M %p",
)


def _parse_timestamp(date_str: str, time_str: str) -> datetime | None:
    """Parse a WhatsApp date + time pair into a ``datetime`` (DD/MM/YYYY order)."""

    day, month, year = (part.strip() for part in date_str.split("/"))
    if len(year) == 2:
        year = f"20{year}"

    normalised_time = re.sub(r"\s+", " ", time_str.strip())
    for time_fmt in _TIME_FORMATS:
        try:
            parsed_time = datetime.strptime(normalised_time, time_fmt)
        except ValueError:
            continue
        try:
            return datetime(
                year=int(year),
                month=int(month),
                day=int(day),
                hour=parsed_time.hour,
                minute=parsed_time.minute,
                second=parsed_time.second,
            )
        except ValueError:
            return None
    return None


def _match_header(line: str) -> tuple[datetime, str] | None:
    """Return ``(timestamp, rest)`` if ``line`` starts a new message, else ``None``."""

    match = _BRACKET_HEADER.match(line) or _DASH_HEADER.match(line)
    if match is None:
        return None
    timestamp = _parse_timestamp(match.group("date"), match.group("time"))
    if timestamp is None:
        return None
    return timestamp, match.group("rest").strip()


def _is_media(body: str) -> bool:
    """Return ``True`` if the body is exactly a media placeholder to be dropped."""

    stripped = body.replace("\u200e", "").strip().lower()
    return stripped in _MEDIA_MARKERS


def _is_system_notice(rest: str) -> bool:
    """Return ``True`` for WhatsApp system notices (no real speaker)."""

    lowered = rest.lower()
    return any(marker in lowered for marker in _SYSTEM_MARKERS)


def _split_speaker(rest: str) -> tuple[str, str] | None:
    """Split ``"Speaker: body"``. Returns ``None`` for speaker-less system lines."""

    separator_index = rest.find(": ")
    if separator_index == -1:
        # No "speaker: body" structure -> treat as a system event line.
        return None

    speaker = rest[:separator_index].strip()
    body = rest[separator_index + 2 :].strip()

    if not speaker:
        return None
    # Group membership events ("Soham added Amey") have no colon, but guard the
    # rare colon-containing variants too.
    if _SYSTEM_EVENT_VERBS.search(speaker):
        return None
    # A real speaker name should not contain newlines.
    if "\n" in speaker:
        return None
    return speaker, body


def parse_whatsapp_export(file_text: str) -> list[Message]:
    """Parse raw WhatsApp export text into a list of :class:`Message` objects.

    Args:
        file_text: Full UTF-8 contents of a WhatsApp ``.txt`` export.

    Returns:
        Ordered list of messages with media and system messages removed and
        multi-line bodies correctly concatenated.
    """

    messages: list[Message] = []

    current_speaker: str | None = None
    current_timestamp: datetime | None = None
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_speaker, current_timestamp, current_lines
        if current_speaker is None or current_timestamp is None:
            current_lines = []
            return
        body = "\n".join(current_lines).strip()
        if body and not _is_media(body):
            messages.append(Message(speaker=current_speaker, timestamp=current_timestamp, body=body))
        current_speaker = None
        current_timestamp = None
        current_lines = []

    for raw_line in file_text.splitlines():
        # Strip the left-to-right marker WhatsApp injects before some lines.
        line = raw_line.replace("\u200e", "")
        header = _match_header(line)

        if header is None:
            # Continuation of the message currently being assembled.
            if current_speaker is not None:
                current_lines.append(raw_line.rstrip("\n"))
            continue

        # A new header line closes the previous message first.
        flush()

        timestamp, rest = header
        if _is_system_notice(rest):
            continue

        split = _split_speaker(rest)
        if split is None:
            # Speaker-less system event (e.g. "X added Y") -> discard.
            continue

        speaker, body = split
        current_speaker = speaker
        current_timestamp = timestamp
        current_lines = [body]

    flush()

    logger.info("Parsed %d messages from WhatsApp export", len(messages))
    return messages
