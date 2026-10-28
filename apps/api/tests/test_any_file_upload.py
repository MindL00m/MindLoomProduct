"""Any file type can be uploaded to Files; multi-file uploads rename duplicates."""

from __future__ import annotations

from loombot import files


def test_unique_name_keeps_any_extension() -> None:
    taken = {"Report.pdf", "archive.zip", "archive (2).zip", "README"}
    assert files.unique_name(taken, "archive.zip") == "archive (3).zip"
    assert files.unique_name(taken, "report.PDF") == "report (2).PDF"
    assert files.unique_name(taken, "README") == "README (2)"
    assert files.unique_name(taken, "new.dwg") == "new.dwg"


def test_other_kind_keeps_its_name_and_skips_validation() -> None:
    assert files.kind_from_name("drawing.dwg") is None
    assert files.normalize_name("drawing.dwg", files.OTHER_KIND) == "drawing.dwg"
    files.validate_binary(files.OTHER_KIND, b"\x00\x01binary")


def test_other_file_text_reads_text_but_not_binary() -> None:
    assert "hello" in files.other_file_text("page.html", b"<p>hello</p>")
    assert "binary file" in files.other_file_text("model.bin", b"\x00\x01\x02")


async def test_upload_of_unknown_type_is_stored_as_file(monkeypatch) -> None:
    captured: dict = {}

    async def fake_binary(org_id, user_id, name, kind, data, *, mime_type=None, **kwargs):
        captured.update(name=name, kind=kind, mime=mime_type)
        return {"file_id": "f", "name": name, "kind": kind}

    async def existing(org_id, user_id):
        return [{"name": "plan.dwg"}]

    monkeypatch.setattr(files, "create_binary_file", fake_binary)
    monkeypatch.setattr(files, "list_files", existing)
    await files.upload_file("o", "u", "plan.dwg", b"data", auto_rename=True)
    assert captured == {"name": "plan (2).dwg", "kind": "file", "mime": captured["mime"]}
    await files.upload_file("o", "u", "page.html", b"<p>x</p>")
    assert captured["kind"] == "file" and captured["mime"] == "text/html"


def test_download_of_other_files_is_never_inline() -> None:
    from api.routes.files import _download_response

    response = _download_response(
        b"<script>", {"kind": "file", "name": "x.html", "mime_type": "text/html"}, inline=True
    )
    assert response.media_type == "application/octet-stream"
    assert response.headers["content-disposition"].startswith("attachment")
