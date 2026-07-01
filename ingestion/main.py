"""FastAPI application exposing the conversation ingestion endpoints.

The API is connector-agnostic: connectors (WhatsApp, Teams, Slack, ...) are
responsible for producing the canonical :class:`Conversation` format and posting
it to ``/ingest/conversation``. All data endpoints require an ``X-Org-Id``
header that scopes reads and writes to one organization.
"""

from __future__ import annotations

import base64
import json
import logging
from contextlib import asynccontextmanager
from typing import Annotated, AsyncIterator
from uuid import uuid4

from fastapi import BackgroundTasks, Depends, FastAPI, File, Header, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, RedirectResponse

from answerer import generate_answer
from auth import create_org, get_org_summary, google_signin, require_org_id
from config import get_settings
from database import close_pools
from google_workspace import (
    connect_google_workspace_dev,
    find_drive_cursor_by_channel,
    find_gmail_cursor_by_email,
    handle_google_workspace_callback,
    run_workspace_sync_background,
    setup_drive_watch,
    setup_gmail_watch,
    start_google_workspace_oauth,
)
from microsoft_teams import (
    connect_microsoft_teams_dev,
    find_teams_cursor_by_subscription,
    graph_validation_response,
    handle_microsoft_teams_callback,
    run_teams_sync_background,
    setup_teams_channel_watch,
    start_microsoft_teams_oauth,
)
from integrations import (
    connect_google_calendar_dev,
    disconnect_google_calendar,
    get_calendar_events,
    handle_google_calendar_callback,
    list_integrations,
    require_user_context,
    start_google_calendar_oauth,
)
from models import (
    AuthSessionResponse,
    CalendarEventsResponse,
    Conversation,
    CreateOrgRequest,
    DirectoryIngestRequest,
    DirectoryIngestResult,
    FileExtractResponse,
    GooglePubSubEnvelope,
    GoogleWebhookResponse,
    GoogleSignInRequest,
    IntegrationsListResponse,
    JobStatus,
    KnowledgeGraphResponse,
    OAuthAuthorizeResponse,
    OrgGraphResponse,
    OrgSummaryResponse,
    QueryRequest,
    QueryResponse,
    WorkspaceSyncStartResponse,
    WorkspaceWatchResponse,
    TeamsWatchRequest,
    TeamsWatchResponse,
    TeamsSyncStartResponse,
)
from file_extract import extract_file_text
from pipeline import run_ingestion_background, run_pdf_ingestion_background
from retrieval import retrieve
from schema import ensure_schema
from storage import fetch_knowledge_graph_debug, fetch_org_graph, upsert_directory

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# In-memory job state (v1 — not durable across restarts).
job_store: dict[str, JobStatus] = {}


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Manage shared connection pools for the app lifecycle."""

    logger.info("Company Brain ingestion service starting up")
    await ensure_schema()
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


@app.get("/graph/debug", response_model=KnowledgeGraphResponse)
async def get_knowledge_graph_debug(
    org_id: Annotated[str, Depends(require_org_id)],
) -> KnowledgeGraphResponse:
    """Return all knowledge-graph nodes and edges for dev/debug visualisation."""

    try:
        return await fetch_knowledge_graph_debug(org_id)
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


@app.get("/integrations", response_model=IntegrationsListResponse)
async def get_integrations(
    ctx: Annotated[tuple[str, str], Depends(require_user_context)],
) -> IntegrationsListResponse:
    """List connected workspace apps for the current user."""

    org_id, user_id = ctx
    try:
        return await list_integrations(org_id, user_id)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/integrations/google/calendar/authorize", response_model=OAuthAuthorizeResponse)
async def authorize_google_calendar(
    ctx: Annotated[tuple[str, str], Depends(require_user_context)],
) -> OAuthAuthorizeResponse:
    """Return the Google OAuth consent URL for Calendar read access."""

    org_id, user_id = ctx
    try:
        return await start_google_calendar_oauth(org_id, user_id)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/integrations/google/calendar/connect-dev", response_model=IntegrationsListResponse)
async def dev_connect_google_calendar(
    ctx: Annotated[tuple[str, str], Depends(require_user_context)],
) -> IntegrationsListResponse:
    """Simulated Calendar connect when Google OAuth credentials are not configured."""

    org_id, user_id = ctx
    await connect_google_calendar_dev(org_id, user_id)
    return await list_integrations(org_id, user_id)


@app.get("/integrations/google/workspace/authorize", response_model=OAuthAuthorizeResponse)
async def authorize_google_workspace(
    ctx: Annotated[tuple[str, str], Depends(require_user_context)],
) -> OAuthAuthorizeResponse:
    """Return a Google OAuth consent URL for Gmail + Drive read sync."""

    org_id, user_id = ctx
    try:
        return await start_google_workspace_oauth(org_id, user_id)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/integrations/google/workspace/connect-dev", response_model=IntegrationsListResponse)
async def dev_connect_google_workspace(
    ctx: Annotated[tuple[str, str], Depends(require_user_context)],
) -> IntegrationsListResponse:
    """Simulated Workspace connect when Google OAuth credentials are not configured."""

    org_id, user_id = ctx
    await connect_google_workspace_dev(org_id, user_id)
    return await list_integrations(org_id, user_id)


@app.get("/integrations/microsoft/teams/authorize", response_model=OAuthAuthorizeResponse)
async def authorize_microsoft_teams(
    ctx: Annotated[tuple[str, str], Depends(require_user_context)],
) -> OAuthAuthorizeResponse:
    """Return a Microsoft OAuth consent URL for Teams read access."""

    org_id, user_id = ctx
    try:
        return await start_microsoft_teams_oauth(org_id, user_id)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/integrations/microsoft/teams/connect-dev", response_model=IntegrationsListResponse)
async def dev_connect_microsoft_teams(
    ctx: Annotated[tuple[str, str], Depends(require_user_context)],
) -> IntegrationsListResponse:
    """Simulated Microsoft Teams connect when Microsoft OAuth is not configured."""

    org_id, user_id = ctx
    await connect_microsoft_teams_dev(org_id, user_id)
    return await list_integrations(org_id, user_id)


@app.get("/integrations/google/calendar/callback")
async def google_calendar_callback(
    code: str = Query(...),
    state: str = Query(...),
) -> RedirectResponse:
    """OAuth callback — exchange code and redirect to the Apps screen."""

    try:
        redirect_url = await handle_google_calendar_callback(code, state)
        return RedirectResponse(url=redirect_url, status_code=302)
    except HTTPException as exc:
        settings = get_settings()
        detail = exc.detail if isinstance(exc.detail, str) else "oauth_failed"
        url = f"{settings.frontend_url.rstrip('/')}/dashboard?tab=apps&error={detail}"
        return RedirectResponse(url=url, status_code=302)


@app.get("/integrations/google/workspace/callback")
async def google_workspace_callback(
    code: str = Query(...),
    state: str = Query(...),
) -> RedirectResponse:
    """OAuth callback for Gmail/Drive sync consent."""

    try:
        redirect_url = await handle_google_workspace_callback(code, state)
        return RedirectResponse(url=redirect_url, status_code=302)
    except HTTPException as exc:
        settings = get_settings()
        detail = exc.detail if isinstance(exc.detail, str) else "oauth_failed"
        url = f"{settings.frontend_url.rstrip('/')}/dashboard?tab=apps&error={detail}"
        return RedirectResponse(url=url, status_code=302)


@app.get("/integrations/microsoft/teams/callback")
async def microsoft_teams_callback(
    code: str = Query(...),
    state: str = Query(...),
) -> RedirectResponse:
    """OAuth callback for Microsoft Teams sync consent."""

    try:
        redirect_url = await handle_microsoft_teams_callback(code, state)
        return RedirectResponse(url=redirect_url, status_code=302)
    except HTTPException as exc:
        settings = get_settings()
        detail = exc.detail if isinstance(exc.detail, str) else "oauth_failed"
        url = f"{settings.frontend_url.rstrip('/')}/dashboard?tab=apps&error={detail}"
        return RedirectResponse(url=url, status_code=302)


@app.post("/integrations/microsoft/teams/watch", response_model=TeamsWatchResponse)
async def watch_microsoft_teams(
    ctx: Annotated[tuple[str, str], Depends(require_user_context)],
    request: TeamsWatchRequest,
) -> TeamsWatchResponse:
    """Start or renew a Microsoft Graph subscription for one Teams channel."""

    org_id, user_id = ctx
    return await setup_teams_channel_watch(
        org_id,
        user_id,
        team_id=request.team_id,
        channel_id=request.channel_id,
    )


def _queue_teams_sync(
    *,
    background_tasks: BackgroundTasks,
    org_id: str,
    user_id: str,
    max_results: int,
) -> TeamsSyncStartResponse:
    job_id = str(uuid4())
    job_store[job_id] = JobStatus(
        job_id=job_id,
        org_id=org_id,
        status="queued",
        conversation_id="microsoft:teams",
        progress="Queued",
    )
    background_tasks.add_task(
        run_teams_sync_background,
        job_id=job_id,
        org_id=org_id,
        user_id=user_id,
        job_store=job_store,
        max_results=max_results,
    )
    return TeamsSyncStartResponse(job_id=job_id, status="queued", source="teams")


@app.post("/integrations/microsoft/teams/sync", response_model=TeamsSyncStartResponse)
async def sync_microsoft_teams_now(
    background_tasks: BackgroundTasks,
    ctx: Annotated[tuple[str, str], Depends(require_user_context)],
    max_results: int = Query(25, ge=1, le=100),
) -> TeamsSyncStartResponse:
    """Queue an immediate Microsoft Teams sync for the current user."""

    org_id, user_id = ctx
    return _queue_teams_sync(
        background_tasks=background_tasks,
        org_id=org_id,
        user_id=user_id,
        max_results=max_results,
    )


@app.post("/integrations/google/workspace/gmail/watch", response_model=WorkspaceWatchResponse)
async def watch_gmail(
    ctx: Annotated[tuple[str, str], Depends(require_user_context)],
) -> WorkspaceWatchResponse:
    """Start or renew Gmail INBOX push notifications for the current user."""

    org_id, user_id = ctx
    return await setup_gmail_watch(org_id, user_id)


@app.post("/integrations/google/workspace/drive/watch", response_model=WorkspaceWatchResponse)
async def watch_drive(
    ctx: Annotated[tuple[str, str], Depends(require_user_context)],
) -> WorkspaceWatchResponse:
    """Start or renew Drive changes notifications for the current user."""

    org_id, user_id = ctx
    return await setup_drive_watch(org_id, user_id)


def _queue_workspace_sync(
    *,
    background_tasks: BackgroundTasks,
    org_id: str,
    user_id: str,
    source: str,
    max_results: int,
    override_history_id: str | None = None,
) -> WorkspaceSyncStartResponse:
    job_id = str(uuid4())
    job_store[job_id] = JobStatus(
        job_id=job_id,
        org_id=org_id,
        status="queued",
        conversation_id=f"google:{source}",
        progress="Queued",
    )
    background_tasks.add_task(
        run_workspace_sync_background,
        job_id=job_id,
        org_id=org_id,
        user_id=user_id,
        source=source,  # type: ignore[arg-type]
        job_store=job_store,
        max_results=max_results,
        override_history_id=override_history_id,
    )
    return WorkspaceSyncStartResponse(
        job_id=job_id,
        status="queued",
        source=source,  # type: ignore[arg-type]
    )


@app.post("/integrations/google/workspace/gmail/sync", response_model=WorkspaceSyncStartResponse)
async def sync_gmail_now(
    background_tasks: BackgroundTasks,
    ctx: Annotated[tuple[str, str], Depends(require_user_context)],
    max_results: int = Query(25, ge=1, le=100),
) -> WorkspaceSyncStartResponse:
    """Queue an immediate Gmail incremental/backfill sync for the current user."""

    org_id, user_id = ctx
    return _queue_workspace_sync(
        background_tasks=background_tasks,
        org_id=org_id,
        user_id=user_id,
        source="gmail",
        max_results=max_results,
    )


@app.post("/integrations/google/workspace/drive/sync", response_model=WorkspaceSyncStartResponse)
async def sync_drive_now(
    background_tasks: BackgroundTasks,
    ctx: Annotated[tuple[str, str], Depends(require_user_context)],
    max_results: int = Query(25, ge=1, le=100),
) -> WorkspaceSyncStartResponse:
    """Queue an immediate Drive changes sync for the current user."""

    org_id, user_id = ctx
    return _queue_workspace_sync(
        background_tasks=background_tasks,
        org_id=org_id,
        user_id=user_id,
        source="drive",
        max_results=max_results,
    )


def _decode_pubsub_data(envelope: GooglePubSubEnvelope) -> dict[str, object]:
    data = envelope.message.get("data")
    if not isinstance(data, str) or not data:
        return {}
    padded = data + "=" * (-len(data) % 4)
    decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
    parsed = json.loads(decoded)
    return parsed if isinstance(parsed, dict) else {}


@app.get("/webhooks/microsoft/teams", response_class=PlainTextResponse)
async def microsoft_teams_webhook_validation(
    validationToken: str | None = Query(default=None),
) -> str:
    """Microsoft Graph subscription validation handshake."""

    return graph_validation_response(validationToken) or ""


@app.post("/webhooks/microsoft/teams", response_model=GoogleWebhookResponse)
async def microsoft_teams_webhook(
    background_tasks: BackgroundTasks,
    payload: dict[str, object],
) -> GoogleWebhookResponse:
    """Receive Microsoft Graph Teams change notifications and queue a sync."""

    values = payload.get("value")
    if not isinstance(values, list) or not values:
        return GoogleWebhookResponse(accepted=True, provider="teams", queued=False)

    queued = False
    job_id: str | None = None
    for item in values:
        if not isinstance(item, dict):
            continue
        subscription_id = str(item.get("subscriptionId") or "")
        if not subscription_id:
            continue
        cursor = await find_teams_cursor_by_subscription(subscription_id)
        if cursor is None:
            continue
        response = _queue_teams_sync(
            background_tasks=background_tasks,
            org_id=cursor.org_id,
            user_id=cursor.user_id,
            max_results=50,
        )
        queued = True
        job_id = response.job_id
    return GoogleWebhookResponse(accepted=True, provider="teams", queued=queued, job_id=job_id)


@app.post("/webhooks/google/pubsub", response_model=GoogleWebhookResponse)
async def google_pubsub_webhook(
    envelope: GooglePubSubEnvelope,
    background_tasks: BackgroundTasks,
) -> GoogleWebhookResponse:
    """Receive Google Pub/Sub push messages, currently Gmail mailbox updates."""

    payload = _decode_pubsub_data(envelope)
    email = str(payload.get("emailAddress") or "").lower()
    history_id = str(payload.get("historyId") or "")
    if not email or not history_id:
        return GoogleWebhookResponse(accepted=True, provider=None, queued=False)

    cursor = await find_gmail_cursor_by_email(email)
    if cursor is None:
        logger.info("Ignoring Gmail Pub/Sub update for unregistered mailbox %s", email)
        return GoogleWebhookResponse(accepted=True, provider="gmail", queued=False)

    response = _queue_workspace_sync(
        background_tasks=background_tasks,
        org_id=cursor.org_id,
        user_id=cursor.user_id,
        source="gmail",
        max_results=50,
        override_history_id=cursor.cursor_value,
    )
    return GoogleWebhookResponse(
        accepted=True,
        provider="gmail",
        queued=True,
        job_id=response.job_id,
    )


@app.post("/webhooks/google/drive", response_model=GoogleWebhookResponse)
async def google_drive_webhook(
    background_tasks: BackgroundTasks,
    x_goog_channel_id: str = Header(..., alias="X-Goog-Channel-ID"),
    x_goog_resource_state: str | None = Header(default=None, alias="X-Goog-Resource-State"),
) -> GoogleWebhookResponse:
    """Receive Drive changes.watch callbacks and queue a Drive change sync."""

    if x_goog_resource_state == "sync":
        return GoogleWebhookResponse(accepted=True, provider="drive", queued=False)

    cursor = await find_drive_cursor_by_channel(x_goog_channel_id)
    if cursor is None:
        logger.info("Ignoring Drive webhook for unknown channel %s", x_goog_channel_id)
        return GoogleWebhookResponse(accepted=True, provider="drive", queued=False)

    response = _queue_workspace_sync(
        background_tasks=background_tasks,
        org_id=cursor.org_id,
        user_id=cursor.user_id,
        source="drive",
        max_results=50,
    )
    return GoogleWebhookResponse(
        accepted=True,
        provider="drive",
        queued=True,
        job_id=response.job_id,
    )


@app.get("/integrations/google/calendar/events", response_model=CalendarEventsResponse)
async def calendar_events(
    ctx: Annotated[tuple[str, str], Depends(require_user_context)],
) -> CalendarEventsResponse:
    """Return upcoming events from the user's connected Google Calendar."""

    org_id, user_id = ctx
    return await get_calendar_events(org_id, user_id)


@app.delete("/integrations/google/calendar")
async def calendar_disconnect(
    ctx: Annotated[tuple[str, str], Depends(require_user_context)],
) -> dict[str, str]:
    """Disconnect Google Calendar for the current user."""

    org_id, user_id = ctx
    await disconnect_google_calendar(org_id, user_id)
    return {"status": "disconnected"}
