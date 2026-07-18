"""Persistence layer for chunks: PostgreSQL (pgvector) and Neo4j.

PostgreSQL stores the raw chunk text, metadata, and the embedding vector via the
SQLAlchemy ORM. Neo4j stores the knowledge-graph relationships. Both use the
shared, pooled connections from :mod:`database`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from pgvector.sqlalchemy import Vector
from sqlalchemy import ARRAY, DateTime, String, Text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from database import get_neo4j_driver, get_session_factory
from embedder import EMBEDDING_DIMENSIONS
from models import (
    ActionItemUpdate,
    Chunk,
    ChunkMetadata,
    DirectoryIngestResult,
    DirectoryPerson,
    GraphDebugEdge,
    GraphDebugNode,
    IssueUpdate,
    KnowledgeGraphResponse,
    OrgEdge,
    OrgGraphResponse,
    OrgPerson,
    ProjectUpdate,
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
    visible_to: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ChunkEmbeddingRow(Base):
    """ORM mapping for the ``chunk_embeddings`` table (pgvector)."""

    __tablename__ = "chunk_embeddings"

    chunk_id: Mapped[str] = mapped_column(String, primary_key=True)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMENSIONS), nullable=False)


async def save_to_postgres(
    chunk: Chunk,
    metadata: ChunkMetadata,
    embedding: list[float],
    org_id: str,
    visible_to: list[str] | None = None,
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
        "visible_to": visible_to or [],
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
MERGE (p:Person {org_id: $org_id, canonical_name: toLower(trim(name))})
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
MERGE (e:Entity {org_id: $org_id, canonical_name: ent.canonical_name})
ON CREATE SET e.entity_id = randomUUID(),
              e.org_id = $org_id,
              e.name = ent.name,
              e.type = ent.type,
              e.work_status = CASE WHEN ent.type = 'project' THEN 'open' ELSE null END,
              e.last_signal_at = CASE WHEN ent.type = 'project' THEN $ts ELSE null END
SET e.type = ent.type,
    e.visible_to = $visible_to,
    e.work_status = CASE
        WHEN ent.type = 'project' AND e.work_status IS NULL THEN 'open'
        ELSE e.work_status
    END,
    e.last_signal_at = CASE
        WHEN ent.type = 'project' THEN coalesce($ts, e.last_signal_at)
        ELSE e.last_signal_at
    END
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
MATCH (p:Person {org_id: $org_id, canonical_name: toLower(trim(name))})
MERGE (p)-[r:MENTIONED_IN]->(c)
SET r.timestamp = $ts
"""

_RELATES_TO_CYPHER = """
MATCH (c:Chunk {chunk_id: $chunk_id, org_id: $org_id})
UNWIND $rels AS rel
MATCH (e:Entity {org_id: $org_id, canonical_name: rel.canonical_name})
MERGE (c)-[r:RELATES_TO]->(e)
SET r.relevance = rel.relevance
"""

_ASKED_CYPHER = """
MATCH (c:Chunk {chunk_id: $chunk_id, org_id: $org_id})
UNWIND $people AS name
MATCH (p:Person {org_id: $org_id, canonical_name: toLower(trim(name))})
MERGE (p)-[r:ASKED]->(c)
SET r.timestamp = $ts
"""

_ANSWERED_CYPHER = """
MATCH (c:Chunk {chunk_id: $chunk_id, org_id: $org_id})
UNWIND $people AS name
MATCH (p:Person {org_id: $org_id, canonical_name: toLower(trim(name))})
MERGE (p)-[r:ANSWERED]->(c)
SET r.timestamp = $ts, r.signal_type = $signal_type
"""

