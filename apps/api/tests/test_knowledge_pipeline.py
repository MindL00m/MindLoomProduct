from datetime import datetime, timezone

import pytest

import answerer
from answerer import _build_context, generate_answer
from models import (
    ChunkResult,
    Citation,
    RetrievalResult,
    TypedEntity,
    ChunkMetadata,
)
from retrieval import _rerank_chunks


def _chunk(chunk_id: str, score: float, *, graph: float = 0.0) -> ChunkResult:
    now = datetime(2026, 7, 1, tzinfo=timezone.utc)
    return ChunkResult(
        chunk_id=chunk_id,
        raw_text=f"Evidence from {chunk_id}",
        summary="Evidence",
        speakers=[],
        start_time=now,
        end_time=now,
        knowledge_type="decision",
        confidence="high",
        similarity_score=score,
        freshness_score=0.9,
        authority_score=0.9,
        graph_score=graph,
        retrieval_score=score,
    )


def test_typed_metadata_contract_supports_decisions_and_claims():
    metadata = ChunkMetadata(
        entities=["Billing"],
        typed_entities=[
            TypedEntity(name="Billing", type="system", relevance="primary")
        ],
        knowledge_type="decision",
        ownership=[],
        confidence="high",
        confidence_reason="Explicitly stated",
        summary="The team selected annual billing.",
        decisions=["Use annual billing."],
        action_items=["Update the pricing page."],
        factual_claims=["Annual billing begins in August."],
    )
    assert metadata.typed_entities[0].type == "system"
    assert metadata.decisions == ["Use annual billing."]


def test_graph_overlap_can_rerank_and_duplicates_are_removed():
    semantic_first = _chunk("semantic", 0.74)
    graph_first = _chunk("graph", 0.70)
    duplicate = _chunk("graph", 0.60)
    ranked = _rerank_chunks(
        [semantic_first, graph_first, duplicate], {"graph": 1.0}, 5
    )
    assert [item.chunk_id for item in ranked] == ["graph", "semantic"]
    assert ranked[0].graph_score == 1.0


def test_answer_context_contains_provenance_and_rank_signals():
    chunk = _chunk("c1", 0.8)
    chunk.citation = Citation(
        chunk_id="c1",
        document_id="d1",
        source="sharepoint",
        source_label="Current Policy",
        source_url="https://sharepoint/policy",
        source_updated_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        version="8",
    )
    context = _build_context(
        RetrievalResult(chunks=[chunk], experts=[], entities_found=[]), []
    )
    assert "document=Current Policy" in context
    assert "version=8" in context
    assert "authority=" in context


@pytest.mark.asyncio
async def test_answer_returns_only_model_cited_graph_sources(monkeypatch):
    first, second = _chunk("c1", 0.9), _chunk("c2", 0.8)

    async def citations(chunks, org_id):
        for chunk in chunks:
            chunk.citation = Citation(
                chunk_id=chunk.chunk_id,
                document_id=f"d-{chunk.chunk_id}",
                source="sharepoint",
                source_label=f"Document {chunk.chunk_id}",
            )

    class FakeCompletions:
        async def create(self, **_kwargs):
            message = type("Message", (), {"content": "The approved answer. [SOURCE: c2]"})()
            choice = type("Choice", (), {"message": message})()
            return type("Response", (), {"choices": [choice]})()

    class FakeClient:
        def __init__(self, **_kwargs):
            self.chat = type("Chat", (), {"completions": FakeCompletions()})()

    monkeypatch.setattr(answerer, "_attach_citations", citations)
    monkeypatch.setattr(answerer, "AsyncOpenAI", FakeClient)
    response = await generate_answer(
        "What is approved?",
        RetrievalResult(chunks=[first, second], experts=[], entities_found=[]),
        org_id="org",
    )
    assert [source.chunk_id for source in response.sources] == ["c2"]
