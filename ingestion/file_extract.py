"""Extract plain text from uploaded files for chat-only (ephemeral) context."""

from __future__ import annotations

import json
import logging

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)

_MAX_CHARS = 120_000


def _truncate(text: str) -> str:
    if len(text) <= _MAX_CHARS:
        return text
    logger.info("Truncating extracted text from %d to %d chars", len(text), _MAX_CHARS)
    return text[:_MAX_CHARS] + "\n\n[… truncated …]"


def _extract_pdf(data: bytes) -> str:
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        pages = [page.get_text("text") for page in doc]
        return _truncate("\n\n".join(p.strip() for p in pages if p.strip()))
    finally:
        doc.close()


def _extract_json(data: bytes) -> str:
    payload = json.loads(data.decode("utf-8"))
    if isinstance(payload, dict) and isinstance(payload.get("messages"), list):
        lines: list[str] = []
        participants = {
            p.get("id", p.get("name", "")): p.get("name", p.get("id", ""))
            for p in payload.get("participants", [])
            if isinstance(p, dict)
        }
        for msg in payload["messages"]:
            if not isinstance(msg, dict):
                continue
            sender = participants.get(msg.get("sender", ""), msg.get("sender", "Unknown"))
            body = str(msg.get("text", "")).strip()
            if body:
                lines.append(f"{sender}: {body}")
        if lines:
            return _truncate("\n".join(lines))
    return _truncate(json.dumps(payload, indent=2)[:_MAX_CHARS])


def extract_file_text(filename: str, data: bytes) -> str:
    """Return extracted text for PDF, JSON (incl. conversations), or plain text."""

    name = (filename or "upload").lower()
    if name.endswith(".pdf") or data[:4] == b"%PDF":
        return _extract_pdf(data)
    if name.endswith(".json"):
        return _extract_json(data)
    return _truncate(data.decode("utf-8", errors="replace"))
