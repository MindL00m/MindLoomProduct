"""Persistence layer for chunks: PostgreSQL (pgvector) and Neo4j.

PostgreSQL stores the raw chunk text, metadata, and the embedding vector via the
SQLAlchemy ORM. Neo4j stores the knowledge-graph relationships. Both use the
shared, pooled connections from :mod:`database`.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import ARRAY, DateTime, String, Text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from database import get_neo4j_driver, get_session_factory
from embedder import EMBEDDING_DIMENSIONS
from models import (
    Chunk,
    ChunkMetadata,
    DirectoryIngestResult,
    DirectoryPerson,
    OrgEdge,
    OrgGraphResponse,
    OrgPerson,
)

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """Declarative base for the ingestion ORM models."""


class ChunkRow(Base):
    """ORM mapping for the ``chunks`` table."""

    __tablename__ = "chunks"

    chunk_id: Mapped[str] = mapped_column(String, primary_key=True)
    org_id: Mapped[str] = mapped_column(String, nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    speakers: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False)
    knowledge_type: Mapped[str] = mapped_column(String, nullable=False)
    confidence: Mapped[str] = mapped_column(String, nullable=False)
    confidence_reason: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ChunkEmbeddingRow(Base):
    """ORM mapping for the ``chunk_embeddings`` table (pgvector)."""

    __tablename__ = "chunk_embeddings"

    chunk_id: Mapped[str] = mapped_column(String, primary_key=True)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMENSIONS), nullable=False)


async def save_to_postgres(
    chunk: Chunk, metadata: ChunkMetadata, embedding: list[float], org_id: str
) -> None:
    """Persist a chunk, its metadata, and its embedding to PostgreSQL.

    Idempotent: re-ingesting the same ``chunk_id`` upserts both rows.

    Args:
        chunk: The chunk being stored.
        metadata: Extracted metadata for the chunk.
        embedding: The chunk's embedding vector (length must be 1536).
    """

    if len(embedding) != EMBEDDING_DIMENSIONS:
        raise ValueError(
            f"Embedding has {len(embedding)} dims, expected {EMBEDDING_DIMENSIONS}"
        )

    session_factory = get_session_factory()
    now = datetime.now(timezone.utc)

    chunk_values = {
        "chunk_id": chunk.chunk_id,
        "org_id": org_id,
        "raw_text": chunk.raw_text,
        "start_time": chunk.start_time,
        "end_time": chunk.end_time,
        "speakers": chunk.speakers,
        "knowledge_type": metadata.knowledge_type,
        "confidence": metadata.confidence,
        "confidence_reason": metadata.confidence_reason,
        "summary": metadata.summary,
        "created_at": now,
    }

    async with session_factory() as session:
        async with session.begin():
            chunk_stmt = pg_insert(ChunkRow).values(**chunk_values)
            chunk_stmt = chunk_stmt.on_conflict_do_update(
                index_elements=[ChunkRow.chunk_id],
                set_={k: v for k, v in chunk_values.items() if k != "chunk_id"},
            )
            await session.execute(chunk_stmt)

            embedding_stmt = pg_insert(ChunkEmbeddingRow).values(
                chunk_id=chunk.chunk_id,
                embedding=embedding,
            )
            embedding_stmt = embedding_stmt.on_conflict_do_update(
                index_elements=[ChunkEmbeddingRow.chunk_id],
                set_={"embedding": embedding},
            )
            await session.execute(embedding_stmt)

    logger.info("Saved chunk %s to PostgreSQL", chunk.chunk_id)


# Default values for graph properties the ingestion pipeline cannot yet derive.
_DEFAULT_ENTITY_TYPE = "topic"  # one of: project, system, topic, tool
_ANSWERED_SIGNAL_TYPE = "explicit"  # LLM-extracted answers are treated as explicit

# Each statement guards against empty input lists, because an UNWIND over an
# empty list collapses the row stream and would silently skip later clauses.

# Chat-derived people are de-duplicated on canonical_name (display name) since
# conversations carry no email; a stable person_id is minted once. New-schema
# fields get sensible defaults on create so every Person is shape-consistent.
_PEOPLE_CYPHER = """
UNWIND $people AS name
MERGE (p:Person {org_id: $org_id, canonical_name: name})
ON CREATE SET p.person_id = randomUUID(),
              p.org_id = $org_id,
              p.name = name,
              p.is_system_user = false,
              p.status = 'active',
              p.groups = [],
              p.source_ids = '{}',
              p.created_at = $ts
