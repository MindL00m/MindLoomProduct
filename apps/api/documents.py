"""Document storage layer: blob persistence + the graph's citation anchors.

This module owns everything about :class:`~models.Document` nodes and the
``(Chunk)-[:DERIVED_FROM]->(Document)`` relationship that lets a chunk cite the
exact slice of its source it came from.

Responsibilities:

* :func:`store_document` — content-hash a raw file, de-duplicate re-uploads, and
  persist the blob + a ``Document`` node (idempotent).
* :func:`link_chunk_to_document` — attach a chunk to its source with a locator
  (char offsets / page / row range).
* :func:`get_citation` — join ``Chunk -> DERIVED_FROM -> Document`` into a
  ready-to-render :class:`~models.Citation`.

Graph persistence sits behind :class:`DocumentRepository` so it can be backed by
Neo4j in production or an in-memory fake in tests, and blob persistence sits
behind :class:`~blob_storage.BlobStorage`. Neither this module's callers nor its
tests need a live database.

NOTE: This intentionally does *not* wire itself into the ingestion pipeline yet;
it only provides the schema, storage, and citation primitives.
"""

from __future__ import annotations

import hashlib
import logging
import re
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional, Sequence
from uuid import uuid4

from blob_storage import BlobStorage, get_blob_storage
from models import Chunk, Citation, DerivedFrom, Document, DocumentStoreResult

logger = logging.getLogger(__name__)

# How a chunk's position within its document is addressed, by source family.
LocatorKind = Literal["char", "page", "row"]

# Spreadsheet-like sources locate chunks by row range.
_ROW_SOURCES = frozenset(
    {"excel", "xlsx", "xls", "spreadsheet", "csv", "google_sheets", "sheets"}
)
# Paginated/slide sources locate chunks by page (1-based).
_PAGE_SOURCES = frozenset(
    {"pptx", "ppt", "powerpoint", "pdf", "slides", "deck", "keynote"}
)


def locator_kind_for_source(source: str) -> LocatorKind:
    """Return how chunks of a given source type are located within their document.

    Defaults to character offsets, which suit free-text / conversational sources
    (whatsapp_export, email, slack, ...).
    """

    normalized = source.strip().lower()
    if normalized in _ROW_SOURCES:
        return "row"
    if normalized in _PAGE_SOURCES:
        return "page"
    return "char"


def document_extracted_text(chunks: Sequence[Chunk]) -> str:
    """Return the canonical extracted text that char offsets are relative to.

    Chunks partition the document's messages in order, so the document's
    extracted text is exactly the chunk raw texts joined by newlines — which is
    what :func:`compute_chunk_locators` uses to assign character offsets.
    """

    return "\n".join(chunk.raw_text for chunk in chunks)


def compute_chunk_locators(
    chunks: Sequence[Chunk], source: str
) -> list[DerivedFrom]:
    """Compute one :class:`DerivedFrom` locator per chunk, by source type.

    * ``char`` sources: ``char_start``/``char_end`` offsets into the document's
      extracted text (see :func:`document_extracted_text`).
    * ``row`` sources: inclusive 1-based ``row_range`` (each message is a row).
    * ``page`` sources: 1-based ``page_number`` (each chunk maps to a page/slide).

    The returned list is positionally aligned with ``chunks``.
    """

    kind = locator_kind_for_source(source)
    locators: list[DerivedFrom] = []

    if kind == "char":
        offset = 0
        for chunk in chunks:
            length = len(chunk.raw_text)
            section = re.search(r"\[Section:\s*([^\]]+)\]", chunk.raw_text)
            table_cell = re.search(r"\[(Table \d+ cell R\d+C\d+)\]", chunk.raw_text)
            locators.append(DerivedFrom(
                char_start=offset, char_end=offset + length,
                section_title=section.group(1) if section else None,
                table_cell=table_cell.group(1) if table_cell else None,
            ))
            # +1 for the newline that joins this chunk to the next in the doc text.
            offset += length + 1
    elif kind == "row":
        row = 1
        for chunk in chunks:
            count = len(chunk.messages)
            sheet = re.search(r"\[Sheet:\s*([^\]]+)\]", chunk.raw_text)
            cells = re.findall(r"\[([A-Z]+\d+)\]", chunk.raw_text)
            locators.append(DerivedFrom(
                row_range=(row, row + max(count, 1) - 1),
                sheet_name=sheet.group(1) if sheet else None,
                cell_range=f"{cells[0]}:{cells[-1]}" if cells else None,
            ))
            row += max(count, 1)
    else:  # page
        for index, _chunk in enumerate(chunks):
            locators.append(DerivedFrom(page_number=index + 1))

    return locators


