"""PDF reading accuracy: tables, reading order, hyphens, ligatures, scans, passwords."""

from __future__ import annotations

import fitz
import pytest

from brain import pdf_chunker, pdf_reader
from brain.document_ingestion import document_pieces


def _ruled_invoice() -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 60), "COMMERCIAL INVOICE", fontsize=18)
    page.insert_text((50, 90), "Invoice no. INV-2026-042", fontsize=10)
    cols, rows = [50, 260, 340, 420, 520], [140, 160, 180, 200]
    data = [["Description", "Qty (MT)", "Rate", "Amount"],
            ["SOP 52% granular", "20.000", "520.50", "10,410.00"],
            ["MOP 60% standard", "10.000", "300.00", "3,000.00"]]
    for y in rows:
        page.draw_line((cols[0], y), (cols[-1], y))
    for x in cols:
        page.draw_line((x, rows[0]), (x, rows[-1]))
    for r, row in enumerate(data):
        for c, value in enumerate(row):
            page.insert_text((cols[c] + 4, rows[r] + 14), value, fontsize=9)
    page.insert_text((50, 230), "Total USD 13,410.00", fontsize=11)
    return doc.tobytes()


def _borderless_packing_list() -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 60), "PACKING LIST", fontsize=16)
    page.insert_text((50, 90), "Packed in 50 kg bags and loaded in two containers as listed.", fontsize=10)
    cols = [50, 250, 330, 410]
    rows = [["Description", "Bags", "Net kg", "Gross kg"],
            ["SOP 52% granular", "400", "20,000", "20,080"],
            ["MOP 60% standard", "200", "10,000", "10,040"],
            ["Total", "600", "30,000", "30,120"]]
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            page.insert_text((cols[c], 130 + r * 16), value, fontsize=9)
    page.insert_text((50, 220), "Marks: GULF AGRO / DUBAI", fontsize=10)
    return doc.tobytes()


def _two_columns() -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 50), "Market update", fontsize=16)
    page.insert_textbox(fitz.Rect(50, 80, 280, 400), "LEFT SOP prices rose in the Gulf. " * 8, fontsize=10)
    page.insert_textbox(fitz.Rect(320, 80, 550, 400), "RIGHT MOP freight from Vancouver. " * 8, fontsize=10)
    return doc.tobytes()


def _scan_pages(text_pages: int, scanned_pages: int) -> bytes:
    picture = fitz.open()
    source = picture.new_page()
    source.insert_text((50, 60), "SCANNED BL MSCU4471", fontsize=14)
    png = source.get_pixmap(matrix=fitz.Matrix(2, 2)).tobytes("png")
    doc = fitz.open()
    for _ in range(text_pages):
        doc.new_page().insert_text((50, 60), "Typed cover page for the shipment documents.", fontsize=11)
    for _ in range(scanned_pages):
        doc.new_page().insert_image(fitz.Rect(0, 0, 595, 842), stream=png)
    return doc.tobytes()


def test_ruled_table_keeps_rows_and_columns() -> None:
    read = pdf_reader.read_pdf_text(_ruled_invoice())
    text = read.text()
    assert read.pages[0].tables == 1
    assert "| SOP 52% granular | 20.000 | 520.50 | 10,410.00 |" in text
    assert "| MOP 60% standard | 10.000 | 300.00 | 3,000.00 |" in text
    assert text.count("SOP 52% granular") == 1  # table text is not repeated
    assert text.index("Invoice no.") < text.index("| Description") < text.index("Total USD")


def test_borderless_table_is_detected() -> None:
    text = pdf_reader.read_pdf_text(_borderless_packing_list()).text()
    assert "| Description | Bags | Net kg | Gross kg |" in text
    assert "| Total | 600 | 30,000 | 30,120 |" in text
    assert "Packed in 50 kg bags" in text and "| Packed" not in text  # prose is not a table
    assert "Marks: GULF AGRO / DUBAI" in text


def test_two_columns_read_left_then_right() -> None:
    text = pdf_reader.read_pdf_text(_two_columns()).text()
    assert text.index("Market update") < text.index("LEFT") < text.index("RIGHT")
    assert text.rindex("LEFT") < text.index("RIGHT")
    assert "|" not in text  # columns of prose are not a table


