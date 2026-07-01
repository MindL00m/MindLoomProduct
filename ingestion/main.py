"""FastAPI application exposing the conversation ingestion endpoints.

The API is connector-agnostic: connectors (WhatsApp, Teams, Slack, ...) are
responsible for producing the canonical :class:`Conversation` format and posting
it to ``/ingest/conversation``. All data endpoints require an ``X-Org-Id``
header that scopes reads and writes to one organization.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Annotated, AsyncIterator
from uuid import uuid4

from fastapi import BackgroundTasks, Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from answerer import generate_answer
from auth import create_org, get_org_summary, google_signin, require_org_id
from database import close_pools
from models import (
    AuthSessionResponse,
    Conversation,
    CreateOrgRequest,
    DirectoryIngestRequest,
    DirectoryIngestResult,
    FileExtractResponse,
    GoogleSignInRequest,
    JobStatus,
    OrgGraphResponse,
    OrgSummaryResponse,
    QueryRequest,
    QueryResponse,
)
from file_extract import extract_file_text
from pipeline import run_ingestion_background, run_pdf_ingestion_background
from retrieval import retrieve
from storage import fetch_org_graph, upsert_directory

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# In-memory job state (v1 — not durable across restarts).
job_store: dict[str, JobStatus] = {}


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Manage shared connection pools for the app lifecycle."""

    logger.info("Company Brain ingestion service starting up")
    try:
        yield
    finally:
        await close_pools()
        logger.info("Company Brain ingestion service shut down")


app = FastAPI(title="Company Brain — Conversation Ingestion", version="2.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/auth/google/signin", response_model=AuthSessionResponse)
async def auth_google_signin(request: GoogleSignInRequest) -> AuthSessionResponse:
    """Simulated Google SSO: map email domain to an existing organization."""

    return await google_signin(
        email=request.email,
        name=request.name,
        photo_url=request.photo_url,
    )


@app.post("/orgs", response_model=AuthSessionResponse)
async def create_organization(request: CreateOrgRequest) -> AuthSessionResponse:
    """Create a new organization and its first admin user."""

    try:
        return await create_org(
            name=request.name,
            domain=request.domain,
            admin_email=request.admin_email,
            admin_name=request.admin_name,
            admin_photo_url=request.admin_photo_url,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/org/summary", response_model=OrgSummaryResponse)
async def org_summary(org_id: Annotated[str, Depends(require_org_id)]) -> OrgSummaryResponse:
    """Return org-scoped counts for dashboard and setup completion."""

    return await get_org_summary(org_id)


@app.post("/ingest/conversation")
async def ingest_conversation(
    background_tasks: BackgroundTasks,
    conversation: Conversation,
    org_id: Annotated[str, Depends(require_org_id)],
) -> dict[str, str]:
    """Ingest a canonical conversation produced by any connector."""

    if not conversation.participants:
        raise HTTPException(status_code=400, detail="Conversation must have at least one participant.")
    if not conversation.messages:
        raise HTTPException(status_code=400, detail="Conversation must have at least one message.")

    job_id = str(uuid4())
    job_store[job_id] = JobStatus(
        job_id=job_id,
        org_id=org_id,
        status="queued",
        conversation_id=conversation.conversation_id,
        progress="Queued",
    )
    background_tasks.add_task(
        run_ingestion_background, job_id, conversation, org_id, job_store
    )

    return {"job_id": job_id, "status": "queued"}


@app.post("/ingest/pdf")
async def ingest_pdf(
    background_tasks: BackgroundTasks,
    org_id: Annotated[str, Depends(require_org_id)],
    file: UploadFile = File(...),
) -> dict[str, str]:
    """Ingest an uploaded PDF: store it, chunk it structurally, and classify."""

    filename = file.filename or "upload.pdf"
    is_pdf = filename.lower().endswith(".pdf") or file.content_type == "application/pdf"
    if not is_pdf:
        raise HTTPException(status_code=400, detail="Expected a PDF file.")

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded PDF is empty.")

    job_id = str(uuid4())
    job_store[job_id] = JobStatus(
        job_id=job_id,
        org_id=org_id,
        status="queued",
        conversation_id=filename,
        progress="Queued",
    )
    background_tasks.add_task(
        run_pdf_ingestion_background, job_id, data, filename, org_id, [], job_store
    )

    return {"job_id": job_id, "status": "queued"}


@app.post("/ingest/directory", response_model=DirectoryIngestResult)
async def ingest_directory(
    request: DirectoryIngestRequest,
    org_id: Annotated[str, Depends(require_org_id)],
) -> DirectoryIngestResult:
    """Upsert an org-directory import (CSV / Google / ...) into the graph."""

    if not request.people:
        raise HTTPException(status_code=400, detail="Directory import contains no people.")

    try:
        return await upsert_directory(request.people, org_id, source=request.source)
    except Exception as e:  # noqa: BLE001 - surface any failure as HTTP 500
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/org/graph", response_model=OrgGraphResponse)
async def get_org_graph(
    org_id: Annotated[str, Depends(require_org_id)],
) -> OrgGraphResponse:
    """Return the organization graph (people + reporting edges) for the UI."""

    try:
        return await fetch_org_graph(org_id)
    except Exception as e:  # noqa: BLE001 - surface any failure as HTTP 500
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/ingest/status/{job_id}", response_model=JobStatus)
async def get_job_status(
    job_id: str,
    org_id: Annotated[str, Depends(require_org_id)],
) -> JobStatus:
    """Return the current status of an ingestion job, or 404 if unknown."""

    job = job_store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"No job found with id '{job_id}'.")
    if job.org_id and job.org_id != org_id:
        raise HTTPException(status_code=404, detail=f"No job found with id '{job_id}'.")
    return job


@app.post("/query", response_model=QueryResponse)
async def query(
    request: QueryRequest,
    org_id: Annotated[str, Depends(require_org_id)],
) -> QueryResponse:
    """Answer a natural-language question from the knowledge base."""

    try:
        retrieval = await retrieve(request.question, request.history, org_id)
        response = await generate_answer(
            request.question,
            retrieval,
            request.history,
            org_id,
            request.ephemeral_documents,
        )
        return response
    except Exception as e:  # noqa: BLE001 - surface any failure as HTTP 500
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/files/extract", response_model=FileExtractResponse)
async def extract_uploaded_file(
    org_id: Annotated[str, Depends(require_org_id)],
    file: UploadFile = File(...),
) -> FileExtractResponse:
    """Extract text from an uploaded file for chat-only (ephemeral) context."""

    filename = file.filename or "upload"
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    try:
        text = extract_file_text(filename, data)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Could not read file: {exc}") from exc
    return FileExtractResponse(
        document_id=str(uuid4()),
        filename=filename,
        text=text,
        char_count=len(text),
    )
