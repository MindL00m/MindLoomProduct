"""Tests for the layout-aware PDF chunker (heading vs paragraph strategies)."""

from __future__ import annotations

import fitz  # PyMuPDF
import pytest

from pdf_chunker import chunk_pdf


def _make_pdf(pages: list[list[tuple[str, float]]]) -> bytes:
    """Build a PDF from pages of (text, font_size) blocks laid top-to-bottom."""

    doc = fitz.open()
    for blocks in pages:
        page = doc.new_page()
        y = 72.0
        for text, size in blocks:
            page.insert_text((72, y), text, fontsize=size)
            y += size * 1.6 * (text.count("\n") + 1) + 12
    data = doc.tobytes()
    doc.close()
    return data


def test_chunk_by_headings_splits_sections_with_page_spans():
    intro_body = "The project started in spring. The team set clear goals."
    background_body = "Prior work was limited. Several gaps remained open."
    methods_body = "We collected data carefully. Then we analysed the results."
    data = _make_pdf(
        [
            [
                ("Introduction", 20),
                (intro_body, 11),
                ("Background", 20),
                (background_body, 11),
            ],
            [
                ("Methods", 20),
                (methods_body, 11),
            ],
        ]
    )

    chunks = chunk_pdf(data)

    # One section per heading.
    assert len(chunks) == 3
    assert chunks[0].text.startswith("Introduction")

    # Sections are disjoint and ordered by character offset.
    for a, b in zip(chunks, chunks[1:]):
        assert a.char_start < a.char_end <= b.char_start

    # The "Methods" section lives on page 2.
    methods = next(c for c in chunks if c.text.startswith("Methods"))
    assert methods.page_start == 2
    assert methods.page_end == 2
    # The first two sections are on page 1.
    assert chunks[0].page_start == 1


def test_chunk_paragraphs_when_no_headings_overlaps_on_sentences():
    # Uniform font size => no headings => paragraph/sentence chunking.
    sentences = [f"Sentence number {i} describes the migration plan." for i in range(1, 16)]
    body = "\n".join(sentences)
    data = _make_pdf([[(body, 11)]])

    chunks = chunk_pdf(data, target_tokens=24, overlap_sentences=1)

    assert len(chunks) >= 2
    # Every chunk ends on a sentence boundary.
    for chunk in chunks:
        assert chunk.text.rstrip().endswith(".")
        assert chunk.char_end > chunk.char_start
        assert chunk.page_start == 1 and chunk.page_end == 1
    # Overlap: at least one chunk begins before the previous one ended.
    assert any(b.char_start < a.char_end for a, b in zip(chunks, chunks[1:]))


def test_empty_pdf_returns_no_chunks():
    doc = fitz.open()
    doc.new_page()  # a blank page, no text
    data = doc.tobytes()
    doc.close()

    assert chunk_pdf(data) == []
