"""Hybrid retrieval: pgvector similarity search + Neo4j expertise traversal.

Given a natural-language question, this module concurrently:

* embeds the question and runs a pgvector cosine-similarity search over chunks, and
* extracts named entities from the question and traverses the Neo4j graph to
  surface people connected to those entities.

The two pipelines are independent and overlap via :func:`asyncio.gather`; within
each pipeline the search step runs after its prerequisite (embedding / entity
extraction) completes.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import Counter

from openai import AsyncOpenAI
from sqlalchemy import text

from config import get_settings
from database import get_neo4j_driver, get_session_factory
from models import ChatMessage, ChunkResult, ExpertResult, RetrievalResult

logger = logging.getLogger(__name__)

_EMBEDDING_MODEL = "text-embedding-3-small"
_EXTRACTION_MODEL = "gpt-4o-mini"

_CONDENSE_PROMPT = """\
Given the conversation so far and a follow-up question, rewrite the follow-up as
a standalone search query that captures the user's intent without needing the
prior turns. Resolve pronouns and references (it, that, they, the project) using
the history. Return ONLY the rewritten query text, no preamble.

Conversation:
{history}

Follow-up question: {question}

Standalone query:"""

# Only the most recent turns matter for resolving references; cap to keep the
# condensation prompt small and cheap.
_CONDENSE_HISTORY_TURNS = 6

_ENTITY_PROMPT = """\
Extract named entities from this question. Return only a JSON array of strings. 
No preamble, no markdown. Only include proper nouns: equipment names, 
people names, project names, system names, locations.

