"""Pictures and scanned PDFs are read with the vision model."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from brain import document_ingestion, media_text
from loombot import files


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (40, 20), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def _blank_pdf(pages: int = 2) -> bytes:
    import fitz

    doc = fitz.open()
    for _ in range(pages):
        doc.new_page()
    return doc.tobytes()


def _scan_pdf(pages: int = 2) -> bytes:
    """Pages that are only a picture of text, like a scanner produces."""

    import fitz

    picture = fitz.open()
    source = picture.new_page()
    source.insert_text((50, 60), "Receipt total 42.00", fontsize=14)
    png = source.get_pixmap(matrix=fitz.Matrix(2, 2)).tobytes("png")
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page().insert_image(fitz.Rect(0, 0, 595, 842), stream=png)
    return doc.tobytes()


def _text_pdf() -> bytes:
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Commercial invoice number 42 for 20 MT of SOP " * 3)
    return doc.tobytes()


def test_is_image_by_name_or_bytes() -> None:
    assert media_text.is_image("receipt.JPG")
    assert media_text.is_image("upload", _png())
    assert not media_text.is_image("notes.txt", b"hello")


def test_scanned_pdf_detection() -> None:
    assert media_text.pdf_is_scanned(_scan_pdf())
    assert not media_text.pdf_is_scanned(_text_pdf())
    assert not media_text.pdf_is_scanned(_blank_pdf())  # nothing on the page to read


async def test_picture_is_transcribed(monkeypatch) -> None:
    async def ocr(data, *, label):
        return "INVOICE 42 | SOP | 20 MT"

    monkeypatch.setattr(media_text, "ocr_available", lambda: True)
    monkeypatch.setattr(media_text, "ocr_picture", ocr)
    text, method = await media_text.extract_text_with_ocr("bill.png", _png())
    assert (text, method) == ("INVOICE 42 | SOP | 20 MT", "ocr")


async def test_scanned_pdf_falls_back_to_ocr(monkeypatch) -> None:
    async def ocr(images, *, label):
        return "BILL OF LADING MSCU4471"

    monkeypatch.setattr(media_text, "ocr_available", lambda: True)
    monkeypatch.setattr("weaver.email_content.ocr_images", ocr)
    text, method = await media_text.extract_text_with_ocr("scan.pdf", _scan_pdf())
    assert method == "ocr" and "BILL OF LADING" in text


async def test_text_pdf_skips_ocr(monkeypatch) -> None:
    async def ocr(*args, **kwargs):
        raise AssertionError("OCR should not run for a text PDF")

    monkeypatch.setattr(media_text, "ocr_available", lambda: True)
    monkeypatch.setattr("weaver.email_content.ocr_images", ocr)
    text, method = await media_text.extract_text_with_ocr("invoice.pdf", _text_pdf())
    assert method == "text" and "Commercial invoice" in text


async def test_without_ocr_pictures_explain_why(monkeypatch) -> None:
    monkeypatch.setattr(media_text, "ocr_available", lambda: False)
    assert await media_text.extract_text_with_ocr("bill.png", _png()) == ("", "none")
    assert "vision model" in media_text.no_text_reason("bill.png", _png())


def test_pictures_are_supported_documents() -> None:
    document_ingestion.ensure_supported_document("photo.jpeg")
    assert document_ingestion.document_source("photo.webp") == "image"


def test_files_accepts_pictures() -> None:
    data = _png()
    assert files.kind_from_name("Receipt.png") == "png"
    files.validate_binary("png", data)
    assert files.binary_stats("png", data) == (20, 40)
    with pytest.raises(Exception):
        files.validate_binary("jpg", b"not a picture")


async def test_files_reads_pictures_with_ocr(monkeypatch) -> None:
    async def fake_bytes(org_id, user_id, file_id):
        return _png(), {}

    async def extract(name, data):
        return "Total 1,040.00", "ocr"

    monkeypatch.setattr(files, "get_file_bytes", fake_bytes)
    monkeypatch.setattr(media_text, "extract_text_with_ocr", extract)
    text = await files.read_text_for_ask(
        "o", "u", {"file_id": "f", "kind": "png", "name": "r.png", "updated_at": "t"}
    )
    assert "Total 1,040.00" in text and "OCR" in text
