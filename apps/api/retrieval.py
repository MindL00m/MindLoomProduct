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
        1 - (ce.embedding <=> CAST(:query_vector AS vector)) AS similarity_score,
        exp(-greatest(extract(epoch FROM (now() - c.end_time)), 0) / 63072000.0)
          AS freshness_score,
        (
          CASE c.knowledge_type
            WHEN 'decision' THEN 1.0
            WHEN 'question_answer' THEN 0.9
            WHEN 'problem_report' THEN 0.75
            WHEN 'status_update' THEN 0.6
            ELSE 0.15
          END
        ) * (
          CASE c.confidence WHEN 'high' THEN 1.0 WHEN 'medium' THEN 0.7 ELSE 0.4 END
        ) AS authority_score
    FROM chunks c
    JOIN chunk_embeddings ce ON c.chunk_id = ce.chunk_id
    WHERE c.org_id = :org_id
      AND 1 - (ce.embedding <=> CAST(:query_vector AS vector)) > :threshold
      AND (
        cardinality(c.visible_to) = 0
        OR c.visible_to && CAST(:access_tokens AS text[])
      )
    ORDER BY similarity_score DESC
    LIMIT :limit
    """
)

# Query 1 — activity on chunks (ANSWERED / MENTIONED_IN).
_ACTIVITY_CYPHER = """
MATCH (p:Person {org_id: $org_id})-[r:ANSWERED|MENTIONED_IN]->(c:Chunk {org_id: $org_id})-[:RELATES_TO]->(e:Entity {org_id: $org_id})
WHERE e.canonical_name CONTAINS $entity_name
  AND (size(c.visible_to) = 0 OR any(token IN c.visible_to WHERE token IN $access_tokens))
WITH p, count(r) as rel_count, collect(type(r)) as rel_types
RETURN p.name as name, p.canonical_email as email, rel_count, rel_types
ORDER BY rel_count DESC
LIMIT 3
"""

_OWNS_CYPHER = """
MATCH (p:Person {org_id: $org_id})-[r:OWNS]->(e:Entity {org_id: $org_id})
WHERE e.canonical_name CONTAINS $entity_name
  AND (
    r.visible_to IS NULL OR size(r.visible_to) = 0
    OR any(token IN r.visible_to WHERE token IN $access_tokens)
  )
WITH p, count(r) as rel_count, collect(type(r)) as rel_types
RETURN p.name as name, p.canonical_email as email, rel_count, rel_types
ORDER BY rel_count DESC
LIMIT 3
"""

_CHUNK_GRAPH_SCORE_CYPHER = """
UNWIND $chunk_ids AS chunk_id
MATCH (c:Chunk {org_id: $org_id, chunk_id: chunk_id})
OPTIONAL MATCH (c)-[:RELATES_TO]->(e:Entity {org_id: $org_id})
WHERE any(term IN $entities WHERE
  toLower(e.canonical_name) CONTAINS toLower(term)
  OR toLower(term) CONTAINS toLower(e.canonical_name))
RETURN chunk_id, count(DISTINCT e) AS matches
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


async def _vector_search(
    query_vector: list[float],
    org_id: str,
    access_tokens: list[str] | None = None,
) -> list[ChunkResult]:
    """Task 3 — pgvector cosine-similarity search over chunks."""

    settings = get_settings()
    session_factory = get_session_factory()
    params = {
        "org_id": org_id,
        "query_vector": _format_vector(query_vector),
        "threshold": settings.retrieval_similarity_threshold,
        "limit": settings.retrieval_chunk_limit * 3,
        "access_tokens": access_tokens or [],
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
            freshness_score=float(row["freshness_score"]),
            authority_score=float(row["authority_score"]),
            retrieval_score=(
                0.78 * float(row["similarity_score"])
                + 0.12 * float(row["freshness_score"])
                + 0.10 * float(row["authority_score"])
            ),
        )
        for row in rows
    ]
    logger.info("pgvector search returned %d chunk(s)", len(chunks))
    return chunks


