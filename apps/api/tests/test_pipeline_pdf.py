"""End-to-end test for PDF ingestion wired into the pipeline.

OpenAI/Postgres/Neo4j side effects are faked; the document layer uses the
in-memory repository and a real local blob store. Asserts a Document, its
Chunks, and page-spanning DERIVED_FROM edges are created, citations render, and
re-uploading is a no-op.
"""

from __future__ import annotations

from pathlib import Path

import fitz  # PyMuPDF
import pytest

import pipeline
from blob_storage import LocalBlobStorage
from documents import (
    InMemoryDocumentRepository,
    compute_content_hash,
    get_citation,
)
from models import ChunkMetadata
from pipeline import run_pdf_ingestion

ORG_ID = "org-test"


def _sample_pdf() -> bytes:
    doc = fitz.open()
    page1 = doc.new_page()
    page1.insert_text((72, 72), "Introduction", fontsize=20)
    page1.insert_text((72, 110), "The system ingests documents. It builds a graph.", fontsize=11)
    page2 = doc.new_page()
    page2.insert_text((72, 72), "Architecture", fontsize=20)
    page2.insert_text((72, 110), "Chunks link to documents. Citations cite pages.", fontsize=11)
    data = doc.tobytes()
    doc.close()
    return data


@pytest.fixture
def fake_side_effects(monkeypatch):
    saved: list[tuple] = []

    async def fake_extract(chunk):
        return ChunkMetadata(
            entities=[],
            knowledge_type="status_update",
            ownership=[],
            confidence="high",
            confidence_reason="test",
            summary="a short summary",
        )

    async def fake_embed(chunk, metadata):
        return [0.0]

    async def fake_save_pg(chunk, metadata, embedding, org_id, visible_to=None):
        return None

    async def fake_save_neo(chunk, metadata, org_id, *, source, source_label, visible_to):
        saved.append((chunk, source, source_label, visible_to))

    monkeypatch.setattr(pipeline, "extract_chunk_metadata", fake_extract)
    monkeypatch.setattr(pipeline, "embed_chunk", fake_embed)
    monkeypatch.setattr(pipeline, "save_to_postgres", fake_save_pg)
    monkeypatch.setattr(pipeline, "save_to_neo4j", fake_save_neo)
    return saved


async def test_pdf_ingestion_creates_document_chunks_and_page_citations(
    tmp_path: Path, fake_side_effects
):
    saved = fake_side_effects
    repo = InMemoryDocumentRepository()
    storage = LocalBlobStorage(tmp_path)
    data = _sample_pdf()

    result = await run_pdf_ingestion(
        data,
        ORG_ID,
        source_label="report.pdf",
        original_filename="report.pdf",
        visible_to=["engineering"],
        repository=repo,
        storage=storage,
    )

    doc = await repo.find_by_content_hash(ORG_ID, compute_content_hash(data))
    assert doc is not None
    assert doc.source == "pdf"
    assert await storage.exists(doc.storage_path)
    assert await storage.get(doc.storage_path) == data

    # Chunks created and all linked.
    assert result.total_chunks >= 2
    assert result.total_messages == 0
    assert len(saved) == result.total_chunks
    assert await repo.count_chunks_for_document(doc.document_id) == result.total_chunks

    # Each chunk inherits visibility and carries a page-based citation.
    seen_pages = set()
    for chunk, source, _label, visible_to in saved:
        assert source == "pdf"
        assert visible_to == ["engineering"]
        citation = await get_citation(chunk.chunk_id, org_id=ORG_ID, repository=repo)
        assert citation.page_start is not None and citation.page_end is not None
        assert citation.page_start <= citation.page_end
        assert "report.pdf" in citation.render()
        assert "page" in citation.render()
        seen_pages.add((citation.page_start, citation.page_end))

    # Content spanned both pages of the document.
    assert max(pe for _ps, pe in seen_pages) == 2


async def test_pdf_reupload_is_noop(tmp_path: Path, fake_side_effects):
    saved = fake_side_effects
    repo = InMemoryDocumentRepository()
    storage = LocalBlobStorage(tmp_path)
    data = _sample_pdf()

    first = await run_pdf_ingestion(
        data, ORG_ID, source_label="report.pdf", original_filename="report.pdf",
        repository=repo, storage=storage,
    )
    saved_after_first = len(saved)
    doc = await repo.find_by_content_hash(ORG_ID, compute_content_hash(data))
    assert doc is not None

    second = await run_pdf_ingestion(
        data, ORG_ID, source_label="report.pdf", original_filename="report.pdf",
        repository=repo, storage=storage,
    )

    assert sum(1 for p in tmp_path.rglob("*") if p.is_file()) == 1  # deduped
    assert second.total_chunks == first.total_chunks  # reports existing count
    assert len(saved) == saved_after_first  # no chunk reprocessed
