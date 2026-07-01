"""PDF chunking with layout-aware structure detection (PyMuPDF).

Pipeline:

    PDF bytes
      -> extract text + layout metadata (font size / bold / page) per line
      -> estimate the body font size, then detect headings by font-size / bold
         heuristics
          -> headings found  -> chunk by heading hierarchy (one section per
             heading), splitting oversized sections at sentence boundaries with
             overlap
          -> no headings     -> paragraph-grouped semantic chunks over sentences
             with sentence-boundary trimming + overlap
      -> every chunk records page_start, page_end, char_start, char_end
         (offsets are into the document's full extracted text)

The resulting :class:`PdfChunk` objects are then handed to the shared LLM
classifier by the ingestion pipeline.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass

import fitz  # PyMuPDF

from config import get_settings

logger = logging.getLogger(__name__)

# Font flag bit 4 (value 16) marks bold glyphs in PyMuPDF span metadata.
_BOLD_FLAG = 1 << 4

# A heading's font must exceed the body font by this ratio (size heuristic).
_HEADING_SIZE_RATIO = 1.15
# Headings are short; anything longer (in words) is treated as body text.
_MAX_HEADING_WORDS = 14
# Number of trailing sentences carried into the next chunk when splitting.
_DEFAULT_OVERLAP_SENTENCES = 2


@dataclass
class PdfChunk:
    """A chunk of a PDF with its position in the document."""

    text: str
    page_start: int  # 1-based, inclusive
    page_end: int  # 1-based, inclusive
    char_start: int  # offset into the full extracted text
    char_end: int


@dataclass
class _Line:
    """A single laid-out text line with font metadata and char offsets."""

    text: str
    page: int  # 1-based
    size: float  # max span font size on the line
    bold: bool
    char_start: int
    char_end: int


def _estimate_tokens(text: str) -> int:
    """Cheap, dependency-free token estimate (~4 chars/token)."""

    return max(1, len(text) // 4)


def _span_is_bold(span: dict) -> bool:
    if span.get("flags", 0) & _BOLD_FLAG:
        return True
    return "bold" in str(span.get("font", "")).lower()


def _extract_lines(doc: "fitz.Document") -> tuple[str, list[_Line]]:
    """Extract text lines with layout metadata and their char offsets.

    Returns the full extracted text (lines joined by newlines) and the per-line
    metadata; the two share a single coordinate space so a line's
    ``char_start``/``char_end`` index directly into the full text.
    """

    lines: list[_Line] = []
    parts: list[str] = []
    offset = 0

    for page_index in range(doc.page_count):
        page = doc[page_index]
        data = page.get_text("dict")
        for block in data.get("blocks", []):
            if block.get("type") != 0:  # 0 == text block
                continue
            for line in block.get("lines", []):
                spans = line.get("spans", [])
                text = "".join(span.get("text", "") for span in spans).strip()
                if not text:
                    continue
                size = max((span.get("size", 0.0) for span in spans), default=0.0)
                bold = any(_span_is_bold(span) for span in spans)
                start = offset
                end = offset + len(text)
                lines.append(
                    _Line(
                        text=text,
                        page=page_index + 1,
                        size=round(size, 1),
                        bold=bold,
                        char_start=start,
                        char_end=end,
                    )
                )
                parts.append(text)
                offset = end + 1  # +1 for the newline joining lines

    return "\n".join(parts), lines


def _body_font_size(lines: list[_Line]) -> float:
    """Estimate the dominant body font size (mode over longer, body-like lines)."""

    counter = Counter(line.size for line in lines if len(line.text) > 15)
    if not counter:
        counter = Counter(line.size for line in lines)
    return counter.most_common(1)[0][0] if counter else 0.0


def _is_heading(line: _Line, body_size: float) -> bool:
    """Heuristically decide whether a line is a heading.

    A short line qualifies when it is either noticeably larger than the body
    font, or bold and at least body-sized.
    """

    words = len(line.text.split())
    if words == 0 or words > _MAX_HEADING_WORDS:
        return False
    larger = line.size >= body_size * _HEADING_SIZE_RATIO
    bold_heading = line.bold and line.size >= body_size * 1.03
    return larger or bold_heading


_SENTENCE_END_RE = re.compile(r"[.!?]+(?=\s|$)")


def _sentence_spans(text: str) -> list[tuple[int, int]]:
    """Return (start, end) char spans of sentences within ``text``.

    Boundaries are trimmed of surrounding whitespace so chunk edges land on
    sentence boundaries.
    """

    raw: list[tuple[int, int]] = []
    start = 0
    for match in _SENTENCE_END_RE.finditer(text):
        raw.append((start, match.end()))
        start = match.end()
    if start < len(text):
        raw.append((start, len(text)))

    spans: list[tuple[int, int]] = []
    for s, e in raw:
        while s < e and text[s].isspace():
            s += 1
        while e > s and text[e - 1].isspace():
            e -= 1
        if e > s:
            spans.append((s, e))
    return spans


def _page_range(lines: list[_Line], char_start: int, char_end: int) -> tuple[int, int]:
    """Return (page_start, page_end) for the lines overlapping a char span."""

    pages = [
        line.page
        for line in lines
        if line.char_start < char_end and line.char_end > char_start
    ]
    if not pages:
        nearest = min(lines, key=lambda line: abs(line.char_start - char_start))
        pages = [nearest.page]
    return min(pages), max(pages)


def _greedy_sentence_chunks(
    full_text: str,
    sentence_spans: list[tuple[int, int]],
    lines: list[_Line],
    target_tokens: int,
    overlap_sentences: int,
) -> list[PdfChunk]:
    """Greedily pack sentences into ~``target_tokens`` chunks with overlap.

    Chunks always begin and end on sentence boundaries; the last
    ``overlap_sentences`` sentences of a flushed chunk seed the next one so
    adjacent chunks share context.
    """

    chunks: list[PdfChunk] = []
    current: list[tuple[int, int]] = []
    current_tokens = 0

    def flush() -> None:
        if not current:
            return
        char_start = current[0][0]
        char_end = current[-1][1]
        text = full_text[char_start:char_end]
        page_start, page_end = _page_range(lines, char_start, char_end)
        chunks.append(PdfChunk(text, page_start, page_end, char_start, char_end))

    for span in sentence_spans:
        span_tokens = _estimate_tokens(full_text[span[0] : span[1]])
        if current and current_tokens + span_tokens > target_tokens:
            flush()
            current = current[-overlap_sentences:] if overlap_sentences > 0 else []
            current_tokens = sum(
                _estimate_tokens(full_text[s:e]) for s, e in current
            )
            # Guarantee forward progress: if the carried-over overlap alone meets
            # the target, drop it rather than looping / duplicating content.
            if current_tokens >= target_tokens:
                current = []
                current_tokens = 0
        current.append(span)
        current_tokens += span_tokens

    flush()
    return chunks


def _split_span(
    full_text: str,
    span_start: int,
    span_end: int,
    lines: list[_Line],
    target_tokens: int,
    overlap_sentences: int,
) -> list[PdfChunk]:
    """Split a char span into sentence-boundary chunks with overlap."""

    sub = full_text[span_start:span_end]
    spans = [(span_start + s, span_start + e) for s, e in _sentence_spans(sub)]
    if not spans:
        return []
    return _greedy_sentence_chunks(
        full_text, spans, lines, target_tokens, overlap_sentences
    )


def _chunk_by_headings(
    full_text: str,
    lines: list[_Line],
    body_size: float,
    target_tokens: int,
    overlap_sentences: int,
) -> list[PdfChunk]:
    """Chunk by heading hierarchy: one section per heading, split if oversized."""

    sections: list[list[_Line]] = []
    current: list[_Line] = []
    for line in lines:
        if _is_heading(line, body_size) and current:
            sections.append(current)
            current = [line]
        else:
            current.append(line)
    if current:
        sections.append(current)

    chunks: list[PdfChunk] = []
    for section in sections:
        char_start = section[0].char_start
        char_end = section[-1].char_end
        text = full_text[char_start:char_end]
        if _estimate_tokens(text) <= target_tokens:
            page_start = min(line.page for line in section)
            page_end = max(line.page for line in section)
            chunks.append(PdfChunk(text, page_start, page_end, char_start, char_end))
        else:
            chunks.extend(
                _split_span(
                    full_text,
                    char_start,
                    char_end,
                    lines,
                    target_tokens,
                    overlap_sentences,
                )
            )
    return chunks


def chunk_pdf(
    data: bytes,
    *,
    target_tokens: int | None = None,
    overlap_sentences: int = _DEFAULT_OVERLAP_SENTENCES,
) -> list[PdfChunk]:
    """Chunk a PDF into structure-aware :class:`PdfChunk` objects.

    Args:
        data: Raw PDF bytes.
        target_tokens: Soft per-chunk token ceiling (defaults to the configured
            ``chunk_max_tokens``).
        overlap_sentences: Sentences of overlap between adjacent chunks when a
            section/document is split by size.

    Returns:
        Ordered chunks, each carrying its page and character span. Empty for a
        PDF with no extractable text.
    """

    target = target_tokens or get_settings().chunk_max_tokens

    doc = fitz.open(stream=data, filetype="pdf")
    try:
        full_text, lines = _extract_lines(doc)
    finally:
        doc.close()

    if not lines:
        logger.warning("PDF contained no extractable text")
        return []

    body_size = _body_font_size(lines)
    has_headings = any(_is_heading(line, body_size) for line in lines)

    if has_headings:
        chunks = _chunk_by_headings(
            full_text, lines, body_size, target, overlap_sentences
        )
        strategy = "headings"
    else:
        spans = _sentence_spans(full_text)
        chunks = _greedy_sentence_chunks(
            full_text, spans, lines, target, overlap_sentences
        )
        strategy = "paragraphs"

    chunks = [chunk for chunk in chunks if chunk.text.strip()]
    logger.info(
        "Chunked PDF (%d pages, strategy=%s) into %d chunks",
        max((line.page for line in lines), default=0),
        strategy,
        len(chunks),
    )
    return chunks