async def _graph_chunk_scores(
    chunks: list[ChunkResult], entities: list[str], org_id: str
) -> dict[str, float]:
    if not chunks or not entities:
        return {}
    driver = get_neo4j_driver()
    async with driver.session() as session:
        result = await session.run(
            _CHUNK_GRAPH_SCORE_CYPHER,
            org_id=org_id,
            chunk_ids=[chunk.chunk_id for chunk in chunks],
            entities=entities,
        )
        rows = [record.data() async for record in result]
    denominator = max(1, len(entities))
    return {
        str(row["chunk_id"]): min(float(row["matches"]) / denominator, 1.0)
        for row in rows
    }


def _rerank_chunks(
    chunks: list[ChunkResult], graph_scores: dict[str, float], limit: int
) -> list[ChunkResult]:
    """Apply graph overlap and return a stable, de-duplicated ranked list."""

    best_by_id: dict[str, ChunkResult] = {}
    for chunk in chunks:
        chunk.graph_score = graph_scores.get(chunk.chunk_id, 0.0)
        chunk.retrieval_score = min(
            chunk.retrieval_score + 0.10 * chunk.graph_score, 1.0
        )
        current = best_by_id.get(chunk.chunk_id)
        if current is None or chunk.retrieval_score > current.retrieval_score:
            best_by_id[chunk.chunk_id] = chunk
    return sorted(
        best_by_id.values(), key=lambda item: item.retrieval_score, reverse=True
    )[:limit]


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


async def _run_expert_query(
    cypher: str, entity_name: str, org_id: str, access_tokens: list[str]
) -> list[dict]:
    """Run a single expert traversal query for one entity in its own session."""

    async def _traverse(tx) -> list[dict]:  # type: ignore[no-untyped-def]
        result = await tx.run(
            cypher,
            entity_name=entity_name.lower(),
            org_id=org_id,
            access_tokens=access_tokens,
        )
        return [record.data() async for record in result]

    driver = get_neo4j_driver()
    async with driver.session() as session:
        return await session.execute_read(_traverse)


async def _expert_search(
    entities: list[str], org_id: str, access_tokens: list[str] | None = None
) -> list[ExpertResult]:
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
            tasks.append(
                _run_expert_query(cypher, entity, org_id, access_tokens or [])
            )
            entity_for_task.append(entity)

    results = await asyncio.gather(*tasks)

    rel_counts: dict[str, Counter[str]] = {}
    matched_entities: dict[str, list[str]] = {}
    expert_emails: dict[str, str] = {}
    for entity, rows in zip(entity_for_task, results):
        for row in rows:
            name = row.get("name")
            if not name:
                continue
            counts = rel_counts.setdefault(name, Counter())
            counts.update(row.get("rel_types") or [])
            if row.get("email"):
                expert_emails[name] = str(row["email"]).lower()
            seen = matched_entities.setdefault(name, [])
            if entity not in seen:
                seen.append(entity)

    experts = [
        ExpertResult(
            name=name,
            reason=_build_reason(counts, matched_entities[name]),
            relationship_count=sum(counts.values()),
            email=expert_emails.get(name),
        )
        for name, counts in rel_counts.items()
    ]
    experts.sort(key=lambda expert: expert.relationship_count, reverse=True)
    logger.info("Neo4j traversal surfaced %d expert(s)", len(experts))
    return experts


async def retrieve(
    question: str,
    history: list[ChatMessage] | None = None,
    org_id: str = "",
    access_tokens: list[str] | None = None,
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
        return await _vector_search(query_vector, org_id, access_tokens)

    async def _expert_pipeline() -> tuple[list[str], list[ExpertResult]]:
        entities = await _extract_entities(search_query)
        experts = await _expert_search(entities, org_id, access_tokens)
        return entities, experts

    chunks, (entities, experts) = await asyncio.gather(
        _chunk_pipeline(),
        _expert_pipeline(),
    )
    graph_scores = await _graph_chunk_scores(chunks, entities, org_id)
    chunks = _rerank_chunks(
        chunks, graph_scores, get_settings().retrieval_chunk_limit
    )

    return RetrievalResult(chunks=chunks, experts=experts, entities_found=entities)
