"""Tests for per-org document isolation in the in-memory repository."""

from __future__ import annotations

from pathlib import Path

import pytest

from blob_storage import LocalBlobStorage
from documents import (
    InMemoryDocumentRepository,
    compute_content_hash,
    get_citation,
    link_chunk_to_document,
    store_document,
)
from models import DerivedFrom

ORG_A = "org-a"
ORG_B = "org-b"


async def test_same_content_hash_isolated_per_org(tmp_path: Path) -> None:
    """Identical bytes in two orgs create separate documents."""

    repo = InMemoryDocumentRepository()
    storage = LocalBlobStorage(tmp_path)
    data = b"shared payload"

    doc_a = await store_document(
        org_id=ORG_A,
        data=data,
        source="pdf",
        source_label="Report A",
        mime_type="application/pdf",
        original_filename="a.pdf",
        repository=repo,
        storage=storage,
    )
    doc_b = await store_document(
        org_id=ORG_B,
        data=data,
        source="pdf",
        source_label="Report B",
        mime_type="application/pdf",
        original_filename="b.pdf",
        repository=repo,
        storage=storage,
    )

    assert doc_a.document.document_id != doc_b.document.document_id
    assert doc_a.document.org_id == ORG_A
    assert doc_b.document.org_id == ORG_B
    assert await repo.find_by_content_hash(ORG_A, compute_content_hash(data))
    assert await repo.find_by_content_hash(ORG_B, compute_content_hash(data))


async def test_citation_scoped_to_org(tmp_path: Path) -> None:
    """A citation lookup with the wrong org_id returns nothing."""

    repo = InMemoryDocumentRepository()
    storage = LocalBlobStorage(tmp_path)
    stored = await store_document(
        org_id=ORG_A,
        data=b"doc",
        source="email",
        source_label="Thread",
        mime_type="text/plain",
        repository=repo,
        storage=storage,
    )
    await link_chunk_to_document(
        "chunk-1",
        stored.document.document_id,
        DerivedFrom(char_start=0, char_end=3),
        org_id=ORG_A,
        repository=repo,
    )

    ok = await get_citation("chunk-1", org_id=ORG_A, repository=repo)
    assert ok.document_id == stored.document.document_id

    from documents import CitationNotFoundError

    with pytest.raises(CitationNotFoundError):
        await get_citation("chunk-1", org_id=ORG_B, repository=repo)
