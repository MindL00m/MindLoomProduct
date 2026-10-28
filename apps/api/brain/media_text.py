"""Text from any uploaded file, including pictures and scanned PDFs.

Pictures (PNG, JPEG, GIF, WebP) and PDF pages without selectable text are read
with the vision model (``CAPTURE_VISION_MODEL``), using the same transcription
prompt as email attachments. OCR follows ``EMAIL_ATTACHMENT_OCR`` and needs an
OpenAI key; without it, pictures and scans yield no text and callers explain why.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import PurePath

from brain.file_extract import extract_file_text

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
# Pages read from a scanned PDF upload, sent to the vision model a few at a time.
MAX_UPLOAD_OCR_PAGES = 20
_OCR_BATCH = 5
_SCANNED_PAGE_CHARS = 40


def is_image(filename: str, data: bytes = b"") -> bool:
    if PurePath(filename or "").suffix.lower() in IMAGE_EXTENSIONS:
        return True
    head = data[:12]
    return (
        head.startswith(b"\x89PNG")
        or head.startswith(b"\xff\xd8\xff")
        or head.startswith(b"GIF8")
        or (head.startswith(b"RIFF") and head[8:12] == b"WEBP")
    )


def ocr_available() -> bool:
    from weaver.email_content import _ocr_enabled

    return _ocr_enabled()


async def ocr_picture(data: bytes, *, label: str) -> str:
    from weaver.email_content import ocr_images

    return await ocr_images([data], label=label)


async def ocr_pdf(data: bytes, *, label: str, max_pages: int = MAX_UPLOAD_OCR_PAGES) -> str:
    from weaver.email_content import _pdf_page_images, ocr_images

    images = await asyncio.to_thread(_pdf_page_images, data, max_pages)
    parts: list[str] = []
    for start in range(0, len(images), _OCR_BATCH):
        batch = images[start:start + _OCR_BATCH]
        text = await ocr_images(batch, label=f"{label}, pages {start + 1}-{start + len(batch)}")
        if text:
            parts.append(f"[Pages {start + 1}-{start + len(batch)}]\n{text}")
    return "\n\n".join(parts)


def pdf_is_scanned(data: bytes) -> bool:
    from weaver.email_content import _pdf_pages

    pages = _pdf_pages(data)
    return bool(pages) and sum(len(p) for p in pages) < _SCANNED_PAGE_CHARS * len(pages)


async def extract_text_with_ocr(filename: str, data: bytes) -> tuple[str, str]:
    """(text, method) where method is "text", "ocr", or "none"."""

    if is_image(filename, data):
        if not ocr_available():
            return "", "none"
        text = (await ocr_picture(data, label=filename)).strip()
        return text, "ocr" if text else "none"
    is_pdf = (filename or "").lower().endswith(".pdf") or data[:4] == b"%PDF"
    text = (await asyncio.to_thread(extract_file_text, filename, data)).strip()
    if is_pdf and ocr_available():
        try:
            scanned = await asyncio.to_thread(pdf_is_scanned, data)
        except Exception:  # noqa: BLE001 - a damaged PDF keeps whatever text it had
            scanned = False
        if scanned:
            transcribed = (await ocr_pdf(data, label=filename)).strip()
            if transcribed:
                return transcribed, "ocr"
    return text, "text" if text else "none"


def no_text_reason(filename: str, data: bytes) -> str:
    if (is_image(filename, data) or (filename or "").lower().endswith(".pdf")) and not ocr_available():
        return (
            "No readable text was found. Pictures and scanned PDFs are read with the vision "
            "model; set OPENAI_API_KEY and keep EMAIL_ATTACHMENT_OCR on."
        )
    return "No readable text was found in this file."