Question: {question}"""

# Cosine similarity search. The query vector is bound as text and cast to vector
# so it works without per-connection pgvector type registration.
_VECTOR_SQL = text(
    """
    SELECT
        c.chunk_id,
        c.raw_text,
        c.summary,
        c.speakers,
        c.start_time,
        c.end_time,
        c.knowledge_type,
        c.confidence,
        1 - (ce.embedding <=> CAST(:query_vector AS vector)) AS similarity_score
    FROM chunks c
    JOIN chunk_embeddings ce ON c.chunk_id = ce.chunk_id
    WHERE 1 - (ce.embedding <=> CAST(:query_vector AS vector)) > :threshold
    ORDER BY similarity_score DESC
    LIMIT :limit
    """
)

# Query 1 — activity on chunks (ANSWERED / MENTIONED_IN).
_ACTIVITY_CYPHER = """
MATCH (p:Person)-[r:ANSWERED|MENTIONED_IN]->(c:Chunk)-[:RELATES_TO]->(e:Entity)
WHERE e.canonical_name CONTAINS $entity_name
WITH p, count(r) as rel_count, collect(type(r)) as rel_types
RETURN p.name as name, rel_count, rel_types
ORDER BY rel_count DESC
LIMIT 3
"""

# Query 2 — ownership of entities (OWNS targets Entity, not Chunk).
_OWNS_CYPHER = """
MATCH (p:Person)-[r:OWNS]->(e:Entity)
WHERE e.canonical_name CONTAINS $entity_name
WITH p, count(r) as rel_count, collect(type(r)) as rel_types
RETURN p.name as name, rel_count, rel_types
ORDER BY rel_count DESC
LIMIT 3
"""

# Human-readable phrasing for each relationship type, as (singular, plural noun).
_REL_PHRASES: dict[str, tuple[str, str, str]] = {
    "ANSWERED": ("answered", "chunk", "chunks"),
    "OWNS": ("owns", "topic", "topics"),
    "MENTIONED_IN": ("mentioned in", "chunk", "chunks"),
    "ASKED": ("asked", "question", "questions"),
}


def _client() -> AsyncOpenAI:
    """Build an OpenAI async client with the configured timeout."""

    settings = get_settings()
    return AsyncOpenAI(
        api_key=settings.openai_api_key,
        timeout=settings.openai_request_timeout_seconds,
    )


def _format_vector(vector: list[float]) -> str:
    """Render an embedding as the pgvector text literal '[a,b,c]'."""

    return "[" + ",".join(str(value) for value in vector) + "]"


async def _embed_question(question: str) -> list[float]:
    """Task 1 — embed the question into a query vector."""

    response = await _client().embeddings.create(model=_EMBEDDING_MODEL, input=question)
    return list(response.data[0].embedding)


def _parse_entities(content: str) -> list[str]:
    """Parse the entity-extraction response into a list of strings."""

    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()

    parsed = json.loads(cleaned)
    if not isinstance(parsed, list):
        raise ValueError("Entity extraction did not return a JSON array")
    return [str(item).strip() for item in parsed if str(item).strip()]


async def _condense_query(question: str, history: list[ChatMessage]) -> str:
    """Rewrite a follow-up into a standalone search query using recent history.

    Never raises: on any failure it logs and falls back to the raw question, so
    retrieval degrades gracefully to non-conversational behaviour.
    """

    if not history:
        return question

    recent = history[-_CONDENSE_HISTORY_TURNS:]
    transcript = "\n".join(
        f"{'User' if turn.role == 'user' else 'Assistant'}: {turn.content}"
        for turn in recent
    )
    try:
        response = await _client().chat.completions.create(
            model=_EXTRACTION_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": _CONDENSE_PROMPT.format(
                        history=transcript, question=question
                    ),
                }
            ],
            temperature=0,
        )
        rewritten = (response.choices[0].message.content or "").strip()
        if rewritten:
            logger.info("Condensed follow-up into standalone query: %r", rewritten)
            return rewritten
    except Exception as exc:  # noqa: BLE001 - retrieval must not fail on condensation
        logger.warning("Query condensation failed; using raw question: %s", exc)
    return question


async def _extract_entities(question: str) -> list[str]:
    """Task 2 — extract named entities from the question via gpt-4o-mini.

    Never raises: on any failure it logs and returns an empty list.
    """

    try:
        response = await _client().chat.completions.create(
            model=_EXTRACTION_MODEL,
            messages=[{"role": "user", "content": _ENTITY_PROMPT.format(question=question)}],
            temperature=0,
        )
        content = response.choices[0].message.content or ""
        return _parse_entities(content)
    except Exception as exc:  # noqa: BLE001 - retrieval must not fail on extraction
        logger.warning("Entity extraction failed for question; returning none: %s", exc)
        return []


async def _vector_search(query_vector: list[float]) -> list[ChunkResult]:
    """Task 3 — pgvector cosine-similarity search over chunks."""

    settings = get_settings()
    session_factory = get_session_factory()
    params = {
        "query_vector": _format_vector(query_vector),
        "threshold": settings.retrieval_similarity_threshold,
        "limit": settings.retrieval_chunk_limit,
    }

    async with session_factory() as session:
        result = await session.execute(_VECTOR_SQL, params)
        rows = result.mappings().all()

    chunks = [
        ChunkResult(
            chunk_id=row["chunk_id"],
            raw_text=row["raw_text"],
            summary=row["summary"],
            speakers=list(row["speakers"] or []),
            start_time=row["start_time"],
            end_time=row["end_time"],
            knowledge_type=row["knowledge_type"],
            confidence=row["confidence"],
            similarity_score=float(row["similarity_score"]),
        )
        for row in rows
    ]
    logger.info("pgvector search returned %d chunk(s)", len(chunks))
    return chunks


def _build_reason(rel_counts: Counter[str], entities: list[str]) -> str:
    """Build a human-readable explanation from relationship-type counts."""

    fragments: list[str] = []
    for rel_type, count in rel_counts.most_common():
        verb, singular, plural = _REL_PHRASES.get(
            rel_type, (rel_type.lower(), "relationship", "relationships")
        )
        noun = singular if count == 1 else plural
        fragments.append(f"{verb} {count} {noun}")

    body = " and ".join(fragments) if fragments else "is connected"
    body = body[0].upper() + body[1:]
    related = ", ".join(entities)
    suffix = f" related to {related}" if related else ""
    return f"{body}{suffix}."


async def _run_expert_query(cypher: str, entity_name: str) -> list[dict]:
    """Run a single expert traversal query for one entity in its own session."""

    async def _traverse(tx) -> list[dict]:  # type: ignore[no-untyped-def]
        result = await tx.run(cypher, entity_name=entity_name)
        return [record.data() async for record in result]

    driver = get_neo4j_driver()
    async with driver.session() as session:
        return await session.execute_read(_traverse)


async def _expert_search(entities: list[str]) -> list[ExpertResult]:
    """Task 4 — traverse Neo4j for experts connected to the question's entities.

    Runs two queries per entity concurrently (chunk activity + entity ownership)
    via :func:`asyncio.gather`, then merges results by person name — summing
    relationship counts and combining relationship types — before ranking.
    """

    if not entities:
        return []

    # Build one task per (entity, query); track which entity each task targets.
    entity_for_task: list[str] = []
    tasks = []
    for entity in entities:
        for cypher in (_ACTIVITY_CYPHER, _OWNS_CYPHER):
            tasks.append(_run_expert_query(cypher, entity))
            entity_for_task.append(entity)

    results = await asyncio.gather(*tasks)

    rel_counts: dict[str, Counter[str]] = {}
    matched_entities: dict[str, list[str]] = {}
    for entity, rows in zip(entity_for_task, results):
        for row in rows:
            name = row.get("name")
            if not name:
                continue
            counts = rel_counts.setdefault(name, Counter())
            counts.update(row.get("rel_types") or [])
            seen = matched_entities.setdefault(name, [])
            if entity not in seen:
                seen.append(entity)

    experts = [
        ExpertResult(
            name=name,
            reason=_build_reason(counts, matched_entities[name]),
            relationship_count=sum(counts.values()),
        )
        for name, counts in rel_counts.items()
    ]
    experts.sort(key=lambda expert: expert.relationship_count, reverse=True)
    logger.info("Neo4j traversal surfaced %d expert(s)", len(experts))
    return experts


async def retrieve(
    question: str, history: list[ChatMessage] | None = None
) -> RetrievalResult:
    """Retrieve relevant chunks and experts for a natural-language question.

    When ``history`` is provided, the follow-up is first condensed into a
    standalone search query so references ("it", "that project") resolve against
    earlier turns. Retrieval then runs two independent pipelines concurrently:
      * embed the query -> pgvector similarity search (chunks), and
      * extract entities -> Neo4j traversal (experts).

    Args:
        question: The natural-language question to answer.
        history: Prior conversation turns (oldest first), for follow-up context.

    Returns:
        A :class:`RetrievalResult` with chunks ranked by similarity score and
        experts ranked by relationship count.
    """

    search_query = await _condense_query(question, history or [])

    async def _chunk_pipeline() -> list[ChunkResult]:
        query_vector = await _embed_question(search_query)
        return await _vector_search(query_vector)

    async def _expert_pipeline() -> tuple[list[str], list[ExpertResult]]:
        entities = await _extract_entities(search_query)
        experts = await _expert_search(entities)
        return entities, experts

    chunks, (entities, experts) = await asyncio.gather(
        _chunk_pipeline(),
        _expert_pipeline(),
    )

    return RetrievalResult(chunks=chunks, experts=experts, entities_found=entities)
