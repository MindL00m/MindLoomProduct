"""Read PDFs accurately: reading order, tables, and per-page OCR.

Every part of MindLoom that reads a PDF (Files, Ask, chat-only files, uploads to
the knowledge graph, email attachments, templates and shipment documents) goes
through this module, so they all see the same text.

Per page:

- **Tables** become Markdown tables (``| a | b |``), so line items, quantities
  and prices stay in their rows and columns instead of being flattened into a
  stream of numbers. Ruled tables are found from their lines; borderless tables
  (common in generated invoices and packing lists) from words lined up in three
  or more columns over three or more rows. Table text is not repeated.
- **Reading order** follows the page: top to bottom, and for two-column layouts
  the left column before the right one (full-width headings stay in place).
- **Hyphenated line ends** are joined ("ship-\\nment" -> "shipment"); line breaks
  are otherwise kept, because addresses and invoice fields depend on them.
- **Scanned pages** (little or no text layer, or a full-page image with only a
  stamp of text) are flagged. :func:`read_pdf` sends just those pages to the
  vision model, one page per call, so a mixed PDF keeps its real text and gets
  its scanned pages read too.

Password-protected PDFs that need a password raise :class:`PdfReadError`.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)

# A page with fewer readable characters than this is treated as scanned.
MIN_PAGE_CHARS = 40
# A page mostly covered by images with little text is also treated as scanned.
IMAGE_COVERAGE = 0.5
IMAGE_PAGE_MAX_CHARS = 200
# Pages read by OCR per document, and how many run at once.
MAX_OCR_PAGES = 30
OCR_CONCURRENCY = 4
# Rendered page size for OCR: long side in pixels (the vision model's sweet spot).
OCR_LONG_SIDE_PX = 2000

# Join hyphenated line ends and split ligatures ("ﬁ" -> "fi") so words match searches.
_TEXT_FLAGS = (fitz.TEXTFLAGS_BLOCKS | fitz.TEXT_DEHYPHENATE) & ~fitz.TEXT_PRESERVE_LIGATURES


class PdfReadError(ValueError):
    """The PDF cannot be opened (damaged, or protected by a password)."""


@dataclass
class PdfPage:
    number: int  # 1-based
    text: str
    tables: int = 0
    needs_ocr: bool = False
    method: str = "text"  # "text", "ocr", or "empty"


@dataclass
class PdfRead:
    pages: list[PdfPage] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def needs_ocr(self) -> bool:
        return any(page.needs_ocr for page in self.pages)

    @property
    def used_ocr(self) -> bool:
        return any(page.method == "ocr" for page in self.pages)

    def text(self, *, page_markers: bool = True) -> str:
        parts = []
        for page in self.pages:
            body = page.text.strip()
            if not body:
                continue
            parts.append(f"[Page {page.number}]\n{body}" if page_markers else body)
        return "\n\n".join(parts)


# --- Layout -------------------------------------------------------------------------


@dataclass
class LayoutItem:
    """One piece of a page in reading order: a text block or a whole table."""

    bbox: tuple[float, float, float, float]
    kind: str  # "text" or "table"
    text: str
    block: dict[str, Any] | None = None  # the PyMuPDF "dict" block, for text items


def _cell(value: Any) -> str:
    return " ".join(str(value or "").split()).replace("|", "/")


def table_markdown(rows: list[list[Any]]) -> str:
    rows = [[_cell(c) for c in row] for row in rows if any(_cell(c) for c in row)]
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * width]
    lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(lines)


# Borderless tables: words further apart than this (points) are in different cells.
CELL_GAP = 9.0
MIN_TABLE_ROWS = 3
MIN_TABLE_COLUMNS = 3
MAX_AVG_CELL_CHARS = 40


def _visual_lines(words: list[tuple]) -> list[list[tuple]]:
    """Words grouped into lines by their vertical centre, each sorted left to right."""

    lines: list[list[tuple]] = []
    for word in sorted(words, key=lambda w: ((w[1] + w[3]) / 2, w[0])):
        centre = (word[1] + word[3]) / 2
        if lines:
            last = lines[-1]
            last_centre = sum((w[1] + w[3]) / 2 for w in last) / len(last)
            if abs(centre - last_centre) <= max(2.5, (word[3] - word[1]) * 0.35):
                last.append(word)
                continue
        lines.append([word])
    return [sorted(line, key=lambda w: w[0]) for line in lines]


def _cells(line: list[tuple]) -> list[tuple[float, float, str]]:
    cells: list[list[tuple]] = [[line[0]]]
    for word in line[1:]:
        if word[0] - cells[-1][-1][2] > CELL_GAP:
            cells.append([word])
        else:
            cells[-1].append(word)
    return [(c[0][0], c[-1][2], " ".join(w[4] for w in c)) for c in cells]


def _borderless_tables(
    page: "fitz.Page", taken: list[tuple[float, float, float, float]]
) -> list[tuple[tuple[float, float, float, float], str]]:
    words = [w for w in page.get_text("words", flags=_TEXT_FLAGS) if not any(_inside(w[:4], a) for a in taken)]
    if len(words) < MIN_TABLE_ROWS * MIN_TABLE_COLUMNS:
        return []
    lines = _visual_lines(words)
    rows = [(line, _cells(line)) for line in lines]
    found: list[tuple[tuple[float, float, float, float], str]] = []
    run: list[tuple[list[tuple], list[tuple[float, float, str]]]] = []

    def close_run() -> None:
        if len(run) >= MIN_TABLE_ROWS:
            table = _build_table(run)
            if table:
                found.append(table)
        run.clear()

    for line, cells in rows:
        tabular = len(cells) >= MIN_TABLE_COLUMNS or (run and len(cells) >= 2)
        if tabular and run:
            prev_line = run[-1][0]
            height = max(w[3] - w[1] for w in prev_line)
            gap = min(w[1] for w in line) - max(w[3] for w in prev_line)
            if gap > height * 1.8 or not _aligned(run, cells):
                close_run()
                tabular = len(cells) >= MIN_TABLE_COLUMNS
        if tabular:
            run.append((line, cells))
        else:
            close_run()
    close_run()
    return found


def _aligned(run: list, cells: list[tuple[float, float, str]]) -> bool:
    """At least two of a row's cells start or end where cells above do."""

    edges = [(c[0], c[1]) for _, row in run for c in row]
    hits = sum(
        any(abs(x0 - e0) <= 12 or abs(x1 - e1) <= 12 for e0, e1 in edges) for x0, x1, _ in cells
    )
    return hits >= 2


