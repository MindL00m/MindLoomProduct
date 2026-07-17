"""Tests for the document storage layer: dedup-on-rehash and citations.

These exercise the public API (``store_document``, ``link_chunk_to_document``,
``get_citation``) against the in-memory repository and the real local blob
backend, so they need no running database.
"""

from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone

import pytest

from blob_storage import LocalBlobStorage
from documents import (
    CitationNotFoundError,
    InMemoryDocumentRepository,
    compute_content_hash,
    get_citation,
    link_chunk_to_document,
    store_document,
)
from models import DerivedFrom

ORG_ID = "org-test"

PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _count_files(root: Path) -> int:
    return sum(1 for p in root.rglob("*") if p.is_file())


async def test_store_document_dedup_on_rehash(tmp_path: Path) -> None:
    """Re-uploading identical bytes reuses the document and stores one blob."""

    repo = InMemoryDocumentRepository()
    storage = LocalBlobStorage(tmp_path)
    data = b"quarterly numbers,42\nmore numbers,43\n"

    first = await store_document(
        org_id=ORG_ID,
        data=data,
        source="excel",
        source_label="Q3 Financials",
        mime_type=XLSX_MIME,
        original_filename="q3.xlsx",
        repository=repo,
        storage=storage,
    )

    second = await store_document(
        org_id=ORG_ID,
        data=data,
        source="excel",
        source_label="Totally Different Label",
        mime_type=XLSX_MIME,
        original_filename="renamed.xlsx",
        repository=repo,
        storage=storage,
    )

    assert first.deduped is False
    assert second.deduped is True
    # Same content => same document id, same hash, and the first upload's metadata
    # is preserved (the re-upload is a no-op).
    assert second.document.document_id == first.document.document_id
    assert second.document.content_hash == first.document.content_hash
    assert second.document.content_hash == compute_content_hash(data)
    assert second.document.source_label == "Q3 Financials"

    # The blob was written exactly once.
    assert _count_files(tmp_path) == 1
    # And it round-trips back to the original bytes.
    assert await storage.get(first.document.storage_path) == data


async def test_distinct_content_creates_new_document(tmp_path: Path) -> None:
    """Different bytes produce a new (non-deduped) document and a second blob."""

    repo = InMemoryDocumentRepository()
    storage = LocalBlobStorage(tmp_path)

    a = await store_document(
        org_id=ORG_ID,
        data=b"alpha",
        source="email",
        source_label="Email A",
        mime_type="text/plain",
        original_filename="a.eml",
        repository=repo,
        storage=storage,
    )
    b = await store_document(
        org_id=ORG_ID,
        data=b"beta",
        source="email",
        source_label="Email B",
        mime_type="text/plain",
        original_filename="b.eml",
        repository=repo,
        storage=storage,
    )

    assert b.deduped is False
    assert a.document.document_id != b.document.document_id
    assert _count_files(tmp_path) == 2


async def test_get_citation_for_pptx_chunk(tmp_path: Path) -> None:
    """A chunk derived from a pptx slide yields a page-based citation."""

    repo = InMemoryDocumentRepository()
    storage = LocalBlobStorage(tmp_path)

    # PPTX files are zip containers; the magic bytes are enough for this test.
    pptx_bytes = b"PK\x03\x04 fake pptx payload for Q3 board deck"
    stored = await store_document(
        org_id=ORG_ID,
        data=pptx_bytes,
        source="powerpoint",
        source_label="Q3 Board Deck",
        mime_type=PPTX_MIME,
        original_filename="q3_board_deck.pptx",
        repository=repo,
        storage=storage,
    )

    chunk_id = "chunk-pptx-1"
    await link_chunk_to_document(
        chunk_id,
        stored.document.document_id,
        DerivedFrom(page_number=4),
        org_id=ORG_ID,
        repository=repo,
    )

    citation = await get_citation(chunk_id, org_id=ORG_ID, repository=repo)

    assert citation.chunk_id == chunk_id
    assert citation.document_id == stored.document.document_id
    assert citation.source == "powerpoint"
    assert citation.original_filename == "q3_board_deck.pptx"
    assert citation.page_number == 4
    assert citation.locator() == "page 4"
    assert citation.render() == "Source: Q3 Board Deck, q3_board_deck.pptx, page 4"


async def test_document_retains_provenance_metadata(tmp_path: Path) -> None:
    repo = InMemoryDocumentRepository()
    storage = LocalBlobStorage(tmp_path)
    updated = datetime(2026, 7, 1, tzinfo=timezone.utc)
    stored = await store_document(
        org_id=ORG_ID,
        data=b"approved safety procedure",
        source="sharepoint",
        source_label="Safety Procedure",
        mime_type="text/plain",
        original_filename="safety.txt",
        title="Safety Procedure",
        author="owner@example.com",
        owners=["owner@example.com"],
        source_updated_at=updated,
        source_application="Microsoft Word",
        source_location="Operations / Policies",
        department="Operations",
        project="Plant safety",
        folder_path="/Policies/Safety",
        version="v7",
        contributors=["owner@example.com", "editor@example.com"],
        permissions=["domain:example.com"],
        source_url="https://example.sharepoint.com/safety",
        repository=repo,
        storage=storage,
    )
    await link_chunk_to_document(
        "chunk-provenance",
        stored.document.document_id,
        org_id=ORG_ID,
        repository=repo,
    )
    citation = await get_citation(
        "chunk-provenance", org_id=ORG_ID, repository=repo
    )

    assert stored.document.department == "Operations"
    assert stored.document.contributors == [
        "owner@example.com",
        "editor@example.com",
    ]
    assert citation.source_url == "https://example.sharepoint.com/safety"
    assert citation.author == "owner@example.com"
    assert citation.source_updated_at == updated
    assert citation.version == "v7"


async def test_get_citation_char_offsets_render(tmp_path: Path) -> None:
    """Char-offset locators render as a chars range."""

    repo = InMemoryDocumentRepository()
    storage = LocalBlobStorage(tmp_path)

    stored = await store_document(
        org_id=ORG_ID,
        data=b"a long email body about the migration plan",
        source="email",
        source_label="Migration Thread",
        mime_type="text/plain",
        original_filename="migration.eml",
        repository=repo,
        storage=storage,
    )
    await link_chunk_to_document(
        "chunk-email-1",
        stored.document.document_id,
        DerivedFrom(char_start=10, char_end=42),
        org_id=ORG_ID,
        repository=repo,
    )

    citation = await get_citation("chunk-email-1", org_id=ORG_ID, repository=repo)
    assert citation.locator() == "chars 10-42"
    assert citation.render() == (
        "Source: Migration Thread, migration.eml, chars 10-42"
    )


async def test_get_citation_missing_raises() -> None:
    """A chunk with no source document raises CitationNotFoundError."""

    repo = InMemoryDocumentRepository()
    with pytest.raises(CitationNotFoundError):
        await get_citation("does-not-exist", org_id=ORG_ID, repository=repo)
