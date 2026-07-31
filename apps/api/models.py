"""Shared Pydantic models used across the Loom API."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, computed_field

PersonStatus = Literal["active", "inactive"]

KnowledgeType = Literal["decision", "question_answer", "problem_report", "status_update", "noise"]
SignalType = Literal["asked", "answered", "owns", "mentioned"]
Confidence = Literal["high", "medium", "low"]
EntityType = Literal["person", "project", "system", "tool", "process", "policy", "location", "equipment", "topic"]
WorkStatus = Literal["open", "closed"]
ActionItemStatus = Literal["open", "done", "cancelled"]
IssueKind = Literal["problem_report", "status_update"]
IssueStatus = Literal["open", "closed"]

# Lifecycle of a stored source document. ``pending`` = stored but not yet
# processed into chunks; ``processed`` = chunking/extraction succeeded;
# ``failed`` = processing errored.
DocumentStatus = Literal["pending", "processed", "failed"]


class CaptureCreate(BaseModel):
    """Approved browser screenshot uploaded by the Loom extension."""

    id: str
    timestamp: int
    data_url: str = Field(alias="dataUrl")
    url: str = ""
    tab_title: str = Field(default="", alias="tabTitle")
    window_id: int | None = Field(default=None, alias="windowId")
    session_id: str = Field(default="", alias="sessionId")
    note: str = ""
    redactions: list[dict[str, float]] = Field(default_factory=list)
    org_id: str = Field(default="default", alias="orgId")
    user_id: str = Field(default="browser-user", alias="userId")


class CaptureRecord(BaseModel):
    id: str
    timestamp: int
    url: str = ""
    tab_title: str = ""
    window_id: int | None = None
    filepath: str
    session_id: str = ""
    note: str = ""
    redactions: list[dict[str, float]] = Field(default_factory=list)
    org_id: str = "default"
    user_id: str = "browser-user"


class CaptureSummary(BaseModel):
    app_or_site: str
    action_summary: str
    content_excerpt: str
    inferred_task_type: str
    confidence: float = Field(ge=0, le=1)


class SkillFileDraft(BaseModel):
    skill_id: str
    session_id: str
    title: str
    purpose: str
    application: str
    context: list[str] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    important_fields: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    decision_guidance: list[str] = Field(default_factory=list)
    follow_up_questions: list[str] = Field(default_factory=list)
    source_capture_ids: list[str] = Field(default_factory=list)
    status: Literal["proposed", "approved", "rejected"] = "proposed"
    expert_notes: str = ""
    created_at: datetime
    updated_at: datetime
    org_id: str = "default"
    created_by: str = "browser-user"


class SkillFileReview(BaseModel):
    status: Literal["approved", "rejected"]
    title: Optional[str] = None
    purpose: Optional[str] = None
    steps: Optional[list[str]] = None
    important_fields: Optional[list[str]] = None
    warnings: Optional[list[str]] = None
    decision_guidance: Optional[list[str]] = None
    expert_notes: str = ""


class SkillFileUpdate(BaseModel):
    """Rename or edit a Skill File without changing approval status."""

    title: Optional[str] = None
    purpose: Optional[str] = None
    application: Optional[str] = None
    expert_notes: Optional[str] = None


class Message(BaseModel):
    """A single, fully-resolved chat message."""

    speaker: str = Field(..., description="Normalised display name of the message author.")
    timestamp: datetime = Field(..., description="Local timestamp at which the message was sent.")
    body: str = Field(..., description="Full text of the message, with multi-line content concatenated.")


class Chunk(BaseModel):
    """A contiguous, topically-coherent group of messages ready for extraction."""

    chunk_id: str = Field(..., description="Stable UUID identifying this chunk.")
    messages: list[Message] = Field(..., description="Ordered messages contained in the chunk.")
    speakers: list[str] = Field(..., description="Distinct speakers participating in the chunk, in first-seen order.")
    start_time: datetime = Field(..., description="Timestamp of the first message in the chunk.")
    end_time: datetime = Field(..., description="Timestamp of the last message in the chunk.")
    raw_text: str = Field(..., description="All messages concatenated as 'Speaker: body' lines.")


class OwnershipSignal(BaseModel):
    """A signal that a person has some relationship to a topic within a chunk."""

    person: str = Field(..., description="Person the signal is about.")
    topic: str = Field(..., description="Topic, project, or system the signal relates to.")
    signal_type: SignalType = Field(
        ...,
        description="Nature of the relationship: asked, answered, owns, or mentioned.",
    )


class TypedEntity(BaseModel):
    name: str
    type: EntityType = "topic"
    relevance: Literal["primary", "secondary"] = "secondary"


class ProjectUpdate(BaseModel):
    """Lifecycle signal for a named project mentioned in the chunk."""

    name: str
    work_status: WorkStatus = "open"
    evidence: str = ""


class ActionItemUpdate(BaseModel):
    """Open/close signal for assigned work extracted from the chunk."""

    text: str
    status: ActionItemStatus = "open"
    assignee: Optional[str] = None
    project: Optional[str] = None


class IssueUpdate(BaseModel):
    """Open/close signal for a problem report or status update."""

    title: str
    kind: IssueKind = "problem_report"
    status: IssueStatus = "open"
    project: Optional[str] = None


class ChunkMetadata(BaseModel):
    """LLM-extracted structured metadata describing a single chunk."""

    entities: list[str] = Field(
        ...,
        description="People, projects, systems, tools, and dates mentioned in the chunk.",
    )
    knowledge_type: KnowledgeType = Field(
        ...,
        description="Dominant kind of knowledge captured in the chunk.",
    )
    ownership: list[OwnershipSignal] = Field(
        ...,
        description="Per-person ownership / participation signals derived from the chunk.",
    )
    confidence: Confidence = Field(..., description="Model's confidence in this extraction.")
    confidence_reason: str = Field(..., description="Short explanation for the assigned confidence level.")
    summary: str = Field(..., description="One-sentence summary of what the chunk contains.")
    typed_entities: list[TypedEntity] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)
    action_items: list[str] = Field(default_factory=list)
    factual_claims: list[str] = Field(default_factory=list)
    valid_until: Optional[datetime] = None
    project_updates: list[ProjectUpdate] = Field(default_factory=list)
    action_item_updates: list[ActionItemUpdate] = Field(default_factory=list)
    issue_updates: list[IssueUpdate] = Field(default_factory=list)


class StatusEvidence(BaseModel):
    chunk_id: str
    summary: str = ""
    source: str = ""
    source_label: str = ""
    knowledge_type: str = ""
    end_time: Optional[datetime] = None
    excerpt: str = ""


class StatusProjectItem(BaseModel):
    entity_id: str
    name: str
    work_status: WorkStatus = "open"
    current_status: str = ""
    last_signal_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    recent_updates: list[StatusEvidence] = Field(default_factory=list)
    evidence: list[StatusEvidence] = Field(default_factory=list)


class StatusIssueItem(BaseModel):
    issue_id: str
    title: str
    kind: IssueKind
    status: IssueStatus = "open"
    project: Optional[str] = None
    created_at: Optional[datetime] = None
    last_seen_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    evidence: list[StatusEvidence] = Field(default_factory=list)


class StatusActionItem(BaseModel):
    action_item_id: str
    text: str
    status: ActionItemStatus = "open"
    assignee: Optional[str] = None
    project: Optional[str] = None
    created_at: Optional[datetime] = None
    last_signal_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    evidence: list[StatusEvidence] = Field(default_factory=list)


class OpenStatusResponse(BaseModel):
    """Open projects, reports, and action items for the Status tab."""

    projects: list[StatusProjectItem] = Field(default_factory=list)
    issues: list[StatusIssueItem] = Field(default_factory=list)
    action_items: list[StatusActionItem] = Field(default_factory=list)


StatusItemKind = Literal["project", "issue", "action_item"]


class FinishStatusItemResponse(BaseModel):
    """Result of marking a status-board item finished."""

    kind: StatusItemKind
    item_id: str
    status: str


class IngestionResult(BaseModel):
    """Summary statistics returned after running a full ingestion."""

    total_messages: int = Field(..., description="Number of messages parsed from the export.")
    total_chunks: int = Field(..., description="Number of chunks produced by the chunker.")
    chunks_by_type: dict[str, int] = Field(
        ...,
        description="Count of successfully processed chunks grouped by knowledge_type.",
    )
    failed_chunks: int = Field(..., description="Number of chunks that failed during processing or storage.")
    duration_seconds: float = Field(..., description="Wall-clock duration of the ingestion run, in seconds.")


class Participant(BaseModel):
    """A single participant in a canonical conversation."""

    id: str = Field(description="Unique identifier for this participant within the conversation")
    name: str = Field(description="Display name of the participant")


class IncomingMessage(BaseModel):
    """A single message in the connector-agnostic conversation format."""

    id: str = Field(description="Unique identifier for this message")
    sender: str = Field(description="Participant id matching one entry in participants list")
    timestamp: datetime = Field(description="UTC timestamp of the message")
    text: str = Field(description="Message body text")


class Conversation(BaseModel):
    """A canonical conversation that any connector (WhatsApp, Teams, Slack) can produce."""

    source: str = Field(description="Origin of the conversation e.g. whatsapp, teams, slack")
    conversation_id: str = Field(description="Unique identifier for this conversation")
    title: Optional[str] = Field(default=None, description="Human readable name for this conversation")
    participants: list[Participant] = Field(description="All participants in the conversation")
    messages: list[IncomingMessage] = Field(
        description="All messages ordered by timestamp ascending"
    )


class Document(BaseModel):
    """A raw source file that one or more chunks were derived from.

    A ``Document`` is the citation anchor for chunks: it records where the raw
    bytes live (``storage_path`` in blob storage), how to de-duplicate re-uploads
    (``content_hash``), and provenance metadata. It is stored as a node in the
    knowledge graph, separate from ``Chunk`` nodes, and connected to them via the
    ``DERIVED_FROM`` relationship.
    """

    document_id: str = Field(description="Stable UUID identifying this document.")
    org_id: str = Field(description="Organization that owns this document.")
    source: str = Field(description="Origin connector, e.g. whatsapp_export, email, excel.")
    source_label: str = Field(description="Human-readable label shown in citations.")
    original_filename: Optional[str] = Field(
        default=None, description="Original upload filename, if any."
    )
    title: Optional[str] = None
    author: Optional[str] = None
    owners: list[str] = Field(default_factory=list)
    source_created_at: Optional[datetime] = None
    source_updated_at: Optional[datetime] = None
    source_application: Optional[str] = None
    source_location: Optional[str] = None
    department: Optional[str] = None
    project: Optional[str] = None
    folder_path: Optional[str] = None
    version: Optional[str] = None
    contributors: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    source_url: Optional[str] = None
    storage_path: str = Field(description="Path / URI to the raw file in blob storage.")
    content_hash: str = Field(description="SHA-256 hex digest of the raw bytes; de-dup key.")
    mime_type: str = Field(description="MIME type of the raw file.")
    uploaded_by: Optional[str] = Field(
        default=None, description="person_id of the uploader, if known."
    )
    visible_to: list[str] = Field(
        default_factory=list, description="Group names permitted to see this document."
    )
    uploaded_at: datetime = Field(description="When the document was uploaded (UTC).")
    status: DocumentStatus = Field(
        default="pending", description="Processing lifecycle state."
    )


class DocumentMetadataInput(BaseModel):
    """Optional provenance supplied with a manual document upload."""

    title: Optional[str] = None
    author: Optional[str] = None
    owners: list[str] = Field(default_factory=list)
    source_created_at: Optional[datetime] = None
    source_updated_at: Optional[datetime] = None
    source_application: Optional[str] = None
    source_location: Optional[str] = None
    department: Optional[str] = None
    project: Optional[str] = None
    folder_path: Optional[str] = None
    version: Optional[str] = None
    contributors: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    source_url: Optional[str] = None


class DerivedFrom(BaseModel):
    """Locator for the slice of a ``Document`` a chunk was derived from.

    All fields are optional because the meaningful locator depends on the source
    type: character offsets for free text, ``page_number`` for paginated sources
    (PDF/PPTX), and ``row_range`` for spreadsheets.
    """

    char_start: Optional[int] = Field(
        default=None, description="Start offset into the document's extracted text."
    )
    char_end: Optional[int] = Field(
        default=None, description="End offset into the document's extracted text."
    )
    page_number: Optional[int] = Field(
        default=None, description="1-based page/slide number for single-page sources."
    )
    page_start: Optional[int] = Field(
        default=None, description="1-based first page a chunk spans (paginated sources)."
    )
    page_end: Optional[int] = Field(
        default=None, description="1-based last page a chunk spans (paginated sources)."
    )
    row_range: Optional[tuple[int, int]] = Field(
        default=None, description="Inclusive (start, end) row range for spreadsheets."
    )
    section_title: Optional[str] = None
    table_cell: Optional[str] = None
    sheet_name: Optional[str] = None
    cell_range: Optional[str] = None


class DocumentStoreResult(BaseModel):
    """Outcome of storing a document, including whether it was de-duplicated."""

    document: Document = Field(description="The stored (or pre-existing) document.")
    deduped: bool = Field(
        description="True when an existing document with the same content_hash was reused."
    )


class Citation(BaseModel):
    """Everything needed to render a human-readable source citation for a chunk.

    Produced by joining ``Chunk -[:DERIVED_FROM]-> Document``. Call
    :meth:`render` for the display string, or read the raw fields directly.
    """

    chunk_id: str = Field(description="Chunk the citation is for.")
    document_id: str = Field(description="Source document id.")
    source: str = Field(description="Origin connector of the document.")
    source_label: str = Field(description="Human-readable document label.")
    source_url: Optional[str] = None
    author: Optional[str] = None
    source_updated_at: Optional[datetime] = None
    version: Optional[str] = None
    original_filename: Optional[str] = Field(
        default=None, description="Original document filename, if any."
    )
    char_start: Optional[int] = Field(default=None)
    char_end: Optional[int] = Field(default=None)
    page_number: Optional[int] = Field(default=None)
    page_start: Optional[int] = Field(default=None)
    page_end: Optional[int] = Field(default=None)
    row_range: Optional[tuple[int, int]] = Field(default=None)
    section_title: Optional[str] = None
    table_cell: Optional[str] = None
    sheet_name: Optional[str] = None
    cell_range: Optional[str] = None

    def locator(self) -> str:
        """Return the location-within-document fragment (e.g. ``pages 2-3``).

        Page ranges take precedence for paginated sources (PDFs); character
        offsets are used for free text and rows for spreadsheets.
        """

        if self.page_start is not None and self.page_end is not None:
            if self.page_start == self.page_end:
                return f"page {self.page_start}"
            return f"pages {self.page_start}-{self.page_end}"
        if self.page_number is not None:
            return f"page {self.page_number}"
        if self.char_start is not None and self.char_end is not None:
            return f"chars {self.char_start}-{self.char_end}"
        if self.row_range is not None:
            location = f"rows {self.row_range[0]}-{self.row_range[1]}"
            if self.sheet_name:
                location = f"sheet {self.sheet_name}, {location}"
            if self.cell_range:
                location += f", cells {self.cell_range}"
            return location
        if self.table_cell:
            return f"section {self.section_title}, {self.table_cell}" if self.section_title else self.table_cell
        if self.section_title:
            return f"section {self.section_title}"
        return ""

    def render(self) -> str:
        """Render the full citation string, e.g.
        ``Source: Q3 Board Deck, q3.pptx, page 4``."""

        name = self.original_filename or "(unnamed document)"
        locator = self.locator()
        base = f"Source: {self.source_label}, {name}"
        return f"{base}, {locator}" if locator else base

    @computed_field  # type: ignore[prop-decorator]
    @property
    def label(self) -> str:
        """Serialized, human-readable citation string for API consumers."""

        return self.render()


class DirectoryPerson(BaseModel):
    """A single person row from an org-directory import (CSV, Google, ...).

    Mirrors the importable subset of the Neo4j ``Person`` node. System-managed
    fields (``person_id``, ``canonical_email``, ``canonical_name``,
    ``manager_id``, ``created_at``, ``updated_at``, ``source_ids``) are derived
    by the storage layer and are intentionally absent here.
    """

    # Identity
    email: str = Field(description="Primary email; lower-cased to canonical_email for de-dup.")
    name: str = Field(description="Full display name.")
    user_id: Optional[str] = Field(default=None, description="External IdP id (Google/Slack/...).")
    preferred_name: Optional[str] = Field(default=None, description="Preferred / nickname.")
    photo_url: Optional[str] = Field(default=None, description="Profile photo URL.")

    # Employment
    title: Optional[str] = Field(default=None, description="Job title, e.g. Staff Engineer.")
    department: Optional[str] = Field(default=None, description="Department, e.g. Engineering.")
    business_unit: Optional[str] = Field(default=None, description="Business unit, e.g. Platform.")
    employee_type: Optional[str] = Field(default=None, description="Employee, Contractor, ...")
    status: PersonStatus = Field(default="active", description="active / inactive.")

    # Organization
    manager_email: Optional[str] = Field(default=None, description="Manager's email (REPORTS_TO).")
    groups: list[str] = Field(default_factory=list, description="Team/group memberships.")
    org_unit: Optional[str] = Field(default=None, description="Org unit path.")

    # Location
    location: Optional[str] = Field(default=None, description="Location label, e.g. London HQ.")
    city: Optional[str] = Field(default=None, description="City.")
    country: Optional[str] = Field(default=None, description="Country.")
    desk_location: Optional[str] = Field(default=None, description="Desk / seat location.")

    # Dates
    start_date: Optional[str] = Field(default=None, description="Employment start date (ISO 8601).")


class DirectoryIngestRequest(BaseModel):
    """Payload for the directory ingestion endpoint."""

    people: list[DirectoryPerson] = Field(description="People to upsert into the graph.")
    source: str = Field(default="csv", description="Origin of the directory, e.g. csv, google.")


class DirectoryIngestResult(BaseModel):
    """Summary returned after a directory import."""

    people_upserted: int = Field(description="Number of Person nodes created or updated.")
    departments: int = Field(description="Distinct departments seen in the import.")
    groups: int = Field(description="Distinct team/group names seen in the import.")
    reporting_links: int = Field(description="REPORTS_TO relationships created/confirmed.")


class OrgPerson(BaseModel):
    """Public-facing profile of a person for the org-chart visualization.

    Deliberately excludes internal/system fields (user_id, source_ids,
    employee_type, desk_location) — only directory-public attributes are shown.
    """

    id: str = Field(description="Stable person_id (used as the graph node key).")
    name: str = Field(description="Full display name.")
    preferred_name: Optional[str] = Field(default=None)
    email: Optional[str] = Field(default=None)
    title: Optional[str] = Field(default=None)
    department: Optional[str] = Field(default=None)
    business_unit: Optional[str] = Field(default=None)
    photo_url: Optional[str] = Field(default=None)
    location: Optional[str] = Field(default=None)
    city: Optional[str] = Field(default=None)
    country: Optional[str] = Field(default=None)
    groups: list[str] = Field(default_factory=list)
    status: Optional[str] = Field(default=None)
    start_date: Optional[str] = Field(default=None)
    manager_id: Optional[str] = Field(default=None, description="person_id of this person's manager.")


class OrgEdge(BaseModel):
    """A reporting relationship: ``source`` reports to ``target``."""

    source: str = Field(description="person_id of the report.")
    target: str = Field(description="person_id of the manager.")


class OrgGraphResponse(BaseModel):
    """The organization graph: people plus their reporting relationships."""

    people: list[OrgPerson] = Field(description="All directory people (public fields).")
    edges: list[OrgEdge] = Field(description="REPORTS_TO relationships between people.")


class GraphDebugNode(BaseModel):
    """A node in the org-scoped knowledge graph (debug export)."""

    id: str = Field(description="Stable node key used in edges.")
    labels: list[str] = Field(description="Neo4j labels, e.g. Person, Chunk.")
    properties: dict[str, object] = Field(default_factory=dict)


class GraphDebugEdge(BaseModel):
    """A directed relationship between two graph nodes."""

    id: str
    source: str
    target: str
    type: str = Field(description="Neo4j relationship type.")
    properties: dict[str, object] = Field(default_factory=dict)


class KnowledgeGraphResponse(BaseModel):
    """Full knowledge-graph snapshot for dev/debug visualisation."""

    nodes: list[GraphDebugNode]
    edges: list[GraphDebugEdge]
    truncated: bool = Field(
        default=False,
        description="True when the export hit the server-side node cap.",
    )


class JobStatus(BaseModel):
    """State of an asynchronous ingestion job."""

    job_id: str = Field(description="Unique identifier for this ingestion job")
    org_id: Optional[str] = Field(default=None, description="Organization that owns this job")
    status: Literal["queued", "processing", "complete", "failed"] = Field(
        description="Current status of the job"
    )
    conversation_id: str = Field(description="The conversation being processed")
    progress: Optional[str] = Field(default=None, description="Human readable progress description")
    result: Optional[IngestionResult] = Field(
        default=None, description="Present when status is complete"
    )
    error: Optional[str] = Field(default=None, description="Present when status is failed")


class ChunkResult(BaseModel):
    """A single chunk returned from pgvector similarity search."""

    chunk_id: str = Field(description="Stable UUID identifying this chunk")
    raw_text: str = Field(description="All messages concatenated as 'Speaker: body' lines")
    summary: str = Field(description="One-sentence summary of what the chunk contains")
    speakers: list[str] = Field(description="Distinct speakers participating in the chunk")
    start_time: datetime = Field(description="Timestamp of the first message in the chunk")
    end_time: datetime = Field(description="Timestamp of the last message in the chunk")
    knowledge_type: str = Field(description="Dominant kind of knowledge captured in the chunk")
    confidence: str = Field(description="Extraction confidence level for the chunk")
    similarity_score: float = Field(description="Cosine similarity score from pgvector search")
    freshness_score: float = 0.0
    authority_score: float = 0.0
    graph_score: float = 0.0
    retrieval_score: float = 0.0
    citation: Optional["Citation"] = Field(
        default=None,
        description="Source citation for this chunk, joined from its DERIVED_FROM document.",
    )


class ExpertResult(BaseModel):
    """A person surfaced from Neo4j graph traversal as a likely expert."""

    name: str = Field(description="Display name of the surfaced person")
    reason: str = Field(description="Human readable explanation of why this person was surfaced")
    relationship_count: int = Field(
        description="Number of graph relationships connecting this person to the relevant entities"
    )
    email: Optional[str] = Field(
        default=None, description="Directory email used to assign an expert request."
    )


class RetrievalResult(BaseModel):
    """Combined vector + graph retrieval response for a question."""

    chunks: list[ChunkResult] = Field(
        description="Ranked list of relevant chunks from pgvector search"
    )
    experts: list[ExpertResult] = Field(
        description="Ranked list of relevant experts from Neo4j traversal"
    )
    entities_found: list[str] = Field(
        description="Named entities extracted from the question used to query Neo4j"
    )


class ChatMessage(BaseModel):
    """A single prior turn in a conversation, for follow-up memory."""

    role: Literal["user", "assistant"] = Field(description="Who authored the message")
    content: str = Field(description="The message text")


class EphemeralDocument(BaseModel):
    """Chat-only file content sent with a query (not stored in the knowledge graph)."""

    document_id: str = Field(description="Client-generated id for citation")
    filename: str = Field(description="Original upload filename")
    text: str = Field(description="Extracted text from the file")


class FileExtractResponse(BaseModel):
    """Text extracted from an uploaded file for ephemeral chat context."""

    document_id: str
    filename: str
    text: str
    char_count: int


class QueryRequest(BaseModel):
    """A natural-language query against the knowledge base."""

    question: str = Field(description="Natural language question from the user")
    user_id: Optional[str] = Field(
        default=None, description="User id for permission filtering, unused in v1"
    )
    history: list[ChatMessage] = Field(
        default_factory=list,
        description="Prior turns in this conversation (oldest first), for memory.",
    )
    ephemeral_documents: list[EphemeralDocument] = Field(
        default_factory=list,
        description="Chat-only attachments available for this conversation only.",
    )


class MessageablePerson(BaseModel):
    """A signed-in org member that can receive Expert Messages."""

    user_id: str
    name: str
    email: str
    title: Optional[str] = None
    department: Optional[str] = None


class ProposedExpertMessage(BaseModel):
    """Draft message awaiting user confirmation in Ask before send."""

    recipient_user_id: str
    recipient_name: str
    recipient_email: str
    message: str
    candidates: list[MessageablePerson] = Field(default_factory=list)


class ProposedPullRequest(BaseModel):
    """Draft single-file GitHub change awaiting Ask approval before opening a PR."""

    owner: str
    repo: str
    path: str
    base_branch: str
    branch_name: str
    old_content: str
    new_content: str
    file_sha: Optional[str] = Field(
        default=None,
        description="Blob SHA of the existing file; null when creating a new file.",
    )
    pr_title: str
    pr_body: str = ""
    commit_message: str = ""
    html_url: Optional[str] = Field(
        default=None,
        description="GitHub URL for the current file, when it already exists.",
    )


class ProposedWorkspaceMember(BaseModel):
    """A signed-in user included in a proposed project workspace."""

    user_id: str
    name: str
    email: str
    reason: str = ""


class ProposedWorkspaceUnmatched(BaseModel):
    """A KG person who could not be linked to a signed-in Loom user."""

    name: str
    email: Optional[str] = None
    reason: str = ""


class ProposedWorkspace(BaseModel):
    """Draft project workspace awaiting Ask approval before creation."""

    name: str
    purpose: str
    context_md: str
    loombot_mode: Literal["context_only", "org_knowledge"] = "context_only"
    members: list[ProposedWorkspaceMember] = Field(default_factory=list)
    unmatched_people: list[ProposedWorkspaceUnmatched] = Field(default_factory=list)


class QueryResponse(BaseModel):
    """An answer generated from retrieved context, with routing metadata."""

    answer: str = Field(description="Answer generated from retrieved context")
    sources: list[ChunkResult] = Field(description="Chunks used to generate the answer")
    expert: Optional[ExpertResult] = Field(
        default=None,
        description="Top expert to route to if confidence is low or no answer found",
    )
    expert_request_created: bool = False
    confidence: Literal["high", "medium", "low"] = Field(description="Confidence in the answer")
    routed: bool = Field(
        description="True if the question could not be answered and was routed to an expert"
    )
    routed_reason: Optional[str] = Field(
        default=None, description="Present when routed is true, explains why"
    )
    proposed_message: Optional[ProposedExpertMessage] = Field(
        default=None,
        description="Draft Expert Message awaiting explicit user approval in Ask.",
    )
    proposed_pull_request: Optional[ProposedPullRequest] = Field(
        default=None,
        description="Draft GitHub file change awaiting explicit user approval in Ask.",
    )
    proposed_workspace: Optional[ProposedWorkspace] = Field(
        default=None,
        description="Draft project workspace awaiting explicit user approval in Ask.",
    )


# --- Auth / tenancy ---------------------------------------------------------


class GoogleSignInRequest(BaseModel):
    """Simulated Google SSO sign-in payload."""

    email: str = Field(description="Google account email")
    name: Optional[str] = Field(default=None, description="Display name")
    photo_url: Optional[str] = Field(default=None, description="Profile photo URL")


class CreateOrgRequest(BaseModel):
    """Create a new organization and its first admin user."""

    name: str = Field(description="Organization display name")
    domain: str = Field(description="Primary email domain, e.g. acme.com")
    admin_email: str = Field(description="Admin Google account email")
    admin_name: Optional[str] = Field(default=None)
    admin_photo_url: Optional[str] = Field(default=None)


class AuthSessionResponse(BaseModel):
    """Returned after sign-in or org creation."""

    org_id: str
    org_name: str
    user_id: str
    email: str
    name: Optional[str] = None
    photo_url: Optional[str] = None
    role: Literal["admin", "member"] = "member"


class OrgSummaryResponse(BaseModel):
    """Org-scoped counts for dashboard and setup completion."""

    organization: str
    people: int
    departments: int
    groups: int


# --- Integrations / connected apps ------------------------------------------


class IntegrationInfo(BaseModel):
    """A connected third-party app for the current user."""

    provider: str = Field(description="Integration key, e.g. google_calendar")
    label: str = Field(description="Human-readable app name")
    connected: bool
    account_email: Optional[str] = None
    connected_at: Optional[str] = None
    setup_status: str = Field(
        default="not_connected",
        description="Connection lifecycle: not_connected, setup_required, importing, active, paused, warning, or error.",
    )
    selected_resource_count: int = 0
    last_synced_at: Optional[str] = None


class IntegrationsListResponse(BaseModel):
    """All workspace apps and their connection status."""

    integrations: list[IntegrationInfo]
    oauth_enabled: bool = Field(
        description="True when real Google OAuth credentials are configured on the server."
    )
    microsoft_oauth_enabled: bool = Field(
        default=False,
        description="True when real Microsoft OAuth credentials are configured on the server.",
    )
    zoom_oauth_enabled: bool = False


class OAuthAuthorizeResponse(BaseModel):
    """URL to redirect the user to for Google consent."""

    authorization_url: str


class ConnectionResource(BaseModel):
    """A provider resource selectable by an administrator."""

    id: str
    name: str
    kind: Literal[
        "drive", "folder", "site", "library", "team", "channel",
        "mailbox", "calendar", "chat", "recording", "transcript",
    ]
    parent_id: Optional[str] = None
    selectable: bool = True
    warning: Optional[str] = None


class ConnectionResourcesResponse(BaseModel):
    provider: Literal["google_workspace", "microsoft_teams", "zoom"]
    resources: list[ConnectionResource]


class ConnectionPolicyInput(BaseModel):
    included_resource_ids: list[str] = Field(min_length=1)
    excluded_resource_ids: list[str] = Field(default_factory=list)
    include_history: bool = True
    history_start_date: Optional[str] = None
    sync_frequency: Literal["realtime", "hourly", "daily"] = "realtime"
    access_mode: Literal[
        "respect_source_permissions", "organization", "selected"
    ] = "respect_source_permissions"
    allowed_departments: list[str] = Field(default_factory=list)
    allowed_user_ids: list[str] = Field(default_factory=list)


class ConnectionPreviewRequest(ConnectionPolicyInput):
    pass


class ConnectionPreviewResponse(BaseModel):
    provider: Literal["google_workspace", "microsoft_teams", "zoom"]
    selected_resources: int
    estimated_items: int
    estimated_size_bytes: int
    permission_warnings: list[str] = Field(default_factory=list)
    unsupported_items: int = 0
    count_is_exact: bool = True
    scanned_items: int = 0


class ConnectionPolicyResponse(ConnectionPolicyInput):
    provider: Literal["google_workspace", "microsoft_teams", "zoom"]
    status: str
    updated_at: str
    initial_job_ids: list[str] = Field(default_factory=list)


# --- Google Workspace sync --------------------------------------------------


class WorkspaceSyncStartResponse(BaseModel):
    """Returned when a Gmail/Drive sync is queued."""

    job_id: str
    status: Literal["queued"]
    source: Literal["gmail", "drive"]


class WorkspaceWatchResponse(BaseModel):
    """Stored watch/cursor state for a Google Workspace source."""

    provider: Literal["gmail", "drive"]
    account_email: str
    cursor: Optional[str] = None
    expiration: Optional[datetime] = None
    status: str = "active"


class GooglePubSubEnvelope(BaseModel):
    """Cloud Pub/Sub push envelope.

    ``message.data`` is base64url-encoded by Pub/Sub. The decoded payload is
    provider-specific; Gmail sends ``emailAddress`` and ``historyId``.
    """

    message: dict[str, object]
    subscription: Optional[str] = None


class GoogleWebhookResponse(BaseModel):
    """Acknowledgement returned to Pub/Sub push delivery."""

    accepted: bool
    provider: Optional[str] = None
    queued: bool = False
    job_id: Optional[str] = None


# --- Microsoft Teams sync ---------------------------------------------------


class TeamsSyncStartResponse(BaseModel):
    """Returned when a Microsoft Teams sync is queued."""

    job_id: str
    status: Literal["queued"]
    source: Literal["teams"]


class TeamsWatchRequest(BaseModel):
    """Request to create a Microsoft Graph subscription for one channel."""

    team_id: str = Field(description="Microsoft Graph team id")
    channel_id: str = Field(description="Microsoft Graph channel id")


class TeamsWatchResponse(BaseModel):
    """Stored Teams subscription/cursor state."""

    provider: Literal["teams"]
    resource: str
    subscription_id: Optional[str] = None
    expiration: Optional[datetime] = None
    status: str = "active"


class MicrosoftGraphWebhookPayload(BaseModel):
    """Microsoft Graph change notification payload."""

    value: list[dict[str, object]] = Field(default_factory=list)
