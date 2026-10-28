"""ZIP archives: safe extraction, Files unpacking, ingestion and email attachments."""

from __future__ import annotations

import io
import zipfile

import pytest

from brain import archives
from loombot import files


def _zip(entries: dict[str, bytes], *, compression=zipfile.ZIP_DEFLATED) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression) as archive:
        for path, data in entries.items():
            archive.writestr(path, data)
    return buffer.getvalue()


def test_read_archive_skips_folders_hidden_and_mac_files() -> None:
    data = _zip({
        "docs/invoice.txt": b"Invoice 42",
        "docs/.secret": b"x",
        "__MACOSX/docs/._invoice.txt": b"x",
        "notes.md": b"# Notes",
    })
    contents = archives.read_archive(data)
    assert [(e.path, e.name) for e in contents.entries] == [
        ("docs/invoice.txt", "invoice.txt"), ("notes.md", "notes.md"),
    ]


def test_read_archive_filters_and_limits(monkeypatch) -> None:
    data = _zip({"a.txt": b"a", "b.exe": b"b", "c.txt": b"c", "d.txt": b"d"})
    monkeypatch.setattr(archives, "MAX_ENTRIES", 2)
    contents = archives.read_archive(data, accept=lambda p: p.endswith(".txt"))
    assert [e.name for e in contents.entries] == ["a.txt", "c.txt"]
    reasons = {s["path"]: s["reason"] for s in contents.skipped}
    assert "not supported" in reasons["b.exe"] and "More than 2" in reasons["d.txt"]


def test_zip_bombs_are_refused() -> None:
    data = _zip({"bomb.txt": b"0" * 5_000_000})
    contents = archives.read_archive(data)
    assert contents.entries == []
    assert contents.skipped[0]["reason"] == "Suspiciously compressed."


def test_not_a_zip() -> None:
    with pytest.raises(archives.ArchiveError):
        archives.read_archive(b"not a zip")


def test_office_files_are_not_treated_as_archives() -> None:
    assert archives.is_zip("bundle.zip")
    assert not archives.is_zip("report.docx", b"PK\x03\x04rest")


def test_zip_text_reads_documents_inside() -> None:
    data = _zip({"q/prices.csv": b"product,price\nSOP,520\n", "logo.png": b"\x89PNG"})
    text = files.zip_text("bundle.zip", data)
    assert "[q/prices.csv]" in text and "SOP" in text
    assert "logo.png" in text  # reported as not read


async def test_extract_zip_to_files_creates_each_file(monkeypatch) -> None:
    data = _zip({"a/brief.txt": b"hello", "b/brief.txt": b"again", "sheet.csv": b"x,y\n1,2\n"})

    async def fake_bytes(org_id, user_id, file_id):
        return data, {"name": "bundle.zip"}

    created: list = []

    async def fake_upload(org_id, user_id, filename, blob, name=None, *, auto_rename=False):
        assert auto_rename
        created.append(filename)
        return {"file_id": str(len(created)), "name": filename, "content": "x"}

    monkeypatch.setattr(files, "get_file_bytes", fake_bytes)
    monkeypatch.setattr(files, "upload_file", fake_upload)
    result = await files.extract_zip_to_files("o", "u", "f")
    assert created == ["brief.txt", "brief.txt", "sheet.csv"]
    assert result["archive"] == "bundle.zip" and "content" not in result["created"][0]


async def test_email_zip_attachments_are_read() -> None:
    from weaver.email_content import extract_attachment

    data = _zip({"contract.txt": b"Rate 520 USD per MT"})
    result = await extract_attachment("docs.zip", "application/zip", data, allow_ocr=False)
    assert "Rate 520 USD per MT" in result.text


async def test_ingesting_a_zip_queues_each_document(monkeypatch) -> None:
    import main
    from core.models import DocumentMetadataInput

    queued: list = []

    async def fake_enqueue(kind, *, org_id, conversation_id, payload):
        queued.append(payload)
        return f"job-{len(queued)}"

    monkeypatch.setattr(main, "enqueue", fake_enqueue)
    data = _zip({"shipment/BL.pdf": b"%PDF-1.4", "contract.docx": b"PK", "setup.exe": b"x"})
    out = await main._ingest_archive(
        "docs.zip", data, "o", "u", DocumentMetadataInput(visibility="private")
    )
    assert out["job_ids"] == ["job-1", "job-2"]
    assert [p["filename"] for p in queued] == ["BL.pdf", "contract.docx"]
    assert queued[0]["metadata"]["folder_path"] == "docs.zip/shipment"
    assert queued[0]["metadata"]["visibility"] == "private"
    assert out["skipped"][0]["path"] == "setup.exe"
