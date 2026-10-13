"""Document upload visibility defaults to the uploader."""

from types import SimpleNamespace

import pytest

from brain import document_ingestion
from core.models import DocumentMetadataInput


@pytest.mark.asyncio
async def test_document_ingestion_defaults_to_private_uploader(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_run_ingestion(conversation, org_id, *, document=None, **_kwargs):
        captured["visible_to"] = document.visible_to
        captured["permissions"] = document.permissions
        return SimpleNamespace(
            total_messages=1,
            total_chunks=1,
            chunks_by_type={},
            failed_chunks=0,
            duration_seconds=0.1,
        )

    monkeypatch.setattr(document_ingestion, "run_ingestion", fake_run_ingestion)
    monkeypatch.setattr(
        document_ingestion,
        "extract_file_text",
        lambda *_args, **_kwargs: "Hello world document body",
    )

    await document_ingestion.ingest_document(
        data=b"hello",
        filename="notes.txt",
        mime_type="text/plain",
        org_id="org-1",
        uploaded_by="user-42",
        metadata=DocumentMetadataInput(title="Notes"),
    )
    assert captured["visible_to"] == ["user:user-42"]
    assert captured["permissions"] == ["user:user-42"]


@pytest.mark.asyncio
async def test_document_ingestion_honours_organization_visibility(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_run_ingestion(conversation, org_id, *, document=None, **_kwargs):
        captured["visible_to"] = document.visible_to
        return SimpleNamespace(
            total_messages=1,
            total_chunks=1,
            chunks_by_type={},
            failed_chunks=0,
            duration_seconds=0.1,
        )

    monkeypatch.setattr(document_ingestion, "run_ingestion", fake_run_ingestion)
    monkeypatch.setattr(
        document_ingestion,
        "extract_file_text",
        lambda *_args, **_kwargs: "Hello world document body",
    )

    await document_ingestion.ingest_document(
        data=b"hello",
        filename="notes.txt",
        mime_type="text/plain",
        org_id="org-1",
        uploaded_by="user-42",
        metadata=DocumentMetadataInput(
            title="Notes",
            visibility="organization",
            permissions=["ignored@example.com"],
        ),
    )
    assert captured["visible_to"] == ["org:org-1"]
