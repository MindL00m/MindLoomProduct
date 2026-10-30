"""Text from any uploaded file, including pictures and scanned PDFs.

Pictures (PNG, JPEG, GIF, WebP) and scanned PDF pages (see ``brain.pdf_reader``,
which reads only the pages that need it) are read with the vision model (``CAPTURE_VISION_MODEL``), using the same transcription
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


def pdf_is_scanned(data: bytes) -> bool:
    """True when any page is mostly a picture (a scan) and needs OCR."""

    from brain.pdf_reader import PdfReadError, read_pdf_text

    try:
        return read_pdf_text(data).needs_ocr
    except PdfReadError:
        return False


async def extract_text_with_ocr(filename: str, data: bytes) -> tuple[str, str]:
    """(text, method) where method is "text", "ocr", or "none"."""

    from brain.archives import ArchiveError, is_zip

    if is_zip(filename, data):
        from loombot.files import zip_text

        try:
            text = (await asyncio.to_thread(zip_text, filename, data)).strip()
        except ArchiveError:
            return "", "none"
        return text, "text" if text else "none"
    if is_image(filename, data):
        if not ocr_available():
            return "", "none"
        text = (await ocr_picture(data, label=filename)).strip()
        return text, "ocr" if text else "none"
    is_pdf = (filename or "").lower().endswith(".pdf") or data[:4] == b"%PDF"
    if is_pdf:
        from brain.pdf_reader import read_pdf

        # A password-protected PDF raises PdfReadError (a ValueError) with a clear message.
        read = await read_pdf(data, label=filename, ocr=ocr_available())
        text = read.text().strip()
        if text and read.notes:
            text = (text + "\n\n" + " ".join(f"({note})" for note in read.notes)).strip()
        return text, "ocr" if read.used_ocr else ("text" if text else "none")
    text = (await asyncio.to_thread(extract_file_text, filename, data)).strip()
    return text, "text" if text else "none"


def no_text_reason(filename: str, data: bytes) -> str:
    if (is_image(filename, data) or (filename or "").lower().endswith(".pdf")) and not ocr_available():
        return (
            "No readable text was found. Pictures and scanned PDFs are read with the vision "
            "model; set OPENAI_API_KEY and keep EMAIL_ATTACHMENT_OCR on."
        )
    return "No readable text was found in this file."