_OWNS_CYPHER = """
UNWIND $pairs AS pair
MATCH (p:Person {org_id: $org_id, canonical_name: toLower(trim(pair.person))})
MATCH (e:Entity {org_id: $org_id, canonical_name: pair.canonical_topic})
MERGE (p)-[r:OWNS]->(e)
ON CREATE SET r.since = $ts
SET r.confirmed = false,
    r.visible_to = $visible_to
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
MATCH (p:Person {org_id: $org_id, canonical_name: toLower(trim(name))})
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

_KNOWLEDGE_RECORDS_CYPHER = """
MATCH (c:Chunk {chunk_id: $chunk_id, org_id: $org_id})
FOREACH (item IN $decisions |
  MERGE (d:Decision {decision_id: item.id})
  SET d.org_id = $org_id, d.text = item.text, d.decided_at = $timestamp,
      d.confidence = $confidence, d.visible_to = $visible_to
  MERGE (d)-[:SUPPORTED_BY]->(c)
)
FOREACH (item IN $claims |
  MERGE (f:Claim {claim_id: item.id})
  SET f.org_id = $org_id, f.text = item.text, f.observed_at = $timestamp,
      f.confidence = $confidence, f.valid_until = $valid_until,
      f.visible_to = $visible_to
  MERGE (f)-[:SUPPORTED_BY]->(c)
)
"""

_PROJECT_UPDATES_CYPHER = """
UNWIND $updates AS u
MERGE (e:Entity {org_id: $org_id, canonical_name: u.canonical_name})
ON CREATE SET e.entity_id = randomUUID(),
              e.org_id = $org_id,
              e.name = u.name,
              e.type = 'project',
              e.work_status = u.work_status
SET e.type = 'project',
    e.name = u.name,
    e.work_status = u.work_status,
    e.last_signal_at = $ts,
    e.closed_at = CASE WHEN u.work_status = 'closed' THEN $ts ELSE e.closed_at END,
    e.visible_to = $visible_to
WITH e
MATCH (c:Chunk {chunk_id: $chunk_id, org_id: $org_id})
MERGE (c)-[r:RELATES_TO]->(e)
SET r.relevance = 'primary'
"""

_ACTION_ITEM_UPDATES_CYPHER = """
UNWIND $updates AS u
MERGE (a:ActionItem {org_id: $org_id, canonical_key: u.canonical_key})
ON CREATE SET a.action_item_id = u.id,
              a.text = u.text,
              a.status = u.status,
              a.created_at = $ts,
              a.visible_to = $visible_to
SET a.text = u.text,
    a.status = u.status,
    a.last_signal_at = $ts,
    a.assignee = coalesce(u.assignee, a.assignee),
    a.visible_to = $visible_to,
    a.closed_at = CASE
        WHEN u.status IN ['done', 'cancelled'] THEN $ts
        ELSE a.closed_at
    END
WITH a, u
MATCH (c:Chunk {chunk_id: $chunk_id, org_id: $org_id})
MERGE (a)-[:EVIDENCED_BY]->(c)
WITH a, u
OPTIONAL MATCH (e:Entity {org_id: $org_id, canonical_name: u.canonical_project})
FOREACH (_ IN CASE WHEN e IS NOT NULL THEN [1] ELSE [] END |
  MERGE (a)-[:PART_OF]->(e)
)
WITH a, u
OPTIONAL MATCH (p:Person {org_id: $org_id, canonical_name: toLower(trim(u.assignee))})
WHERE u.assignee IS NOT NULL AND trim(u.assignee) <> ''
FOREACH (_ IN CASE WHEN p IS NOT NULL THEN [1] ELSE [] END |
  MERGE (a)-[:ASSIGNED_TO]->(p)
)
"""

_OPEN_ISSUE_UPDATES_CYPHER = """
UNWIND $updates AS u
MERGE (i:OpenIssue {org_id: $org_id, canonical_key: u.canonical_key})
ON CREATE SET i.issue_id = u.id,
              i.title = u.title,
              i.kind = u.kind,
              i.status = u.status,
              i.created_at = $ts,
              i.visible_to = $visible_to
SET i.title = u.title,
    i.kind = u.kind,
    i.status = u.status,
    i.last_seen_at = $ts,
    i.visible_to = $visible_to,
    i.closed_at = CASE WHEN u.status = 'closed' THEN $ts ELSE i.closed_at END