def _build_table(run: list) -> tuple[tuple[float, float, float, float], str] | None:
    spans = sorted((c[0], c[1]) for _, row in run for c in row)
    columns: list[list[float]] = []
    for x0, x1 in spans:
        if columns and x0 <= columns[-1][1] + 4:
            columns[-1][1] = max(columns[-1][1], x1)
        else:
            columns.append([x0, x1])
    if len(columns) < MIN_TABLE_COLUMNS:
        return None
    table_rows: list[list[str]] = []
    for _, row in run:
        out = [""] * len(columns)
        for x0, x1, text in row:
            index = max(
                range(len(columns)),
                key=lambda i: min(x1, columns[i][1]) - max(x0, columns[i][0]),
            )
            out[index] = f"{out[index]} {text}".strip()
        table_rows.append(out)
    filled = [cell for row in table_rows for cell in row if cell]
    if not filled or sum(map(len, filled)) / len(filled) > MAX_AVG_CELL_CHARS:
        return None  # columns of prose, not a table
    words = [w for line, _ in run for w in line]
    bbox = (
        min(w[0] for w in words), min(w[1] for w in words),
        max(w[2] for w in words), max(w[3] for w in words),
    )
    return bbox, table_markdown(table_rows)


def _tables(page: "fitz.Page") -> list[tuple[tuple[float, float, float, float], str]]:
    ruled = _ruled_tables(page)
    try:
        borderless = _borderless_tables(page, [area for area, _ in ruled])
    except Exception:  # noqa: BLE001 - detection is best effort
        logger.debug("Borderless table detection failed", exc_info=True)
        borderless = []
    return ruled + borderless