class CitationNotFoundError(LookupError):
    """Raised when a chunk has no ``DERIVED_FROM`` document to cite."""

    def __init__(self, chunk_id: str) -> None:
        super().__init__(f"No source document found for chunk {chunk_id!r}")
        self.chunk_id = chunk_id


def compute_content_hash(data: bytes) -> str:
    """Return the SHA-256 hex digest of ``data`` (the document de-dup key)."""

    return hashlib.sha256(data).hexdigest()


def _storage_key(org_id: str, content_hash: str, original_filename: str | None) -> str:
    """Build a content-addressed blob key scoped to an organization."""

    suffix = Path(original_filename).suffix.lower() if original_filename else ""
    return f"{org_id}/{content_hash[:2]}/{content_hash}{suffix}"


# --- Repository abstraction -------------------------------------------------


class DocumentRepository(ABC):
    """Graph persistence for documents and their chunk links."""

    @abstractmethod
    async def find_by_content_hash(
        self, org_id: str, content_hash: str
    ) -> Optional[Document]:
        """Return an existing document with this hash within ``org_id``, or ``None``."""

    @abstractmethod
    async def create(self, document: Document) -> Document:
        """Persist ``document``, idempotent on ``content_hash``.

        If a document with the same hash already exists it is returned unchanged
        (so the caller can detect de-dup by comparing ``document_id``).
        """

    @abstractmethod
    async def link_chunk(
        self, chunk_id: str, document_id: str, locator: DerivedFrom, org_id: str
    ) -> None:
        """Create/Update a ``DERIVED_FROM`` edge from a chunk to a document."""

    @abstractmethod
    async def get_citation(self, chunk_id: str, org_id: str) -> Optional[Citation]:
        """Return the citation for ``chunk_id``, or ``None`` if unlinked."""

    @abstractmethod
    async def count_chunks_for_document(self, document_id: str) -> int:
        """Return how many chunks are linked to ``document_id`` via DERIVED_FROM."""


def _to_datetime(value: object) -> datetime:
    """Coerce a Neo4j temporal (or datetime) into a native ``datetime``."""

    if isinstance(value, datetime):
        return value
    to_native = getattr(value, "to_native", None)
    if callable(to_native):
        return to_native()  # type: ignore[no-any-return]
    raise TypeError(f"Cannot coerce {value!r} to datetime")


def _document_from_record(record: dict) -> Document:
    """Build a :class:`Document` from a Neo4j result row."""

    return Document(
        document_id=record["document_id"],
        org_id=record["org_id"],
        source=record["source"],
        source_label=record["source_label"],
        original_filename=record.get("original_filename"),
        title=record.get("title"),
        author=record.get("author"),
        owners=list(record.get("owners") or []),
        source_created_at=_to_datetime(record["source_created_at"]) if record.get("source_created_at") else None,
        source_updated_at=_to_datetime(record["source_updated_at"]) if record.get("source_updated_at") else None,
        source_application=record.get("source_application"),
        source_location=record.get("source_location"),
        department=record.get("department"),
        project=record.get("project"),
        folder_path=record.get("folder_path"),
        version=record.get("version"),
        contributors=list(record.get("contributors") or []),
        permissions=list(record.get("permissions") or []),
        source_url=record.get("source_url"),
        storage_path=record["storage_path"],
        content_hash=record["content_hash"],
        mime_type=record["mime_type"],
        uploaded_by=record.get("uploaded_by"),
        visible_to=list(record.get("visible_to") or []),
        uploaded_at=_to_datetime(record["uploaded_at"]),
        status=record["status"],
    )