WITH i, u
MATCH (c:Chunk {chunk_id: $chunk_id, org_id: $org_id})
MERGE (i)-[:EVIDENCED_BY]->(c)
WITH i, u
OPTIONAL MATCH (e:Entity {org_id: $org_id, canonical_name: u.canonical_project})
FOREACH (_ IN CASE WHEN e IS NOT NULL THEN [1] ELSE [] END |
  MERGE (i)-[:ABOUT]->(e)
)
"""


def _canonical_key(text: str) -> str:
    """Stable key for matching action items / issues across chunks."""

    normalized = re.sub(r"\s+", " ", text.strip().lower())
    return normalized


def _stable_id(prefix: str, org_id: str, key: str) -> str:
    digest = hashlib.sha1(f"{org_id}:{key}".encode("utf-8")).hexdigest()[:16]
    return f"{prefix}:{digest}"


def _coerce_action_updates(metadata: ChunkMetadata) -> list[ActionItemUpdate]:
    """Merge structured updates with legacy bare action_items strings."""

    updates = list(metadata.action_item_updates)
    seen = {_canonical_key(item.text) for item in updates if item.text.strip()}
    for text in metadata.action_items:
        key = _canonical_key(text)
        if not key or key in seen:
            continue
        updates.append(ActionItemUpdate(text=text, status="open"))
        seen.add(key)
    return [item for item in updates if item.text.strip()]


def _coerce_issue_updates(metadata: ChunkMetadata) -> list[IssueUpdate]:
    """Use explicit issue updates, or synthesize from knowledge_type."""

    updates = [item for item in metadata.issue_updates if item.title.strip()]
    if updates:
        return updates
    if metadata.knowledge_type in ("problem_report", "status_update"):
        title = (metadata.summary or "").strip() or metadata.knowledge_type.replace("_", " ")
        return [
            IssueUpdate(
                title=title,
                kind=metadata.knowledge_type,  # type: ignore[arg-type]
                status="open",
            )
        ]
    return []


def _coerce_project_updates(
    metadata: ChunkMetadata, entity_nodes: list[dict]
) -> list[ProjectUpdate]:
    updates = [item for item in metadata.project_updates if item.name.strip()]
    if updates:
        return updates
    # Mentioned project entities default to open until a close signal arrives.
    return [
        ProjectUpdate(name=str(ent["name"]), work_status="open")
        for ent in entity_nodes
        if ent.get("type") == "project" and ent.get("name")
    ]


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
        {
            "person": s.person,
            "topic": s.topic,
            "canonical_topic": s.topic.strip().lower(),
        }
        for s in metadata.ownership
        if s.signal_type == "owns" and s.topic
    ]

    all_people = sorted(
        set(mentioned) | set(asked) | set(answered) | {pair["person"] for pair in owns_pairs}
    )

    # Entities = LLM-listed entities plus any topics referenced by ownership
    # signals. Topics tied to a person signal are "primary", others "secondary".
    signal_topics = {s.topic for s in metadata.ownership if s.topic}
    typed = {entity.name: entity for entity in metadata.typed_entities if entity.name}
    all_entities = sorted({entity for entity in metadata.entities if entity} | signal_topics | set(typed))
    entity_nodes = [
        {
            "name": name,
            "canonical_name": name.strip().lower(),
            "type": typed[name].type if name in typed else _DEFAULT_ENTITY_TYPE,
        }
        for name in all_entities
    ]
    relates = [
        {
            "name": name,
            "canonical_name": name.strip().lower(),
            "relevance": (
                typed[name].relevance
                if name in typed
                else ("primary" if name in signal_topics else "secondary")
            ),
        }
        for name in all_entities
    ]

    # Treat the chunk as a question when it is a Q&A or has explicit askers.
    is_question = metadata.knowledge_type == "question_answer" or bool(asked)
    question_id = f"q-{chunk.chunk_id}"
    resolved = bool(answered)
    asked_topics = sorted(
        {s.topic for s in metadata.ownership if s.signal_type == "asked" and s.topic}
    )
    question_entities = [
        name.strip().lower() for name in (asked_topics or all_entities)
    ]

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
                _ENTITIES_CYPHER,
                org_id=org_id,
                entities=entity_nodes,
                visible_to=visible,
                ts=end_time,
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
            await tx.run(
                _OWNS_CYPHER,
                org_id=org_id,
                pairs=owns_pairs,
                ts=start_time,
                visible_to=visible,
            )

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
        if metadata.decisions or metadata.factual_claims:
            await tx.run(
                _KNOWLEDGE_RECORDS_CYPHER,
                org_id=org_id,
                chunk_id=chunk.chunk_id,
                decisions=[
                    {"id": f"decision:{chunk.chunk_id}:{index}", "text": value}
                    for index, value in enumerate(metadata.decisions)
                ],
                claims=[
                    {"id": f"claim:{chunk.chunk_id}:{index}", "text": value}
                    for index, value in enumerate(metadata.factual_claims)
                ],
                timestamp=end_time,
                confidence=metadata.confidence,
                valid_until=metadata.valid_until,
                visible_to=visible,
            )

        project_updates = _coerce_project_updates(metadata, entity_nodes)
        if project_updates:
            await tx.run(
                _PROJECT_UPDATES_CYPHER,
                org_id=org_id,
                chunk_id=chunk.chunk_id,
                updates=[
                    {
                        "name": item.name.strip(),
                        "canonical_name": item.name.strip().lower(),
                        "work_status": item.work_status,
                    }
                    for item in project_updates
                ],
                ts=end_time,
                visible_to=visible,
            )

        action_updates = _coerce_action_updates(metadata)
        if action_updates:
            await tx.run(
                _ACTION_ITEM_UPDATES_CYPHER,
                org_id=org_id,
                chunk_id=chunk.chunk_id,
                updates=[
                    {
                        "id": _stable_id("action", org_id, _canonical_key(item.text)),
                        "text": item.text.strip(),
                        "canonical_key": _canonical_key(item.text),
                        "status": item.status,
                        "assignee": item.assignee,
                        "canonical_project": (
                            item.project.strip().lower() if item.project else None
                        ),
                    }
                    for item in action_updates
                ],
                ts=end_time,
                visible_to=visible,
            )

        issue_updates = _coerce_issue_updates(metadata)
        if issue_updates:
            await tx.run(
                _OPEN_ISSUE_UPDATES_CYPHER,
                org_id=org_id,
                chunk_id=chunk.chunk_id,
                updates=[
                    {
                        "id": _stable_id("issue", org_id, _canonical_key(item.title)),
                        "title": item.title.strip(),
                        "canonical_key": _canonical_key(item.title),
                        "kind": item.kind,
                        "status": item.status,
                        "canonical_project": (
                            item.project.strip().lower() if item.project else None
                        ),
                    }
                    for item in issue_updates
                ],
                ts=end_time,
                visible_to=visible,
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


_GRAPH_NODE_CAP = 400

_KG_NODES_CYPHER = """
MATCH (n {org_id: $org_id})
WHERE n:Person OR n:Chunk OR n:Document OR n:Entity OR n:Question
   OR n:Decision OR n:ActionItem OR n:Claim OR n:OpenIssue