SET p.updated_at = $ts,
    p.last_active = CASE
        WHEN p.last_active IS NULL OR p.last_active < $ts THEN $ts
        ELSE p.last_active
    END
"""

_ENTITIES_CYPHER = """
UNWIND $entities AS ent
MERGE (e:Entity {org_id: $org_id, canonical_name: ent.name})
ON CREATE SET e.entity_id = randomUUID(),
              e.org_id = $org_id,
              e.name = ent.name,
              e.type = ent.type
SET e.visible_to = $visible_to
"""

_CHUNK_NODE_CYPHER = """
MERGE (c:Chunk {chunk_id: $chunk_id})
SET c.org_id = $org_id,
    c.raw_text = $raw_text,
    c.summary = $summary,
    c.knowledge_type = $knowledge_type,
    c.confidence = $confidence,
    c.confidence_reason = $confidence_reason,
    c.source = $source,
    c.source_label = $source_label,
    c.visible_to = $visible_to,
    c.start_time = $start_time,
    c.end_time = $end_time,
    c.created_at = $created_at
"""

_MENTIONED_IN_CYPHER = """
MATCH (c:Chunk {chunk_id: $chunk_id, org_id: $org_id})
UNWIND $people AS name
MATCH (p:Person {org_id: $org_id, canonical_name: name})
MERGE (p)-[r:MENTIONED_IN]->(c)
SET r.timestamp = $ts
"""

_RELATES_TO_CYPHER = """
MATCH (c:Chunk {chunk_id: $chunk_id, org_id: $org_id})
UNWIND $rels AS rel
MATCH (e:Entity {org_id: $org_id, canonical_name: rel.name})
MERGE (c)-[r:RELATES_TO]->(e)
SET r.relevance = rel.relevance
"""

_ASKED_CYPHER = """
MATCH (c:Chunk {chunk_id: $chunk_id, org_id: $org_id})
UNWIND $people AS name
MATCH (p:Person {org_id: $org_id, canonical_name: name})
MERGE (p)-[r:ASKED]->(c)
SET r.timestamp = $ts
"""

_ANSWERED_CYPHER = """
MATCH (c:Chunk {chunk_id: $chunk_id, org_id: $org_id})
UNWIND $people AS name
MATCH (p:Person {org_id: $org_id, canonical_name: name})
MERGE (p)-[r:ANSWERED]->(c)
SET r.timestamp = $ts, r.signal_type = $signal_type
"""

_OWNS_CYPHER = """
UNWIND $pairs AS pair
MATCH (p:Person {org_id: $org_id, canonical_name: pair.person})
MATCH (e:Entity {org_id: $org_id, canonical_name: pair.topic})
MERGE (p)-[r:OWNS]->(e)
ON CREATE SET r.since = $ts
SET r.confirmed = false
"""

_QUESTION_NODE_CYPHER = """
MERGE (q:Question {question_id: $question_id})
ON CREATE SET q.created_at = $created_at
SET q.org_id = $org_id,
    q.text = $text,
    q.status = $status,
    q.resolved_at = $resolved_at,
    q.visible_to = $visible_to
