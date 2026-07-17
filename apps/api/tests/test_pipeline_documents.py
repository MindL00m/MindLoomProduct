"""End-to-end tests for document provenance wired into the ingestion pipeline.

For each source family these run the real pipeline orchestration with the
OpenAI/Postgres/Neo4j *side effects* faked (monkeypatched) and the document
layer backed by the in-memory repository + a real local blob store. They assert
that a Document, its Chunks, and the DERIVED_FROM edges are all created with
source-appropriate offsets, and that ``get_citation`` renders sensibly.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import pipeline
from blob_storage import LocalBlobStorage
from documents import (
    InMemoryDocumentRepository,
    compute_chunk_locators,
    compute_content_hash,
    get_citation,
)
from models import ChunkMetadata, Conversation, IncomingMessage, Participant
from pipeline import DocumentInput, run_ingestion

ORG_ID = "org-test"

BASE = datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)


def _conversation(source: str) -> Conversation:
    """Two message groups separated by a >30min gap => deterministically 2 chunks.

    Bodies are lower-case so the chunker's capitalised-entity topic-shift rule
    never introduces extra (non-deterministic) splits.
    """

    participants = [Participant(id="p1", name="ada"), Participant(id="p2", name="bran")]
    messages = [
        IncomingMessage(id="m1", sender="p1", timestamp=BASE, text="hello there team"),
        IncomingMessage(
            id="m2", sender="p2", timestamp=BASE + timedelta(minutes=1), text="all good here"
        ),
        IncomingMessage(
            id="m3", sender="p1", timestamp=BASE + timedelta(minutes=60), text="lunch plans today"
        ),
        IncomingMessage(
            id="m4", sender="p2", timestamp=BASE + timedelta(minutes=61), text="sounds great"
        ),
    ]
    return Conversation(
        source=source,
        conversation_id=f"conv-{source}",
        title=f"{source} sample",
        participants=participants,
        messages=messages,
    )


@pytest.fixture
def fake_side_effects(monkeypatch):
    """Fake the OpenAI + DB side effects; collect the chunks saved to "Neo4j"."""

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


# (source, sample file bytes, filename, expected locator kind)
SOURCE_CASES = [
    ("whatsapp_export", b"chat export sample bytes", "chat.txt", "char"),
    ("email", b"From: ada\nSubject: hi\n\nbody", "thread.eml", "char"),
    ("excel", b"col_a,col_b\n1,2\n3,4\n", "data.xlsx", "row"),
    ("pptx", b"PK\x03\x04 fake slides", "deck.pptx", "page"),
]


@pytest.mark.parametrize("source,data,filename,kind", SOURCE_CASES)
async def test_pipeline_creates_document_chunks_and_citations(
    source, data, filename, kind, tmp_path: Path, fake_side_effects
):
    saved = fake_side_effects
    repo = InMemoryDocumentRepository()
    storage = LocalBlobStorage(tmp_path)
    conversation = _conversation(source)
    document = DocumentInput(
        data=data,
        source=source,
        source_label=f"{source} sample",
        original_filename=filename,
        mime_type="application/octet-stream",
        visible_to=["engineering"],
    )

    result = await run_ingestion(
        conversation, ORG_ID, document=document, repository=repo, storage=storage
    )

    stored_doc = await repo.find_by_content_hash(ORG_ID, compute_content_hash(data))
    assert stored_doc is not None
    assert stored_doc.original_filename == filename
    assert await storage.exists(stored_doc.storage_path)
    assert await storage.get(stored_doc.storage_path) == data
    # Exactly one blob on disk.
    assert sum(1 for p in tmp_path.rglob("*") if p.is_file()) == 1

    # --- Chunks created and every one linked via DERIVED_FROM. ---
    assert result.total_chunks == 2
    assert len(saved) == 2
    assert await repo.count_chunks_for_document(stored_doc.document_id) == 2

    # Chunks inherit the document's visibility.
    for _chunk, _src, _label, visible_to in saved:
        assert visible_to == ["engineering"]

    # --- Correct, source-appropriate offsets on each citation. ---
    chunks_in_order = sorted((c for c, *_ in saved), key=lambda c: c.start_time)
    expected = compute_chunk_locators(chunks_in_order, source)

    for chunk, exp in zip(chunks_in_order, expected):
        citation = await get_citation(chunk.chunk_id, org_id=ORG_ID, repository=repo)
        assert citation.document_id == stored_doc.document_id
        rendered = citation.render()
        assert "engineering" not in rendered  # visibility is not leaked into citations
        assert f"{source} sample" in rendered

        if kind == "char":
            assert citation.char_start == exp.char_start
            assert citation.char_end == exp.char_end
            assert citation.char_end - citation.char_start == len(chunk.raw_text)
            assert f"chars {exp.char_start}-{exp.char_end}" in rendered
        elif kind == "row":
            assert tuple(citation.row_range) == exp.row_range
            assert f"rows {exp.row_range[0]}-{exp.row_range[1]}" in rendered
        else:  # page
            assert citation.page_number == exp.page_number
            assert f"page {exp.page_number}" in rendered

    # char offsets must tile the document's extracted text with no gaps.
    if kind == "char":
        spans = []
        for chunk in chunks_in_order:
            citation = await get_citation(chunk.chunk_id, org_id=ORG_ID, repository=repo)
            spans.append((citation.char_start, citation.char_end))
        spans.sort()
        assert spans[0][0] == 0
        for (_s1, e1), (s2, _e2) in zip(spans, spans[1:]):
            assert s2 == e1 + 1  # consecutive, separated by the joining newline


async def test_reupload_is_noop_and_does_not_duplicate_chunks(
    tmp_path: Path, fake_side_effects
):
    """Re-running the same document dedups storage and skips re-chunking."""

    saved = fake_side_effects
    repo = InMemoryDocumentRepository()
    storage = LocalBlobStorage(tmp_path)
    data = b"identical file contents"
    document = DocumentInput(
        data=data,
        source="whatsapp_export",
        source_label="dup sample",
        original_filename="dup.txt",
    )

    first = await run_ingestion(
        _conversation("whatsapp_export"),
        ORG_ID,
        document=document,
        repository=repo,
        storage=storage,
    )
    assert first.total_chunks == 2
    doc = await repo.find_by_content_hash(ORG_ID, compute_content_hash(data))
    assert doc is not None
    links_after_first = await repo.count_chunks_for_document(doc.document_id)
    saved_after_first = len(saved)

    # Re-upload the exact same document.
    second = await run_ingestion(
        _conversation("whatsapp_export"),
        ORG_ID,
        document=document,
        repository=repo,
        storage=storage,
    )

    # Storage de-duped (one blob), chunking skipped (no new chunk processing).
    assert sum(1 for p in tmp_path.rglob("*") if p.is_file()) == 1
    assert second.total_chunks == links_after_first  # reports existing count
    assert await repo.count_chunks_for_document(doc.document_id) == links_after_first
    assert len(saved) == saved_after_first  # no chunk was processed again


async def test_attach_citations_enriches_answer_sources(tmp_path: Path):
    """answerer._attach_citations populates ChunkResult.citation from the graph."""

    from datetime import datetime as _dt

    from answerer import _attach_citations
    from documents import link_chunk_to_document, store_document
    from models import ChunkResult, DerivedFrom

    repo = InMemoryDocumentRepository()
    storage = LocalBlobStorage(tmp_path)
    stored = await store_document(
        org_id=ORG_ID,
        data=b"slide deck bytes",
        source="pptx",
        source_label="Q3 Board Deck",
        mime_type="application/vnd.ms-powerpoint",
        original_filename="q3.pptx",
        repository=repo,
        storage=storage,
    )
    await link_chunk_to_document(
        "chunk-A",
        stored.document.document_id,
        DerivedFrom(page_number=2),
        org_id=ORG_ID,
        repository=repo,
    )

    def _chunk(chunk_id: str) -> ChunkResult:
        return ChunkResult(
            chunk_id=chunk_id,
            raw_text="...",
            summary="...",
            speakers=["ada"],
            start_time=_dt.now(timezone.utc),
            end_time=_dt.now(timezone.utc),
            knowledge_type="status_update",
            confidence="high",
            similarity_score=0.5,
        )

    linked = _chunk("chunk-A")
    unlinked = _chunk("chunk-B")
    await _attach_citations([linked, unlinked], ORG_ID, repository=repo)

    assert linked.citation is not None
    assert linked.citation.render() == "Source: Q3 Board Deck, q3.pptx, page 2"
    assert unlinked.citation is None