def _ruled_tables(page: "fitz.Page") -> list[tuple[tuple[float, float, float, float], str]]:
    try:
        found = page.find_tables()
    except Exception:  # noqa: BLE001 - a page that confuses table detection is read as text
        logger.debug("Table detection failed on page %s", page.number, exc_info=True)
        return []
    out = []
    for table in found.tables:
        rows = table.extract()
        if len(rows) < 2 or max((len(r) for r in rows), default=0) < 2:
            continue
        markdown = table_markdown(rows)
        if markdown:
            out.append((tuple(table.bbox), markdown))
    return out


def _inside(bbox: tuple[float, ...], area: tuple[float, ...], slack: float = 2.0) -> bool:
    x0, y0, x1, y1 = bbox
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    return area[0] - slack <= cx <= area[2] + slack and area[1] - slack <= cy <= area[3] + slack


# Ligature characters some PDFs still map to, spelled out so words match searches.
_LIGATURES = str.maketrans({
    "\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl", "\ufb03": "ffi", "\ufb04": "ffl",
    "\ufb05": "st", "\ufb06": "st",
})


def _block_text(block: dict[str, Any], skip: list[tuple[float, ...]] = ()) -> str:
    lines = []
    for line in block.get("lines", []):
        if any(_inside(tuple(line["bbox"]), area) for area in skip):
            continue  # this line is part of a table
        text = "".join(span.get("text", "") for span in line.get("spans", []))
        if text.strip():
            lines.append(text.rstrip())
    return "\n".join(lines).strip().translate(_LIGATURES)


def _column_order(items: list[LayoutItem], width: float) -> list[LayoutItem]:
    """Top-to-bottom order, reading a two-column region left column first."""

    items = sorted(items, key=lambda i: (round(i.bbox[1], 1), i.bbox[0]))
    mid = width / 2
    left = [i for i in items if i.bbox[2] <= mid + 5]
    right = [i for i in items if i.bbox[0] >= mid - 5]
    if len(left) < 2 or len(right) < 2:
        return items
    # A real two-column region: both columns overlap vertically.
    top = max(min(i.bbox[1] for i in left), min(i.bbox[1] for i in right))
    bottom = min(max(i.bbox[3] for i in left), max(i.bbox[3] for i in right))
    if bottom - top < 40:
        return items
    columns = set(map(id, left)) | set(map(id, right))
    spanning = [i for i in items if id(i) not in columns]
    before = [i for i in spanning if i.bbox[3] <= top + 2]
    after = [i for i in spanning if i not in before]
    in_band = lambda i: i.bbox[1] < bottom + 2  # noqa: E731
    left_in = [i for i in left if in_band(i)]
    right_in = [i for i in right if in_band(i)]
    rest = [i for i in left + right if not in_band(i)] + after
    return before + left_in + right_in + sorted(rest, key=lambda i: (i.bbox[1], i.bbox[0]))


def page_layout(page: "fitz.Page") -> list[LayoutItem]:
    """The page's text blocks and tables in reading order."""

    tables = _tables(page)
    data = page.get_text("dict", flags=_TEXT_FLAGS, sort=True)
    items: list[LayoutItem] = []
    areas = [area for area, _ in tables]
    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue
        bbox = tuple(block["bbox"])
        text = _block_text(block, areas)
        if text:
            items.append(LayoutItem(bbox=bbox, kind="text", text=text, block=block))
    items += [LayoutItem(bbox=area, kind="table", text=markdown) for area, markdown in tables]
    return _column_order(items, page.rect.width)


def _image_coverage(page: "fitz.Page") -> float:
    area = abs(page.rect) or 1.0
    covered = 0.0
    try:
        for info in page.get_image_info():
            covered += abs(fitz.Rect(info["bbox"]) & page.rect)
    except Exception:  # noqa: BLE001
        return 0.0
    return min(covered / area, 1.0)