"""

_QUESTION_ASKED_BY_CYPHER = """
MATCH (q:Question {question_id: $question_id, org_id: $org_id})
UNWIND $people AS name
MATCH (p:Person {org_id: $org_id, canonical_name: name})
MERGE (q)-[:ASKED_BY]->(p)
"""

_QUESTION_RELATED_TO_CYPHER = """
MATCH (q:Question {question_id: $question_id, org_id: $org_id})
UNWIND $entities AS name
MATCH (e:Entity {org_id: $org_id, canonical_name: name})
MERGE (q)-[:RELATED_TO]->(e)
"""

_QUESTION_ANSWERED_BY_CYPHER = """
MATCH (q:Question {question_id: $question_id, org_id: $org_id})
MATCH (c:Chunk {chunk_id: $chunk_id, org_id: $org_id})
MERGE (q)-[r:ANSWERED_BY]->(c)
SET r.timestamp = $ts
"""


async def save_to_neo4j(
    chunk: Chunk,
    metadata: ChunkMetadata,
    org_id: str,
    source: str = "unknown",
    source_label: str = "",
    visible_to: list[str] | None = None,
) -> None:
    """Persist a chunk's knowledge-graph nodes and relationships to Neo4j.

    Writes ``Person``, ``Entity``, ``Chunk``, and (for question chunks)
    ``Question`` nodes with their full property sets, plus the expertise-map,
    knowledge-connection, and routing-loop relationships described by the graph
    schema. All nodes use ``MERGE`` so re-ingestion is idempotent. The
    ``ROUTED_TO`` relationship is intentionally not written here: it is produced
    by the question-routing engine, not at ingestion time.

    Args:
        chunk: The chunk being stored.
        metadata: Extracted metadata describing the chunk's relationships.
        source: Origin of the conversation (e.g. whatsapp_export, email, excel).
        source_label: Human-readable label supplied at upload time.
        visible_to: Group names allowed to see this chunk/its entities.
    """

    visible = visible_to or []
    now = datetime.now(timezone.utc)
    start_time = chunk.start_time
    end_time = chunk.end_time

    asked = sorted({s.person for s in metadata.ownership if s.signal_type == "asked"})
    answered = sorted({s.person for s in metadata.ownership if s.signal_type == "answered"})
    mentioned = sorted(
        set(chunk.speakers) | {s.person for s in metadata.ownership if s.signal_type == "mentioned"}
    )
    owns_pairs = [
        {"person": s.person, "topic": s.topic}
        for s in metadata.ownership
        if s.signal_type == "owns" and s.topic
    ]

    all_people = sorted(
        set(mentioned) | set(asked) | set(answered) | {pair["person"] for pair in owns_pairs}
    )

    # Entities = LLM-listed entities plus any topics referenced by ownership
    # signals. Topics tied to a person signal are "primary", others "secondary".
    signal_topics = {s.topic for s in metadata.ownership if s.topic}
    all_entities = sorted({entity for entity in metadata.entities if entity} | signal_topics)
    entity_nodes = [{"name": name, "type": _DEFAULT_ENTITY_TYPE} for name in all_entities]
    relates = [
        {"name": name, "relevance": "primary" if name in signal_topics else "secondary"}
        for name in all_entities
    ]

    # Treat the chunk as a question when it is a Q&A or has explicit askers.
    is_question = metadata.knowledge_type == "question_answer" or bool(asked)
    question_id = f"q-{chunk.chunk_id}"
    resolved = bool(answered)
    asked_topics = sorted(
        {s.topic for s in metadata.ownership if s.signal_type == "asked" and s.topic}
    )
    question_entities = asked_topics or all_entities

    async def _write(tx) -> None:  # type: ignore[no-untyped-def]
        await tx.run(
            _CHUNK_NODE_CYPHER,
            org_id=org_id,
            chunk_id=chunk.chunk_id,
            raw_text=chunk.raw_text,
            summary=metadata.summary,
            knowledge_type=metadata.knowledge_type,
            confidence=metadata.confidence,
            confidence_reason=metadata.confidence_reason,
            source=source,
            source_label=source_label,
            visible_to=visible,
            start_time=start_time,
            end_time=end_time,
            created_at=now,
        )
        if all_people:
            await tx.run(_PEOPLE_CYPHER, org_id=org_id, people=all_people, ts=end_time)
        if entity_nodes:
            await tx.run(
                _ENTITIES_CYPHER, org_id=org_id, entities=entity_nodes, visible_to=visible
            )
        if relates:
            await tx.run(
                _RELATES_TO_CYPHER, org_id=org_id, chunk_id=chunk.chunk_id, rels=relates
            )
        if mentioned:
            await tx.run(
                _MENTIONED_IN_CYPHER,
                org_id=org_id,
                chunk_id=chunk.chunk_id,
                people=mentioned,
                ts=start_time,
            )
        if asked:
            await tx.run(
                _ASKED_CYPHER,
                org_id=org_id,
                chunk_id=chunk.chunk_id,
                people=asked,
                ts=start_time,
            )
        if answered:
            await tx.run(
                _ANSWERED_CYPHER,
                org_id=org_id,
                chunk_id=chunk.chunk_id,
                people=answered,
                ts=end_time,
                signal_type=_ANSWERED_SIGNAL_TYPE,
            )
        if owns_pairs:
            await tx.run(_OWNS_CYPHER, org_id=org_id, pairs=owns_pairs, ts=start_time)

        if is_question:
            await tx.run(
                _QUESTION_NODE_CYPHER,
                org_id=org_id,
                question_id=question_id,
                text=metadata.summary,
                status="resolved" if resolved else "unresolved",
                created_at=start_time,
                resolved_at=end_time if resolved else None,
                visible_to=visible,
            )
            if asked:
                await tx.run(
                    _QUESTION_ASKED_BY_CYPHER,
                    org_id=org_id,
                    question_id=question_id,
                    people=asked,
                )
            if question_entities:
                await tx.run(
                    _QUESTION_RELATED_TO_CYPHER,
                    org_id=org_id,
                    question_id=question_id,
                    entities=question_entities,
                )
            if resolved:
                await tx.run(
                    _QUESTION_ANSWERED_BY_CYPHER,
                    org_id=org_id,
                    question_id=question_id,
                    chunk_id=chunk.chunk_id,
                    ts=end_time,
                )

    driver = get_neo4j_driver()
    async with driver.session() as session:
        await session.execute_write(_write)

    logger.info("Saved chunk %s relationships to Neo4j", chunk.chunk_id)


# --- Directory (org setup) --------------------------------------------------

# Upsert Person nodes from a directory import, keyed on canonical_email. Every
# field from the expanded Person schema is set; identity/audit fields are minted
# on create only. ``source_ids`` is stored as a JSON string because Neo4j node
# properties cannot hold maps.
_DIRECTORY_UPSERT_CYPHER = """
UNWIND $people AS p
MERGE (person:Person {org_id: $org_id, canonical_email: p.canonical_email})
ON CREATE SET person.person_id = randomUUID(),
              person.org_id = $org_id,
              person.created_at = $now,
              person.is_system_user = false
