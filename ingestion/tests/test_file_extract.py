"""Tests for chat-only file text extraction."""

from __future__ import annotations

import json

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
