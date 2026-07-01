"""Tests for chat-only (ephemeral) attachment handling in the answerer."""

from __future__ import annotations

import pytest

from answerer import _build_context, _ephemeral_sources, generate_answer
from models import EphemeralDocument, RetrievalResult


def test_build_context_includes_ephemeral_blocks():
    retrieval = RetrievalResult(chunks=[], experts=[], entities_found=[])
    ephemeral = [
        EphemeralDocument(
            document_id="doc-1",
            filename="notes.txt",
            text="Secret roadmap details.",
        )
    ]
    context = _build_context(retrieval, ephemeral)
    assert "[EPHEMERAL: doc-1 | notes.txt]" in context
    assert "Secret roadmap details." in context


def test_ephemeral_sources_label_chat_only():
    docs = [
        EphemeralDocument(
            document_id="doc-1",
            filename="brief.pdf",
            text="Only for this conversation.",
        )
    ]
    sources = _ephemeral_sources(docs)
    assert len(sources) == 1
    assert sources[0].chunk_id == "doc-1"
    assert sources[0].citation is not None
    assert sources[0].citation.source_label == "brief.pdf (this chat only)"


@pytest.mark.asyncio
async def test_generate_answer_without_context_returns_fallback():
    retrieval = RetrievalResult(chunks=[], experts=[], entities_found=[])
    response = await generate_answer("What is the plan?", retrieval)
    assert "don't have enough information" in response.answer.lower()
    assert response.sources == []
