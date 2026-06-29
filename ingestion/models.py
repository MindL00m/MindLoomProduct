"""Shared Pydantic models used across the WhatsApp ingestion pipeline."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

PersonStatus = Literal["active", "inactive"]

KnowledgeType = Literal["decision", "question_answer", "problem_report", "status_update", "noise"]
SignalType = Literal["asked", "answered", "owns", "mentioned"]
Confidence = Literal["high", "medium", "low"]


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


class JobStatus(BaseModel):
    """State of an asynchronous ingestion job."""

    job_id: str = Field(description="Unique identifier for this ingestion job")
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


class ExpertResult(BaseModel):
    """A person surfaced from Neo4j graph traversal as a likely expert."""

    name: str = Field(description="Display name of the surfaced person")
    reason: str = Field(description="Human readable explanation of why this person was surfaced")
    relationship_count: int = Field(
        description="Number of graph relationships connecting this person to the relevant entities"
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


class QueryRequest(BaseModel):
    """A natural-language query against the knowledge base."""

    question: str = Field(description="Natural language question from the user")
    user_id: Optional[str] = Field(
        default=None, description="User id for permission filtering, unused in v1"
    )


class QueryResponse(BaseModel):
    """An answer generated from retrieved context, with routing metadata."""

    answer: str = Field(description="Answer generated from retrieved context")
    sources: list[ChunkResult] = Field(description="Chunks used to generate the answer")
    expert: Optional[ExpertResult] = Field(
        default=None,
        description="Top expert to route to if confidence is low or no answer found",
    )
    confidence: Literal["high", "medium", "low"] = Field(description="Confidence in the answer")
    routed: bool = Field(
        description="True if the question could not be answered and was routed to an expert"
    )
    routed_reason: Optional[str] = Field(
        default=None, description="Present when routed is true, explains why"
    )