class Neo4jDocumentRepository(DocumentRepository):
    """Neo4j-backed document repository using the shared async driver."""

    _RETURN_FIELDS = """
        d.document_id AS document_id,
        d.org_id AS org_id,
        d.source AS source,
        d.source_label AS source_label,
        d.original_filename AS original_filename,
        d.title AS title,
        d.author AS author,
        d.owners AS owners,
        d.source_created_at AS source_created_at,
        d.source_updated_at AS source_updated_at,
        d.source_application AS source_application,
        d.source_location AS source_location,
        d.department AS department,
        d.project AS project,
        d.folder_path AS folder_path,
        d.version AS version,
        d.contributors AS contributors,
        d.permissions AS permissions,
        d.source_url AS source_url,
        d.storage_path AS storage_path,
        d.content_hash AS content_hash,
        d.mime_type AS mime_type,
        d.uploaded_by AS uploaded_by,
        d.visible_to AS visible_to,
        d.uploaded_at AS uploaded_at,
        d.status AS status
    """

    _FIND_CYPHER = f"""
    MATCH (d:Document {{org_id: $org_id, content_hash: $content_hash}})
    RETURN {_RETURN_FIELDS}
    LIMIT 1
    """

    _CREATE_CYPHER = f"""
    MERGE (d:Document {{org_id: $org_id, content_hash: $content_hash}})
    ON CREATE SET
        d.document_id = $document_id,
        d.org_id = $org_id,
        d.source = $source,
        d.source_label = $source_label,
        d.original_filename = $original_filename,
        d.title = $title,
        d.author = $author,
        d.owners = $owners,
        d.source_created_at = $source_created_at,
        d.source_updated_at = $source_updated_at,
        d.source_application = $source_application,
        d.source_location = $source_location,
        d.department = $department,
        d.project = $project,
        d.folder_path = $folder_path,
        d.version = $version,
        d.contributors = $contributors,
        d.permissions = $permissions,
        d.source_url = $source_url,
        d.storage_path = $storage_path,
        d.mime_type = $mime_type,
        d.uploaded_by = $uploaded_by,
        d.visible_to = $visible_to,
        d.uploaded_at = $uploaded_at,
        d.status = $status
    RETURN {_RETURN_FIELDS}
    """

    _LINK_CYPHER = """
    MATCH (c:Chunk {chunk_id: $chunk_id, org_id: $org_id})
    MATCH (d:Document {document_id: $document_id, org_id: $org_id})
    MERGE (c)-[r:DERIVED_FROM]->(d)
    SET r.char_start = $char_start,
        r.char_end = $char_end,
        r.page_number = $page_number,
        r.page_start = $page_start,
        r.page_end = $page_end,
        r.row_range = $row_range,
        r.section_title = $section_title,
        r.table_cell = $table_cell,
        r.sheet_name = $sheet_name,
        r.cell_range = $cell_range
    RETURN count(r) AS linked
    """

    _CITATION_CYPHER = """
    MATCH (c:Chunk {chunk_id: $chunk_id, org_id: $org_id})-[r:DERIVED_FROM]->(d:Document)
    RETURN c.chunk_id AS chunk_id,
           d.document_id AS document_id,
           d.source AS source,
           d.source_label AS source_label,
           d.original_filename AS original_filename,
           d.source_url AS source_url,
           d.author AS author,
           d.source_updated_at AS source_updated_at,
           d.version AS version,
           r.char_start AS char_start,
           r.char_end AS char_end,
           r.page_number AS page_number,
           r.page_start AS page_start,
           r.page_end AS page_end,
           r.row_range AS row_range
           ,r.section_title AS section_title
           ,r.table_cell AS table_cell
           ,r.sheet_name AS sheet_name
           ,r.cell_range AS cell_range
    LIMIT 1
    """

    _COUNT_CHUNKS_CYPHER = """
    MATCH (:Chunk)-[r:DERIVED_FROM]->(:Document {document_id: $document_id})
    RETURN count(r) AS n
    """

    def _driver(self):  # type: ignore[no-untyped-def]
        # Imported lazily so importing this module never requires a live driver
        # (keeps unit tests that use the in-memory repo dependency-free).
        from database import get_neo4j_driver

        return get_neo4j_driver()

    async def find_by_content_hash(
        self, org_id: str, content_hash: str
    ) -> Optional[Document]:
        async def _read(tx):  # type: ignore[no-untyped-def]
            result = await tx.run(
                self._FIND_CYPHER, org_id=org_id, content_hash=content_hash
            )
            return await result.single()

        async with self._driver().session() as session:
            record = await session.execute_read(_read)
        return _document_from_record(record.data()) if record else None

    async def create(self, document: Document) -> Document:
        params = {
            "org_id": document.org_id,
            "content_hash": document.content_hash,
            "document_id": document.document_id,
            "source": document.source,
            "source_label": document.source_label,
            "original_filename": document.original_filename,
            "title": document.title,
            "author": document.author,
            "owners": document.owners,
            "source_created_at": document.source_created_at,
            "source_updated_at": document.source_updated_at,
            "source_application": document.source_application,
            "source_location": document.source_location,
            "department": document.department,
            "project": document.project,
            "folder_path": document.folder_path,
            "version": document.version,
            "contributors": document.contributors,
            "permissions": document.permissions,
            "source_url": document.source_url,
            "storage_path": document.storage_path,
            "mime_type": document.mime_type,
            "uploaded_by": document.uploaded_by,
            "visible_to": document.visible_to,
            "uploaded_at": document.uploaded_at,
            "status": document.status,
        }

        async def _write(tx):  # type: ignore[no-untyped-def]
            result = await tx.run(self._CREATE_CYPHER, **params)
            return await result.single()

        async with self._driver().session() as session:
            record = await session.execute_write(_write)
        return _document_from_record(record.data())

    async def link_chunk(
        self, chunk_id: str, document_id: str, locator: DerivedFrom, org_id: str
    ) -> None:
        params = {
            "org_id": org_id,
            "chunk_id": chunk_id,
            "document_id": document_id,
            "char_start": locator.char_start,
            "char_end": locator.char_end,
            "page_number": locator.page_number,
            "page_start": locator.page_start,
            "page_end": locator.page_end,
            "row_range": list(locator.row_range) if locator.row_range else None,
            "section_title": locator.section_title,
            "table_cell": locator.table_cell,
            "sheet_name": locator.sheet_name,
            "cell_range": locator.cell_range,
        }

        async def _write(tx):  # type: ignore[no-untyped-def]
            result = await tx.run(self._LINK_CYPHER, **params)
            record = await result.single()
            return record["linked"] if record else 0

        async with self._driver().session() as session:
            linked = await session.execute_write(_write)
        if not linked:
            raise ValueError(
                f"Could not link chunk {chunk_id!r} to document {document_id!r}: "
                "chunk or document node not found."
            )

    async def get_citation(self, chunk_id: str, org_id: str) -> Optional[Citation]:
        async def _read(tx):  # type: ignore[no-untyped-def]
            result = await tx.run(
                self._CITATION_CYPHER, chunk_id=chunk_id, org_id=org_id
            )
            return await result.single()

        async with self._driver().session() as session:
            record = await session.execute_read(_read)
        if record is None:
            return None
        return _citation_from_record(record.data())

    async def count_chunks_for_document(self, document_id: str) -> int:
        async def _read(tx):  # type: ignore[no-untyped-def]
            result = await tx.run(self._COUNT_CHUNKS_CYPHER, document_id=document_id)
            record = await result.single()
            return record["n"] if record else 0

        async with self._driver().session() as session:
            return await session.execute_read(_read)