RETURN n, labels(n) AS labels
LIMIT $limit
"""

_KG_EDGES_CYPHER = """
MATCH (a {org_id: $org_id})-[r]->(b)
WHERE b.org_id = $org_id
  AND (
    a:Person OR a:Chunk OR a:Document OR a:Entity OR a:Question
    OR a:Decision OR a:ActionItem OR a:Claim OR a:OpenIssue
  )
  AND (
    b:Person OR b:Chunk OR b:Document OR b:Entity OR b:Question
    OR b:Decision OR b:ActionItem OR b:Claim OR b:OpenIssue
  )
RETURN a, labels(a) AS a_labels, type(r) AS rel_type, properties(r) AS rel_props,
       b, labels(b) AS b_labels
"""


def _serialize_graph_value(value: object) -> object:
    """Convert Neo4j driver values into JSON-safe Python objects."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()  # type: ignore[union-attr]
    if isinstance(value, list):
        return [_serialize_graph_value(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _serialize_graph_value(v) for k, v in value.items()}
    return str(value)


def _serialize_props(props: dict) -> dict[str, object]:
    return {str(k): _serialize_graph_value(v) for k, v in props.items()}


def _graph_node_id(labels: list[str], props: dict) -> str | None:
    """Derive a stable id for a knowledge-graph node."""

    if "Person" in labels:
        return (
            props.get("person_id")
            or props.get("canonical_email")
            or props.get("canonical_name")
        )
    if "Chunk" in labels:
        return props.get("chunk_id")
    if "Document" in labels:
        return props.get("document_id")
    if "Entity" in labels:
        return props.get("entity_id") or props.get("canonical_name")
    if "Question" in labels:
        return props.get("question_id")
    if "Decision" in labels:
        return props.get("decision_id")
    if "ActionItem" in labels:
        return props.get("action_item_id")
    if "Claim" in labels:
        return props.get("claim_id")
    if "OpenIssue" in labels:
        return props.get("issue_id") or props.get("canonical_key")
    return None


async def fetch_knowledge_graph_debug(org_id: str) -> KnowledgeGraphResponse:
    """Export all org-scoped knowledge-graph nodes and outgoing edges for debug UI."""

    async def _read(tx) -> tuple[list[dict], list[dict]]:  # type: ignore[no-untyped-def]
        node_result = await tx.run(_KG_NODES_CYPHER, org_id=org_id, limit=_GRAPH_NODE_CAP + 1)
        node_records = [record.data() async for record in node_result]
        edge_result = await tx.run(_KG_EDGES_CYPHER, org_id=org_id)
        edge_records = [record.data() async for record in edge_result]
        return node_records, edge_records

    driver = get_neo4j_driver()
    async with driver.session() as session:
        node_records, edge_records = await session.execute_read(_read)

    truncated = len(node_records) > _GRAPH_NODE_CAP
    if truncated:
        node_records = node_records[:_GRAPH_NODE_CAP]

    nodes: list[GraphDebugNode] = []
    known_ids: set[str] = set()
    for record in node_records:
        raw = dict(record["n"])
        labels = list(record["labels"])
        node_id = _graph_node_id(labels, raw)
        if not node_id:
            continue
        known_ids.add(node_id)
        nodes.append(
            GraphDebugNode(
                id=node_id,
                labels=labels,
                properties=_serialize_props(raw),
            )
        )

    edges: list[GraphDebugEdge] = []
    seen_edges: set[str] = set()
    for idx, record in enumerate(edge_records):
        a_labels = list(record["a_labels"])
        b_labels = list(record["b_labels"])
        a_props = dict(record["a"])
        b_props = dict(record["b"])
        source = _graph_node_id(a_labels, a_props)
        target = _graph_node_id(b_labels, b_props)
        if not source or not target or source not in known_ids or target not in known_ids:
            continue
        rel_type = record["rel_type"]
        edge_id = f"{source}->{rel_type}->{target}:{idx}"
        if edge_id in seen_edges:
            continue
        seen_edges.add(edge_id)
        edges.append(
            GraphDebugEdge(
                id=edge_id,
                source=source,
                target=target,
                type=rel_type,
                properties=_serialize_props(dict(record["rel_props"])),
            )
        )

    logger.info(
        "Knowledge graph debug: %d nodes, %d edges (truncated=%s)",
        len(nodes),
        len(edges),
        truncated,
    )
    return KnowledgeGraphResponse(nodes=nodes, edges=edges, truncated=truncated)