def test_hyphens_joined_and_ligatures_split() -> None:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 60), "The ship-", fontsize=11)
    page.insert_text((50, 75), "ment leaves on 20 October.", fontsize=11)
    read = pdf_reader.read_pdf_text(doc.tobytes())
    assert "shipment leaves" in read.text()
    assert not read.needs_ocr  # a short typed page is not a scan

    from loombot.file_builders import markdown_to_pdf

    # The HTML layout engine typesets "fi" and "fl" as ligature glyphs.
    ligatures = pdf_reader.read_pdf_text(markdown_to_pdf("Quality per the specification, final at port."))
    assert "specification, final" in ligatures.text()


def test_only_scanned_pages_need_ocr() -> None:
    read = pdf_reader.read_pdf_text(_scan_pages(1, 2))
    assert [p.needs_ocr for p in read.pages] == [False, True, True]


async def test_ocr_reads_each_scanned_page_on_its_own(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_ocr(images, *, label):
        calls.append(label)
        assert len(images) == 1
        return f"OCR text for {label.rsplit(' ', 1)[-1]}"

    monkeypatch.setattr("weaver.email_content.ocr_images", fake_ocr)
    read = await pdf_reader.read_pdf(_scan_pages(1, 2), label="bl.pdf", ocr=True)
    assert sorted(calls) == ["bl.pdf, page 2", "bl.pdf, page 3"]
    assert [p.method for p in read.pages] == ["text", "ocr", "ocr"]
    assert "Typed cover page" in read.text() and "OCR text for 3" in read.text()


async def test_ocr_limit_and_disabled_notes(monkeypatch) -> None:
    async def fake_ocr(images, *, label):
        return "x"

    monkeypatch.setattr("weaver.email_content.ocr_images", fake_ocr)
    limited = await pdf_reader.read_pdf(_scan_pages(0, 3), ocr=True, max_ocr_pages=2)
    assert [p.method for p in limited.pages] == ["ocr", "ocr", "empty"]
    assert "1 more" in limited.notes[0]
    off = await pdf_reader.read_pdf(_scan_pages(0, 1), ocr=False)
    assert "vision OCR is off" in off.notes[0]


def test_password_protected_pdf_is_refused() -> None:
    doc = fitz.open()
    doc.new_page().insert_text((50, 60), "secret")
    data = doc.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="pw", owner_pw="owner")
    with pytest.raises(pdf_reader.PdfReadError, match="password"):
        pdf_reader.read_pdf_text(data)


def test_damaged_pdf_is_refused() -> None:
    with pytest.raises(pdf_reader.PdfReadError):
        pdf_reader.read_pdf_text(b"%PDF-1.4 not really")


def test_chunker_keeps_a_table_whole_and_uses_ocr_pages() -> None:
    chunks = pdf_chunker.chunk_pdf(_ruled_invoice())
    joined = "\n".join(c.text for c in chunks)
    assert "| SOP 52% granular | 20.000 | 520.50 | 10,410.00 |" in joined
    scanned = pdf_chunker.chunk_pdf(_scan_pages(1, 1), ocr_pages={2: "BL MSCU4471\n20 MT SOP"})
    assert any("BL MSCU4471" in c.text and c.page_end == 2 for c in scanned)


def test_long_documents_split_and_tables_repeat_their_header() -> None:
    rows = "\n".join(f"| Item {i} | {i * 10} | 520.00 |" for i in range(400))
    text = "Intro paragraph.\n\n| Item | Qty | Rate |\n|---|---|---|\n" + rows + "\n\nClosing paragraph."
    pieces = document_pieces(text, max_tokens=300)
    assert len(pieces) > 5
    table_pieces = [p for p in pieces if "| Item 1" in p or "| Item 399" in p]
    assert all(p.count("| Item | Qty | Rate |") >= 1 for p in table_pieces)
    assert "Intro paragraph." in pieces[0] and "Closing paragraph." in pieces[-1]


async def test_failed_ocr_is_reported(monkeypatch) -> None:
    async def broken(images, *, label):
        raise RuntimeError("invalid API key")

    monkeypatch.setattr("weaver.email_content.ocr_images", broken)
    read = await pdf_reader.read_pdf(_scan_pages(1, 1), ocr=True)
    assert "Typed cover page" in read.text()
    assert "page(s) 2 could not be read" in read.notes[0]