class InMemoryDocumentRepository(DocumentRepository):
    """In-memory repository for tests and local experiments."""

    def __init__(self) -> None:
        self._by_hash: dict[tuple[str, str], Document] = {}
        self._by_id: dict[str, Document] = {}
        self._links: dict[str, tuple[str, DerivedFrom]] = {}

    async def find_by_content_hash(
        self, org_id: str, content_hash: str
    ) -> Optional[Document]:
        return self._by_hash.get((org_id, content_hash))

    async def create(self, document: Document) -> Document:
        key = (document.org_id, document.content_hash)
        existing = self._by_hash.get(key)
        if existing is not None:
            return existing
        self._by_hash[key] = document
        self._by_id[document.document_id] = document
        return document

    async def link_chunk(
        self, chunk_id: str, document_id: str, locator: DerivedFrom, org_id: str
    ) -> None:
        if document_id not in self._by_id:
            raise ValueError(f"Unknown document {document_id!r}")
        if self._by_id[document_id].org_id != org_id:
            raise ValueError(f"Document {document_id!r} does not belong to org {org_id!r}")
        self._links[chunk_id] = (document_id, locator)

    async def get_citation(self, chunk_id: str, org_id: str) -> Optional[Citation]:
        link = self._links.get(chunk_id)
        if link is None:
            return None
        document_id, locator = link
        document = self._by_id[document_id]
        if document.org_id != org_id:
            return None
        return Citation(
            chunk_id=chunk_id,
            document_id=document.document_id,
            source=document.source,
            source_label=document.source_label,
            original_filename=document.original_filename,
            source_url=document.source_url,
            author=document.author,
            source_updated_at=document.source_updated_at,
            version=document.version,
            char_start=locator.char_start,
            char_end=locator.char_end,
            page_number=locator.page_number,
            page_start=locator.page_start,
            page_end=locator.page_end,
            row_range=locator.row_range,
            section_title=locator.section_title,
            table_cell=locator.table_cell,
            sheet_name=locator.sheet_name,
            cell_range=locator.cell_range,
        )

    async def count_chunks_for_document(self, document_id: str) -> int:
        return sum(1 for doc_id, _ in self._links.values() if doc_id == document_id)