SET person.email = p.email,
    person.user_id = coalesce(p.user_id, person.user_id),
    person.name = p.name,
    person.canonical_name = p.canonical_name,
    person.preferred_name = p.preferred_name,
    person.photo_url = p.photo_url,
    person.title = p.title,
    person.department = p.department,
    person.business_unit = p.business_unit,
    person.employee_type = p.employee_type,
    person.status = coalesce(p.status, 'active'),
    person.manager_email = p.manager_email,
    person.groups = p.groups,
    person.org_unit = p.org_unit,
    person.location = p.location,
    person.city = p.city,
    person.country = p.country,
    person.desk_location = p.desk_location,
    person.start_date = p.start_date,
    person.source_ids = p.source_ids,
    person.updated_at = $now
"""

# Wire reporting relationships. Rows whose manager isn't in the graph yield no
# match and are silently skipped (the person still imports, just unlinked).
_DIRECTORY_REPORTS_TO_CYPHER = """
UNWIND $links AS link
MATCH (p:Person {org_id: $org_id, canonical_email: link.canonical_email})
MATCH (m:Person {org_id: $org_id, canonical_email: link.manager_email})
MERGE (p)-[:REPORTS_TO]->(m)
SET p.manager_id = m.person_id
RETURN count(*) AS linked
"""


def _canonical(value: str | None) -> str | None:
    """Lower-case + strip a value for use as a canonical key, or None."""

    if value is None:
        return None
    cleaned = value.strip().lower()
    return cleaned or None


async def upsert_directory(
    people: list[DirectoryPerson],
    org_id: str,
    source: str = "csv",
) -> DirectoryIngestResult:
    """Upsert directory people into Neo4j and wire their reporting hierarchy.

    People are de-duplicated on ``canonical_email``. ``manager_email`` values are
    resolved to ``REPORTS_TO`` edges (and ``manager_id``) in a second pass once
    all nodes exist, so manager order within the file doesn't matter.

    Args:
        people: Parsed directory rows to upsert.
        source: Origin label recorded in ``source_ids`` (e.g. csv, google).

    Returns:
        A :class:`DirectoryIngestResult` with upsert and linkage counts.
    """

    now = datetime.now(timezone.utc)

    rows: list[dict] = []
    known_emails: set[str] = set()
    for person in people:
        canonical_email = _canonical(person.email)
        if not canonical_email:
            continue
        known_emails.add(canonical_email)
        # source_ids maps a source system to its id (external id, else email).
        source_ids = {source: person.user_id or person.email}
        rows.append(
            {
                "canonical_email": canonical_email,
                "email": person.email.strip(),
                "user_id": person.user_id,
                "name": person.name.strip(),
                "canonical_name": _canonical(person.name),
                "preferred_name": person.preferred_name,
                "photo_url": person.photo_url,
                "title": person.title,
                "department": person.department,
                "business_unit": person.business_unit,
                "employee_type": person.employee_type,
                "status": person.status,
                "manager_email": _canonical(person.manager_email),
                "groups": person.groups,
                "org_unit": person.org_unit,
                "location": person.location,
                "city": person.city,
                "country": person.country,
                "desk_location": person.desk_location,
                "start_date": person.start_date,
                "source_ids": json.dumps(source_ids),
            }
        )

    # Only attempt links where both endpoints are present in this import.
    links = [
        {"canonical_email": row["canonical_email"], "manager_email": row["manager_email"]}
        for row in rows
        if row["manager_email"] and row["manager_email"] in known_emails
        and row["manager_email"] != row["canonical_email"]
    ]

    reporting_links = 0

    async def _write(tx) -> int:  # type: ignore[no-untyped-def]
        linked = 0
        if rows:
            await tx.run(_DIRECTORY_UPSERT_CYPHER, org_id=org_id, people=rows, now=now)
        if links:
            result = await tx.run(
                _DIRECTORY_REPORTS_TO_CYPHER, org_id=org_id, links=links
            )
            record = await result.single()
            linked = record["linked"] if record else 0
        return linked

    driver = get_neo4j_driver()
    async with driver.session() as session:
        reporting_links = await session.execute_write(_write)

    departments = len({p.department.strip().lower() for p in people if p.department})
    groups = len({g.strip().lower() for p in people for g in p.groups if g})

    logger.info(
        "Directory import (%s): upserted %d people, %d reporting links",
        source,
        len(rows),
        reporting_links,
    )

    return DirectoryIngestResult(
        people_upserted=len(rows),
        departments=departments,
        groups=groups,
        reporting_links=reporting_links,
    )


# Public-facing org graph: directory people (those with an email) and their
# REPORTS_TO edges. Internal fields (user_id, source_ids, ...) are not selected.
_ORG_GRAPH_CYPHER = """
MATCH (p:Person {org_id: $org_id})
WHERE p.canonical_email IS NOT NULL
OPTIONAL MATCH (p)-[:REPORTS_TO]->(m:Person {org_id: $org_id})
RETURN p.person_id AS id,
       p.name AS name,
       p.preferred_name AS preferred_name,
       p.email AS email,
       p.title AS title,
       p.department AS department,
       p.business_unit AS business_unit,
       p.photo_url AS photo_url,
       p.location AS location,
       p.city AS city,
       p.country AS country,
       coalesce(p.groups, []) AS groups,
       p.status AS status,
       p.start_date AS start_date,
       m.person_id AS manager_id
ORDER BY name
"""


async def fetch_org_graph(org_id: str) -> OrgGraphResponse:
    """Return the org chart: public person profiles + REPORTS_TO edges."""

    async def _read(tx) -> list[dict]:  # type: ignore[no-untyped-def]
        result = await tx.run(_ORG_GRAPH_CYPHER, org_id=org_id)
        return [record.data() async for record in result]

    driver = get_neo4j_driver()
    async with driver.session() as session:
        records = await session.execute_read(_read)

    people = [OrgPerson(**record) for record in records]
    edges = [
        OrgEdge(source=record["id"], target=record["manager_id"])
        for record in records
        if record.get("manager_id")
    ]
    logger.info("Org graph: %d people, %d reporting edges", len(people), len(edges))
    return OrgGraphResponse(people=people, edges=edges)
