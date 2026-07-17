"""Tests for chat-only file text extraction."""

from __future__ import annotations

import json
from io import BytesIO
from zipfile import ZipFile

import fitz  # PyMuPDF

from file_extract import extract_file_text


def _simple_pdf(text: str) -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text, fontsize=11)
    data = doc.tobytes()
    doc.close()
    return data


def test_extract_plain_text():
    data = b"Hello from a notes file.\nSecond line."
    assert extract_file_text("notes.txt", data) == "Hello from a notes file.\nSecond line."


def test_extract_json_conversation():
    payload = {
        "participants": [{"id": "u1", "name": "Alice"}],
        "messages": [
            {"sender": "u1", "text": "We agreed on flat pricing."},
            {"sender": "u1", "text": "Launch is next quarter."},
        ],
    }
    text = extract_file_text("chat.json", json.dumps(payload).encode())
    assert "Alice: We agreed on flat pricing." in text
    assert "Alice: Launch is next quarter." in text


def test_extract_pdf():
    data = _simple_pdf("Quarterly revenue grew 12%.")
    text = extract_file_text("report.pdf", data)
    assert "Quarterly revenue grew 12%." in text


def _ooxml(path: str, xml: str) -> bytes:
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        archive.writestr(path, xml)
    return output.getvalue()


def test_extract_word_document():
    data = _ooxml(
        "word/document.xml",
        '<w:document xmlns:w="urn:test"><w:p><w:t>Company policy</w:t></w:p></w:document>',
    )
    assert "Company policy" in extract_file_text("policy.docx", data)


def test_extract_powerpoint_document():
    data = _ooxml(
        "ppt/slides/slide1.xml",
        '<p:sld xmlns:p="urn:p" xmlns:a="urn:a"><a:t>Quarterly plan</a:t></p:sld>',
    )
    assert "Quarterly plan" in extract_file_text("plan.pptx", data)


def test_extract_csv_normalizes_rows():
    data = b"\xef\xbb\xbfasset,status\nPump 1,operational\n"
    assert extract_file_text("maintenance.csv", data) == (
        "asset,status\nPump 1,operational"
    )


def test_extract_xlsx_resolves_shared_strings():
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        archive.writestr(
            "xl/sharedStrings.xml",
            '<sst xmlns="urn:x"><si><t>Asset</t></si><si><t>Pump 1</t></si></sst>',
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            '<worksheet xmlns="urn:x"><sheetData><row>'
            '<c t="s"><v>0</v></c><c t="s"><v>1</v></c>'
            "</row></sheetData></worksheet>",
        )
    text = extract_file_text("maintenance.xlsx", output.getvalue())
    assert "Asset,Pump 1" in text
