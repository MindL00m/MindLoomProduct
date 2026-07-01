"""FastAPI application exposing the conversation ingestion endpoints.

The API is connector-agnostic: connectors (WhatsApp, Teams, Slack, ...) are
responsible for producing the canonical :class:`Conversation` format and posting
it to ``/ingest/conversation``.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator
from uuid import uuid4

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from answerer import generate_answer
from database import close_pools
from models import (
    Conversation,
    DirectoryIngestRequest,
    DirectoryIngestResult,
    JobStatus,
    OrgGraphResponse,
    QueryRequest,
    QueryResponse,
)
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

# Allow the static frontend (opened via file:// or served on any local port) to
# call this API from the browser. Permissive by design for the local demo.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/ingest/conversation")
async def ingest_conversation(
    background_tasks: BackgroundTasks,
    conversation: Conversation,
) -> dict[str, str]:
    """Ingest a canonical conversation produced by any connector.

    Performs a shallow presence check (at least one participant and message)
    before enqueuing a background ingestion job; deeper validation happens in
    the pipeline.
    """

    if not conversation.participants:
        raise HTTPException(status_code=400, detail="Conversation must have at least one participant.")
    if not conversation.messages:
        raise HTTPException(status_code=400, detail="Conversation must have at least one message.")

    job_id = str(uuid4())
    job_store[job_id] = JobStatus(
        job_id=job_id,
        status="queued",
        conversation_id=conversation.conversation_id,
        progress="Queued",
    )
    background_tasks.add_task(run_ingestion_background, job_id, conversation, job_store)

    return {"job_id": job_id, "status": "queued"}


@app.post("/ingest/pdf")
async def ingest_pdf(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
) -> dict[str, str]:
    """Ingest an uploaded PDF: store it, chunk it structurally, and classify.

    Chunking + per-chunk LLM classification is slow, so this enqueues a
    background job and returns a ``job_id``; poll ``/ingest/status/{job_id}``.
    """

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
        status="queued",
        conversation_id=filename,
        progress="Queued",
    )
    background_tasks.add_task(
        run_pdf_ingestion_background, job_id, data, filename, [], job_store
    )

    return {"job_id": job_id, "status": "queued"}


@app.post("/ingest/directory", response_model=DirectoryIngestResult)
async def ingest_directory(request: DirectoryIngestRequest) -> DirectoryIngestResult:
    """Upsert an org-directory import (CSV / Google / ...) into the graph.

    People are de-duplicated on email and their reporting hierarchy is wired via
    ``REPORTS_TO``. This is synchronous: directory imports are small relative to
    conversation ingestion and the caller wants the resulting counts.
    """

    if not request.people:
        raise HTTPException(status_code=400, detail="Directory import contains no people.")

    try:
        return await upsert_directory(request.people, source=request.source)
    except Exception as e:  # noqa: BLE001 - surface any failure as HTTP 500
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/org/graph", response_model=OrgGraphResponse)
async def get_org_graph() -> OrgGraphResponse:
    """Return the organization graph (people + reporting edges) for the UI."""

    try:
        return await fetch_org_graph()
    except Exception as e:  # noqa: BLE001 - surface any failure as HTTP 500
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/ingest/status/{job_id}", response_model=JobStatus)
async def get_job_status(job_id: str) -> JobStatus:
    """Return the current status of an ingestion job, or 404 if unknown."""

    job = job_store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"No job found with id '{job_id}'.")
    return job


@app.post("/query", response_model=QueryResponse)
async def query(request: QueryRequest) -> QueryResponse:
    """Answer a natural-language question from the knowledge base."""

    try:
        retrieval = await retrieve(request.question, request.history)
        response = await generate_answer(
            request.question, retrieval, request.history
        )
        return response
    except Exception as e:  # noqa: BLE001 - surface any failure as HTTP 500
        raise HTTPException(status_code=500, detail=str(e))
