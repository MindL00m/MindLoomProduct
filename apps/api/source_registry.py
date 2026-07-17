"""Lifecycle registry for externally managed files and messages."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import DateTime, String, Text, UniqueConstraint, delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Mapped, mapped_column

from auth import Base
from database import get_neo4j_driver, get_session_factory
from documents import compute_content_hash
from models import Conversation, IngestionResult
from pipeline import DocumentInput, run_ingestion
from storage import ChunkRow


class ExternalSourceRow(Base):
    __tablename__ = "external_sources"
    __table_args__ = (
        UniqueConstraint(
            "org_id", "provider", "external_id", name="uq_external_source_identity"
        ),
    )

    source_key: Mapped[str] = mapped_column(String, primary_key=True)
    org_id: Mapped[str] = mapped_column(String, nullable=False)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    external_id: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    document_id: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    visible_to_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


async def get_external_source(
    org_id: str, provider: str, external_id: str
) -> ExternalSourceRow | None:
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(ExternalSourceRow).where(
                ExternalSourceRow.org_id == org_id,
                ExternalSourceRow.provider == provider,
                ExternalSourceRow.external_id == external_id,
            )
        )
        return result.scalar_one_or_none()


async def delete_document_version(org_id: str, document_id: str | None) -> None:
    """Remove the old document and all search/graph chunks derived from it."""

    if not document_id:
        return
    driver = get_neo4j_driver()
    async with driver.session() as graph:
        result = await graph.run(
            """
            MATCH (c:Chunk {org_id: $org_id})-[:DERIVED_FROM]->
                  (d:Document {org_id: $org_id, document_id: $document_id})
            RETURN collect(c.chunk_id) AS chunk_ids
            """,
            org_id=org_id,
            document_id=document_id,
        )
        record = await result.single()
        chunk_ids = list(record.get("chunk_ids") or []) if record else []

    if chunk_ids:
        factory = get_session_factory()
        async with factory() as session:
            async with session.begin():
                await session.execute(delete(ChunkRow).where(ChunkRow.chunk_id.in_(chunk_ids)))

    async with driver.session() as graph:
        await graph.run(
            """
            MATCH (d:Document {org_id: $org_id, document_id: $document_id})
            OPTIONAL MATCH (c:Chunk {org_id: $org_id})-[:DERIVED_FROM]->(d)
            DETACH DELETE c, d
            """,
            org_id=org_id,
            document_id=document_id,
        )


async def ingest_external_source(
    *,
    org_id: str,
    provider: str,
    external_id: str,
    version: str | None,
    conversation: Conversation,
    document: DocumentInput,
) -> IngestionResult | None:
    """Idempotently ingest a source version and retire the previous version."""

    content_hash = compute_content_hash(document.data)
    current = await get_external_source(org_id, provider, external_id)
    if (
        current
        and current.status == "active"
        and current.content_hash == content_hash
        and (not version or current.version == version)
    ):
        return None

    result = await run_ingestion(conversation, org_id, document=document)
    from documents import Neo4jDocumentRepository

    stored = await Neo4jDocumentRepository().find_by_content_hash(org_id, content_hash)
    if stored is None:
        raise RuntimeError("Ingested document was not persisted.")

    now = datetime.now(timezone.utc)
    old_document_id = current.document_id if current else None
    factory = get_session_factory()
    async with factory() as session:
        async with session.begin():
            stmt = pg_insert(ExternalSourceRow).values(
                source_key=str(uuid4()),
                org_id=org_id,
                provider=provider,
                external_id=external_id,
                version=version,
                content_hash=content_hash,
                document_id=stored.document_id,
                status="active",
                visible_to_json=__import__("json").dumps(document.visible_to),
                last_seen_at=now,
                deleted_at=None,
                created_at=now,
                updated_at=now,
            ).on_conflict_do_update(
                constraint="uq_external_source_identity",
                set_={
                    "version": version,
                    "content_hash": content_hash,
                    "document_id": stored.document_id,
                    "status": "active",
                    "visible_to_json": __import__("json").dumps(document.visible_to),
                    "last_seen_at": now,
                    "deleted_at": None,
                    "updated_at": now,
                },
            )
            await session.execute(stmt)

    if old_document_id and old_document_id != stored.document_id:
        await delete_document_version(org_id, old_document_id)
    return result


async def mark_external_source_deleted(
    org_id: str, provider: str, external_id: str
) -> bool:
    current = await get_external_source(org_id, provider, external_id)
    if current is None or current.status == "deleted":
        return False
    await delete_document_version(org_id, current.document_id)
    now = datetime.now(timezone.utc)
    factory = get_session_factory()
    async with factory() as session:
        current = await session.get(ExternalSourceRow, current.source_key)
        if current:
            current.status = "deleted"
            current.document_id = None
            current.deleted_at = now
            current.updated_at = now
            await session.commit()
    return True