def _citation_from_record(record: dict) -> Citation:
    """Build a :class:`Citation` from a Neo4j citation row."""

    row_range = record.get("row_range")
    return Citation(
        chunk_id=record["chunk_id"],
        document_id=record["document_id"],
        source=record["source"],
        source_label=record["source_label"],
        original_filename=record.get("original_filename"),
        source_url=record.get("source_url"),
        author=record.get("author"),
        source_updated_at=_to_datetime(record["source_updated_at"]) if record.get("source_updated_at") else None,
        version=record.get("version"),
        char_start=record.get("char_start"),
        char_end=record.get("char_end"),
        page_number=record.get("page_number"),
        page_start=record.get("page_start"),
        page_end=record.get("page_end"),
        row_range=tuple(row_range) if row_range else None,  # type: ignore[arg-type]
        section_title=record.get("section_title"),
        table_cell=record.get("table_cell"),
        sheet_name=record.get("sheet_name"),
        cell_range=record.get("cell_range"),
    )


# --- Public API -------------------------------------------------------------


async def store_document(
    *,
    org_id: str,
    data: bytes,
    source: str,
    source_label: str,
    mime_type: str,
    original_filename: str | None = None,
    title: str | None = None,
    author: str | None = None,
    owners: list[str] | None = None,
    source_created_at: datetime | None = None,
    source_updated_at: datetime | None = None,
    source_application: str | None = None,
    source_location: str | None = None,
    department: str | None = None,
    project: str | None = None,
    folder_path: str | None = None,
    version: str | None = None,
    contributors: list[str] | None = None,
    permissions: list[str] | None = None,
    source_url: str | None = None,
    uploaded_by: str | None = None,
    visible_to: list[str] | None = None,
    status: str = "pending",
    repository: DocumentRepository | None = None,
    storage: BlobStorage | None = None,
) -> DocumentStoreResult:
    """Store a raw source file and its ``Document`` node, de-duplicating re-uploads.

    The content is hashed first; if a document with the same hash already exists,
    blob storage is skipped entirely and the existing document is reused (so the
    same bytes uploaded twice resolve to one ``document_id`` and one blob).

    Args:
        data: Raw bytes of the uploaded file.
        source: Origin connector (e.g. whatsapp_export, email, excel).
        source_label: Human-readable label for citations.
        mime_type: MIME type of the file.
        original_filename: Original upload filename, if any.
        uploaded_by: ``person_id`` of the uploader, if known.
        visible_to: Group names permitted to see the document.
        status: Initial lifecycle state (default ``pending``).
        repository: Graph repository (defaults to Neo4j).
        storage: Blob backend (defaults to the configured one).

    Returns:
        A :class:`DocumentStoreResult` with the stored/reused document and a
        ``deduped`` flag.
    """

    repository = repository or Neo4jDocumentRepository()
    storage = storage or get_blob_storage()

    content_hash = compute_content_hash(data)

    existing = await repository.find_by_content_hash(org_id, content_hash)
    if existing is not None:
        logger.info(
            "Document de-dup: reusing %s for content_hash %s…",
            existing.document_id,
            content_hash[:12],
        )
        return DocumentStoreResult(document=existing, deduped=True)

    key = _storage_key(org_id, content_hash, original_filename)
    storage_path = await storage.put(key, data)

    document = Document(
        document_id=str(uuid4()),
        org_id=org_id,
        source=source,
        source_label=source_label,
        original_filename=original_filename,
        title=title or source_label,
        author=author,
        owners=list(owners or []),
        source_created_at=source_created_at,
        source_updated_at=source_updated_at,
        source_application=source_application,
        source_location=source_location,
        department=department,
        project=project,
        folder_path=folder_path,
        version=version,
        contributors=list(contributors or []),
        permissions=list(permissions or visible_to or []),
        source_url=source_url,
        storage_path=storage_path,
        content_hash=content_hash,
        mime_type=mime_type,
        uploaded_by=uploaded_by,
        visible_to=list(visible_to or []),
        uploaded_at=datetime.now(timezone.utc),
        status=status,  # type: ignore[arg-type]
    )

    persisted = await repository.create(document)
    # A different id back means a concurrent caller won the create race; the blob
    # we wrote is content-addressed (same key) so it's harmless either way.
    deduped = persisted.document_id != document.document_id
    return DocumentStoreResult(document=persisted, deduped=deduped)


async def link_chunk_to_document(
    chunk_id: str,
    document_id: str,
    locator: DerivedFrom | None = None,
    *,
    org_id: str,
    repository: DocumentRepository | None = None,
) -> None:
    """Attach ``chunk_id`` to ``document_id`` via ``DERIVED_FROM`` with a locator."""

    repository = repository or Neo4jDocumentRepository()
    await repository.link_chunk(
        chunk_id, document_id, locator or DerivedFrom(), org_id
    )


async def get_citation(
    chunk_id: str,
    *,
    org_id: str,
    repository: DocumentRepository | None = None,
) -> Citation:
    """Return a renderable :class:`Citation` for ``chunk_id``.

    Joins ``Chunk -[:DERIVED_FROM]-> Document`` and returns enough to render
    e.g. ``Source: Q3 Board Deck, q3.pptx, page 4``.

    Raises:
        CitationNotFoundError: If the chunk has no source document.
    """

    repository = repository or Neo4jDocumentRepository()
    citation = await repository.get_citation(chunk_id, org_id)
    if citation is None:
        raise CitationNotFoundError(chunk_id)
    return citation