def _read_page(page: "fitz.Page") -> PdfPage:
    layout = page_layout(page)
    text = "\n\n".join(item.text for item in layout).strip()
    readable = len("".join(text.split()))
    # Only a page that is mostly a picture is a scan; a short typed page is not.
    coverage = _image_coverage(page) if readable < IMAGE_PAGE_MAX_CHARS else 0.0
    scanned = coverage >= IMAGE_COVERAGE or (readable < MIN_PAGE_CHARS and coverage >= 0.15)
    return PdfPage(
        number=page.number + 1,
        text=text,
        tables=sum(1 for item in layout if item.kind == "table"),
        needs_ocr=scanned,
        method="text" if text else "empty",
    )


def open_pdf(data: bytes) -> "fitz.Document":
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:  # noqa: BLE001
        raise PdfReadError("That file is not a readable PDF.") from exc
    if doc.needs_pass and not doc.authenticate(""):
        doc.close()
        raise PdfReadError("This PDF is protected by a password; upload an unlocked copy.")
    return doc


def read_pdf_text(data: bytes) -> PdfRead:
    """Text of every page, with tables and reading order, without OCR."""

    with open_pdf(data) as doc:
        return PdfRead(pages=[_read_page(page) for page in doc])


# --- OCR ----------------------------------------------------------------------------


def render_page(data: bytes, number: int) -> bytes:
    """A page as PNG, sized so its long side is about ``OCR_LONG_SIDE_PX``."""

    with open_pdf(data) as doc:
        page = doc[number - 1]
        long_side = max(page.rect.width, page.rect.height) or 1
        zoom = min(max(OCR_LONG_SIDE_PX / long_side, 1.5), 4.0)
        return page.get_pixmap(matrix=fitz.Matrix(zoom, zoom)).tobytes("png")


def ocr_available() -> bool:
    from weaver.email_content import _ocr_enabled

    return _ocr_enabled()


async def _ocr_page(data: bytes, page: PdfPage, label: str) -> str:
    from weaver.email_content import ocr_images

    image = await asyncio.to_thread(render_page, data, page.number)
    return await ocr_images([image], label=f"{label}, page {page.number}")


async def read_pdf(
    data: bytes,
    *,
    label: str = "document.pdf",
    ocr: bool | None = None,
    max_ocr_pages: int = MAX_OCR_PAGES,
) -> PdfRead:
    """Text of every page; scanned pages are read with the vision model.

    ``ocr`` defaults to whether vision OCR is configured. A scanned page keeps
    any text layer it had when OCR finds nothing.
    """

    result = await asyncio.to_thread(read_pdf_text, data)
    scanned = [page for page in result.pages if page.needs_ocr]
    if not scanned:
        return result
    if not (ocr if ocr is not None else ocr_available()):
        result.notes.append(
            f"{len(scanned)} page(s) look scanned and were not read: vision OCR is off "
            "(set OPENAI_API_KEY and keep EMAIL_ATTACHMENT_OCR on)."
        )
        return result
    todo, skipped = scanned[:max_ocr_pages], scanned[max_ocr_pages:]
    gate = asyncio.Semaphore(OCR_CONCURRENCY)
    failed: list[int] = []

    async def run(page: PdfPage) -> None:
        async with gate:
            try:
                text = (await _ocr_page(data, page, label)).strip()
            except Exception:  # noqa: BLE001 - one failed page keeps its text layer
                logger.warning("OCR failed for %s page %s", label, page.number, exc_info=True)
                failed.append(page.number)
                return
        if text:
            # The OCR reads the whole page, including any small text layer (a stamp).
            page.text = text
            page.method = "ocr"

    await asyncio.gather(*(run(page) for page in todo))
    if failed:
        pages = ", ".join(str(n) for n in sorted(failed))
        result.notes.append(
            f"Scanned page(s) {pages} could not be read: the vision model request failed."
        )
    if skipped:
        result.notes.append(
            f"Only the first {max_ocr_pages} scanned pages were read; "
            f"{len(skipped)} more were skipped."
        )
    return result
