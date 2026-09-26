"""Extract plain text from uploaded files for chat-only (ephemeral) context."""

from __future__ import annotations

import json
import logging
import re
import zipfile
import csv
from io import BytesIO
from xml.etree import ElementTree

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


def _extract_office_xml(data: bytes, prefixes: tuple[str, ...]) -> str:
    """Extract text nodes from OOXML Word, PowerPoint, and Excel packages."""

    parts: list[str] = []
    with zipfile.ZipFile(BytesIO(data)) as archive:
        names = sorted(
            name for name in archive.namelist() if name.startswith(prefixes) and name.endswith(".xml")
        )
        for name in names:
            try:
                root = ElementTree.fromstring(archive.read(name))
            except ElementTree.ParseError:
                continue
            text = " ".join(value.strip() for value in root.itertext() if value.strip())
            if text:
                parts.append(re.sub(r"\s+", " ", text))
    return _truncate("\n\n".join(parts))


def _extract_docx(data: bytes) -> str:
    """Preserve Word headings and table-cell addresses in extracted text."""

    with zipfile.ZipFile(BytesIO(data)) as archive:
        root = ElementTree.fromstring(archive.read("word/document.xml"))
    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    lines: list[str] = []
    table_number = 0
    for child in root.findall(".//w:body/*", namespace):
        if child.tag.endswith("}p"):
            value = " ".join(text.strip() for text in child.itertext() if text.strip())
            style = child.find("./w:pPr/w:pStyle", namespace)
            style_name = str(style.attrib.get(f"{{{namespace['w']}}}val", "")) if style is not None else ""
            if value:
                lines.append(f"[Section: {value}]" if style_name.lower().startswith("heading") else value)
        elif child.tag.endswith("}tbl"):
            table_number += 1
            for row_number, row in enumerate(child.findall("./w:tr", namespace), start=1):
                for column_number, cell in enumerate(row.findall("./w:tc", namespace), start=1):
                    value = " ".join(text.strip() for text in cell.itertext() if text.strip())
                    if value:
                        lines.append(f"[Table {table_number} cell R{row_number}C{column_number}] {value}")
    if not lines:
        # Tolerate simplified/non-standard OOXML namespaces used by exporters
        # and fixtures while keeping structured extraction for normal Word files.
        return _extract_office_xml(
            data, ("word/document.xml", "word/header", "word/footer")
        )
    return _truncate("\n".join(lines))


def _extract_xlsx(data: bytes) -> str:
    rows: list[str] = []
    with zipfile.ZipFile(BytesIO(data)) as archive:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
            shared = [
                " ".join(text.strip() for text in item.itertext() if text.strip())
                for item in root
            ]
        for name in sorted(
            item
            for item in archive.namelist()
            if item.startswith("xl/worksheets/sheet") and item.endswith(".xml")
        ):
            root = ElementTree.fromstring(archive.read(name))
            sheet_name = name.rsplit("/", 1)[-1].removesuffix(".xml")
            rows.append(f"[Sheet: {sheet_name}]")
            for row in root.iter():
                if not row.tag.endswith("}row"):
                    continue
                values: list[str] = []
                for cell in row:
                    if not cell.tag.endswith("}c"):
                        continue
                    value = next(
                        (node.text or "" for node in cell if node.tag.endswith("}v")),
                        "",
                    )
                    if cell.attrib.get("t") == "s" and value.isdigit():
                        index = int(value)
                        value = shared[index] if index < len(shared) else value
                    reference = str(cell.attrib.get("r") or "")
                    values.append(f"[{reference}] {value}" if reference else value)
                if any(values):
                    rows.append(",".join(values))
    return _truncate("\n".join(rows))


def _extract_csv(data: bytes) -> str:
    decoded = data.decode("utf-8-sig", errors="replace")
    rows = csv.reader(decoded.splitlines())
    return _truncate("\n".join(",".join(cell.strip() for cell in row) for row in rows))


def _extract_jsonl(data: bytes) -> str:
    lines: list[str] = []
    for raw in data.decode("utf-8", errors="replace").splitlines():
        if not raw.strip():
            continue
        try:
            lines.append(json.dumps(json.loads(raw), ensure_ascii=False))
        except json.JSONDecodeError:
            lines.append(raw)
    return _truncate("\n".join(lines))


def extract_file_text(filename: str, data: bytes) -> str:
    """Return extracted text for PDF, JSON (incl. conversations), or plain text."""

    name = (filename or "upload").lower()
    if name.endswith(".pdf") or data[:4] == b"%PDF":
        return _extract_pdf(data)
    if name.endswith(".json"):
        return _extract_json(data)
    if name.endswith(".jsonl"):
        return _extract_jsonl(data)
    if name.endswith(".csv"):
        return _extract_csv(data)
    if name.endswith(".docx"):
        return _extract_docx(data)
    if name.endswith(".pptx"):
        return _extract_office_xml(data, ("ppt/slides/", "ppt/notesSlides/"))
    if name.endswith(".xlsx"):
        return _extract_xlsx(data)
    return _truncate(data.decode("utf-8", errors="replace"))
