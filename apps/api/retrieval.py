"""Hybrid retrieval: pgvector similarity search + Neo4j expertise traversal.

Given a natural-language question, this module concurrently:

* embeds the question and runs a pgvector cosine-similarity search over chunks, and
* extracts named entities from the question and traverses the Neo4j graph to
  surface people connected to those entities **and** chunks linked to those
  entities via ``RELATES_TO`` (so Ask can answer about calendar/topic entities
  even when pure vector similarity misses).

The two pipelines are independent and overlap via :func:`asyncio.gather`; within
each pipeline the search step runs after its prerequisite (embedding / entity
extraction) completes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from collections import Counter
from datetime import datetime, timezone

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
No preamble, no markdown. Include proper nouns: people, projects, systems,
locations, nicknames, and titled characters (e.g. "Mr.Greedy", "Alpha Launch").
Preserve the spelling from the question when possible.

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

_CHUNKS_BY_ID_SQL = text(
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
        CAST(:entity_similarity AS double precision) AS similarity_score,
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
    WHERE c.org_id = :org_id
      AND c.chunk_id = ANY(:chunk_ids)
      AND (
        cardinality(c.visible_to) = 0
        OR c.visible_to && CAST(:access_tokens AS text[])
      )
    """
)

# Exact-ish lexical fallback: match entity names inside chunk text even when
# vector similarity is low and/or Neo4j RELATES_TO edges are missing.
_LEXICAL_ENTITY_SQL = text(
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
        CAST(:entity_similarity AS double precision) AS similarity_score,
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
    WHERE c.org_id = :org_id
      AND (
        cardinality(c.visible_to) = 0
        OR c.visible_to && CAST(:access_tokens AS text[])
      )
      AND (
        regexp_replace(lower(c.raw_text), '[^a-z0-9]+', '', 'g')
          LIKE '%' || :normalized || '%'
        OR regexp_replace(lower(c.summary), '[^a-z0-9]+', '', 'g')
          LIKE '%' || :normalized || '%'
        OR lower(c.raw_text) LIKE '%' || :raw_lower || '%'
        OR lower(c.summary) LIKE '%' || :raw_lower || '%'
      )
    ORDER BY c.end_time DESC NULLS LAST
    LIMIT :limit
    """
)

# Query 1 — activity on chunks (ANSWERED / MENTIONED_IN).
_ACTIVITY_CYPHER = """
MATCH (p:Person {org_id: $org_id})-[r:ANSWERED|MENTIONED_IN]->(c:Chunk {org_id: $org_id})-[:RELATES_TO]->(e:Entity {org_id: $org_id})
WHERE (
  toLower(e.canonical_name) CONTAINS $entity_name
  OR toLower(coalesce(e.name, '')) CONTAINS $entity_name
  OR replace(replace(toLower(e.canonical_name), ' ', ''), '.', '')
       CONTAINS replace(replace($entity_name, ' ', ''), '.', '')
  OR replace(replace($entity_name, ' ', ''), '.', '')
       CONTAINS replace(replace(toLower(e.canonical_name), ' ', ''), '.', '')
)
  AND (size(c.visible_to) = 0 OR any(vis IN coalesce(c.visible_to, []) WHERE
        any(token IN $access_tokens WHERE toLower(token) = toLower(vis))))
WITH p, count(r) as rel_count, collect(type(r)) as rel_types
RETURN p.name as name, p.canonical_email as email, rel_count, rel_types
ORDER BY rel_count DESC
LIMIT 3
"""

_OWNS_CYPHER = """
MATCH (p:Person {org_id: $org_id})-[r:OWNS]->(e:Entity {org_id: $org_id})
WHERE (
  toLower(e.canonical_name) CONTAINS $entity_name
  OR toLower(coalesce(e.name, '')) CONTAINS $entity_name
  OR replace(replace(toLower(e.canonical_name), ' ', ''), '.', '')
       CONTAINS replace(replace($entity_name, ' ', ''), '.', '')
  OR replace(replace($entity_name, ' ', ''), '.', '')
       CONTAINS replace(replace(toLower(e.canonical_name), ' ', ''), '.', '')
)
  AND (
    r.visible_to IS NULL OR size(r.visible_to) = 0
    OR any(vis IN coalesce(r.visible_to, []) WHERE
         any(token IN $access_tokens WHERE toLower(token) = toLower(vis)))
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
  OR toLower(term) CONTAINS toLower(e.canonical_name)
  OR toLower(coalesce(e.name, '')) CONTAINS toLower(term)
  OR replace(replace(toLower(e.canonical_name), ' ', ''), '.', '')
       CONTAINS replace(replace(toLower(term), ' ', ''), '.', '')
  OR replace(replace(toLower(term), ' ', ''), '.', '')
       CONTAINS replace(replace(toLower(e.canonical_name), ' ', ''), '.', ''))
RETURN chunk_id, count(DISTINCT e) AS matches
"""

# Chunks linked to entities named in the question — return full Neo4j payload so
# Ask still works when Postgres was reset / drifted while Neo4j retained data.
_ENTITY_CHUNKS_CYPHER = """
MATCH (e:Entity {org_id: $org_id})
WHERE any(term IN $entity_terms WHERE
  toLower(e.canonical_name) CONTAINS toLower(term)
  OR toLower(term) CONTAINS toLower(e.canonical_name)
  OR toLower(coalesce(e.name, '')) CONTAINS toLower(term)
  OR toLower(term) CONTAINS toLower(coalesce(e.name, ''))
  OR replace(replace(toLower(e.canonical_name), ' ', ''), '.', '')
       CONTAINS replace(replace(toLower(term), ' ', ''), '.', '')
  OR replace(replace(toLower(term), ' ', ''), '.', '')
       CONTAINS replace(replace(toLower(e.canonical_name), ' ', ''), '.', ''))
MATCH (c:Chunk {org_id: $org_id})-[:RELATES_TO]->(e)
WHERE size(coalesce(c.visible_to, [])) = 0
   OR any(vis IN coalesce(c.visible_to, []) WHERE
        any(token IN $access_tokens WHERE toLower(token) = toLower(vis)))
RETURN DISTINCT
  c.chunk_id AS chunk_id,
  coalesce(c.raw_text, '') AS raw_text,
  coalesce(c.summary, '') AS summary,
  coalesce(c.knowledge_type, 'noise') AS knowledge_type,
  coalesce(c.confidence, 'low') AS confidence,
  c.start_time AS start_time,
  c.end_time AS end_time,
  coalesce(c.source, '') AS source,
  coalesce(c.source_label, '') AS source_label
LIMIT $limit
"""

_NEO4J_LEXICAL_CHUNKS_CYPHER = """
MATCH (c:Chunk {org_id: $org_id})
WHERE (
  replace(replace(toLower(coalesce(c.raw_text, '')), ' ', ''), '.', '')
    CONTAINS $normalized
  OR replace(replace(toLower(coalesce(c.summary, '')), ' ', ''), '.', '')
    CONTAINS $normalized
  OR toLower(coalesce(c.raw_text, '')) CONTAINS $raw_lower
  OR toLower(coalesce(c.summary, '')) CONTAINS $raw_lower
)
AND (
  size(coalesce(c.visible_to, [])) = 0
  OR any(vis IN coalesce(c.visible_to, []) WHERE
       any(token IN $access_tokens WHERE toLower(token) = toLower(vis)))
)
RETURN DISTINCT
  c.chunk_id AS chunk_id,
  coalesce(c.raw_text, '') AS raw_text,
  coalesce(c.summary, '') AS summary,
  coalesce(c.knowledge_type, 'noise') AS knowledge_type,
  coalesce(c.confidence, 'low') AS confidence,
  c.start_time AS start_time,
  c.end_time AS end_time,
  coalesce(c.source, '') AS source,
  coalesce(c.source_label, '') AS source_label
ORDER BY coalesce(c.end_time, datetime('1970-01-01T00:00:00Z')) DESC
LIMIT $limit
"""

# Human-readable phrasing for each relationship type, as (singular, plural noun).
_REL_PHRASES: dict[str, tuple[str, str, str]] = {
    "ANSWERED": ("answered", "chunk", "chunks"),
    "OWNS": ("owns", "topic", "topics"),
    "MENTIONED_IN": ("mentioned in", "chunk", "chunks"),
    "ASKED": ("asked", "question", "questions"),
}

# Explicit entity-link hits get a strong similarity so they survive ranking even
# when the calendar/title text would score poorly against the question embedding.
_ENTITY_LINK_SIMILARITY = 0.88

_HEURISTIC_ABOUT = re.compile(
    r"(?:about|regarding|concerning)\s+(?:the\s+)?(?:entity\s+)?[\"']?([^\"'?]+)[\"']?",
    re.IGNORECASE,
)
_HEURISTIC_WHO_WHAT = re.compile(
    r"(?:who|what)\s+(?:is|are|was|were)\s+(?:the\s+)?(?:entity\s+)?[\"']?([^\"'?]+)[\"']?",
    re.IGNORECASE,
)
_HEURISTIC_TITLE = re.compile(
    r"\b((?:Mr|Mrs|Ms|Dr)\.?\s*[A-Za-z][A-Za-z0-9_-]*)\b",
)


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


def normalize_entity_key(name: str) -> str:
    """Collapse case/space/punctuation so 'Mr.Greedy' ≡ 'Mr. Greedy'."""

    return re.sub(r"[^a-z0-9]+", "", name.strip().lower())


def _clean_heuristic_candidate(raw: str) -> str | None:
    candidate = raw.strip(" \t\n\r.,:;!?")
    candidate = re.split(
        r"\s+(?:and|or|that|which|who|from|in|on|with)\s+",
        candidate,
        maxsplit=1,
    )[0].strip()
    if not candidate or len(candidate) > 80:
        return None
    # Ignore generic pronouns / filler.
    if candidate.lower() in {"it", "this", "that", "they", "he", "she", "someone"}:
        return None
    return candidate


def _heuristic_entities(question: str) -> list[str]:
    """Cheap fallback entities when the user names something explicitly."""

    found: list[str] = []
    for pattern in (_HEURISTIC_WHO_WHAT, _HEURISTIC_ABOUT):
        match = pattern.search(question)
        if not match:
            continue
        candidate = _clean_heuristic_candidate(match.group(1))
        if candidate:
            found.append(candidate)
    for match in _HEURISTIC_TITLE.finditer(question):
        candidate = _clean_heuristic_candidate(match.group(1))
        if candidate:
            found.append(candidate)
    return _merge_entity_names(found)


def _merge_entity_names(*groups: list[str]) -> list[str]:
    """Dedupe entity strings while treating punctuation variants as the same."""

    by_key: dict[str, str] = {}
    for group in groups:
        for name in group:
            cleaned = name.strip()
            if not cleaned:
                continue
            key = normalize_entity_key(cleaned) or cleaned.lower()
            # Prefer the longer / more punctuated original spelling for display.
            existing = by_key.get(key)
            if existing is None or len(cleaned) > len(existing):
                by_key[key] = cleaned
    return list(by_key.values())


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

    Never raises: on any failure it logs and returns heuristic entities only.
    """

    heuristic = _heuristic_entities(question)
    try:
        response = await _client().chat.completions.create(
            model=_EXTRACTION_MODEL,
            messages=[{"role": "user", "content": _ENTITY_PROMPT.format(question=question)}],
            temperature=0,
        )
        content = response.choices[0].message.content or ""
        return _merge_entity_names(_parse_entities(content), heuristic)
    except Exception as exc:  # noqa: BLE001 - retrieval must not fail on extraction
        logger.warning("Entity extraction failed for question; returning none: %s", exc)
        return heuristic


def _rows_to_chunks(rows: object, *, score_boost: float = 0.0) -> list[ChunkResult]:
    chunks: list[ChunkResult] = []
    for row in rows:  # type: ignore[attr-defined]
        similarity = float(row["similarity_score"])
        freshness = float(row["freshness_score"])
        authority = float(row["authority_score"])
        retrieval = (
            0.78 * similarity + 0.12 * freshness + 0.10 * authority + score_boost
        )
        chunks.append(
            ChunkResult(
                chunk_id=row["chunk_id"],
                raw_text=row["raw_text"],
                summary=row["summary"],
                speakers=list(row["speakers"] or []),
                start_time=row["start_time"],
                end_time=row["end_time"],
                knowledge_type=row["knowledge_type"],
                confidence=row["confidence"],
                similarity_score=similarity,
                freshness_score=freshness,
                authority_score=authority,
                retrieval_score=min(retrieval, 1.0),
            )
        )
    return chunks


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

    chunks = _rows_to_chunks(rows)
    logger.info("pgvector search returned %d chunk(s)", len(chunks))
    return chunks


def _authority_for(knowledge_type: str, confidence: str) -> float:
    type_score = {
        "decision": 1.0,
        "question_answer": 0.9,
        "problem_report": 0.75,
        "status_update": 0.6,
    }.get(knowledge_type, 0.15)
    conf_score = {"high": 1.0, "medium": 0.7}.get(confidence, 0.4)
    return type_score * conf_score


def _freshness_for(end_time: datetime | None) -> float:
    if end_time is None:
        return 0.5
    stamp = end_time if end_time.tzinfo else end_time.replace(tzinfo=timezone.utc)
    age = max((datetime.now(timezone.utc) - stamp).total_seconds(), 0.0)
    return float(math.exp(-age / 63_072_000.0))


def _neo4j_rows_to_chunks(
    rows: list[dict], *, score_boost: float = 0.05
) -> list[ChunkResult]:
    """Build ChunkResult rows from Neo4j Chunk properties (no Postgres needed)."""

    chunks: list[ChunkResult] = []
    for row in rows:
        chunk_id = str(row.get("chunk_id") or "")
        raw_text = str(row.get("raw_text") or "")
        if not chunk_id or not raw_text.strip():
            continue
        knowledge_type = str(row.get("knowledge_type") or "noise")
        confidence = str(row.get("confidence") or "low")
        end_time = row.get("end_time")
        start_time = row.get("start_time")
        if not isinstance(end_time, datetime):
            end_time = datetime.now(timezone.utc)
        if not isinstance(start_time, datetime):
            start_time = end_time
        similarity = _ENTITY_LINK_SIMILARITY
        freshness = _freshness_for(end_time)
        authority = _authority_for(knowledge_type, confidence)
        retrieval = min(
            0.78 * similarity + 0.12 * freshness + 0.10 * authority + score_boost,
            1.0,
        )
        summary = str(row.get("summary") or "")
        source_label = str(row.get("source_label") or "")
        if not summary and source_label:
            summary = source_label
        chunks.append(
            ChunkResult(
                chunk_id=chunk_id,
                raw_text=raw_text,
                summary=summary,
                speakers=[],
                start_time=start_time,
                end_time=end_time,
                knowledge_type=knowledge_type,
                confidence=confidence,
                similarity_score=similarity,
                freshness_score=freshness,
                authority_score=authority,
                retrieval_score=retrieval,
            )
        )
    return chunks


async def _entity_linked_chunks(
    entities: list[str],
    org_id: str,
    access_tokens: list[str] | None = None,
    *,
    limit: int = 20,
) -> list[ChunkResult]:
    """Return chunks linked via RELATES_TO, hydrated from Neo4j."""

    if not entities:
        return []
    driver = get_neo4j_driver()
    async with driver.session() as session:
        result = await session.run(
            _ENTITY_CHUNKS_CYPHER,
            org_id=org_id,
            entity_terms=entities,
            access_tokens=access_tokens or [],
            limit=limit,
        )
        rows = [record.data() async for record in result]
    chunks = _neo4j_rows_to_chunks(rows, score_boost=0.08)
    logger.info("Neo4j entity-linked retrieval returned %d chunk(s)", len(chunks))
    return chunks


async def _load_chunks_by_ids(
    chunk_ids: list[str],
    org_id: str,
    access_tokens: list[str] | None = None,
) -> list[ChunkResult]:
    """Hydrate Postgres chunk rows for entity-linked Neo4j hits."""

    if not chunk_ids:
        return []
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(
            _CHUNKS_BY_ID_SQL,
            {
                "org_id": org_id,
                "chunk_ids": list(chunk_ids),
                "access_tokens": access_tokens or [],
                "entity_similarity": _ENTITY_LINK_SIMILARITY,
            },
        )
        rows = result.mappings().all()
    chunks = _rows_to_chunks(rows, score_boost=0.05)
    logger.info("Postgres entity-linked hydration returned %d chunk(s)", len(chunks))
    return chunks


async def _lexical_entity_search(
    entities: list[str],
    org_id: str,
    access_tokens: list[str] | None = None,
    *,
    limit_per_entity: int = 8,
) -> list[ChunkResult]:
    """Find chunks whose text mentions an entity (Postgres + Neo4j)."""

    if not entities:
        return []
    session_factory = get_session_factory()
    collected: list[ChunkResult] = []
    seen: set[str] = set()
    async with session_factory() as session:
        for entity in entities:
            normalized = normalize_entity_key(entity)
            raw_lower = entity.strip().lower()
            if len(normalized) < 3 and len(raw_lower) < 3:
                continue
            result = await session.execute(
                _LEXICAL_ENTITY_SQL,
                {
                    "org_id": org_id,
                    "access_tokens": access_tokens or [],
                    "entity_similarity": _ENTITY_LINK_SIMILARITY,
                    "normalized": normalized or raw_lower,
                    "raw_lower": raw_lower,
                    "limit": limit_per_entity,
                },
            )
            for chunk in _rows_to_chunks(result.mappings().all(), score_boost=0.08):
                if chunk.chunk_id in seen:
                    continue
                seen.add(chunk.chunk_id)
                collected.append(chunk)

    # Neo4j lexical fallback covers graph-only / drifted stores.
    driver = get_neo4j_driver()
    async with driver.session() as graph:
        for entity in entities:
            normalized = normalize_entity_key(entity)
            raw_lower = entity.strip().lower()
            if len(normalized) < 3 and len(raw_lower) < 3:
                continue
            result = await graph.run(
                _NEO4J_LEXICAL_CHUNKS_CYPHER,
                org_id=org_id,
                access_tokens=access_tokens or [],
                normalized=normalized or raw_lower,
                raw_lower=raw_lower,
                limit=limit_per_entity,
            )
            for chunk in _neo4j_rows_to_chunks(
                [record.data() async for record in result], score_boost=0.08
            ):
                if chunk.chunk_id in seen:
                    continue
                seen.add(chunk.chunk_id)
                collected.append(chunk)

    logger.info(
        "Lexical entity search for %s returned %d chunk(s)",
        entities,
        len(collected),
    )
    return collected


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
      * extract entities -> Neo4j traversal (experts + entity-linked chunks).

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

    async def _entity_pipeline() -> tuple[list[str], list[ExpertResult], list[ChunkResult]]:
        entities = await _extract_entities(search_query)
        # Always fold heuristic names from the raw question too ("who is Mr. Greedy").
        entities = _merge_entity_names(entities, _heuristic_entities(question))
        experts_task = asyncio.create_task(
            _expert_search(entities, org_id, access_tokens)
        )
        linked_chunks, lexical_chunks = await asyncio.gather(
            _entity_linked_chunks(entities, org_id, access_tokens),
            _lexical_entity_search(entities, org_id, access_tokens),
        )
        experts = await experts_task
        return entities, experts, [*linked_chunks, *lexical_chunks]

    vector_chunks, (entities, experts, entity_chunks) = await asyncio.gather(
        _chunk_pipeline(),
        _entity_pipeline(),
    )
    merged = [*vector_chunks, *entity_chunks]
    graph_scores = await _graph_chunk_scores(merged, entities, org_id)
    chunks = _rerank_chunks(
        merged, graph_scores, get_settings().retrieval_chunk_limit
    )

    return RetrievalResult(chunks=chunks, experts=experts, entities_found=entities)
