"""FastAPI application exposing the conversation ingestion endpoints.

The API is connector-agnostic: connectors (WhatsApp, Teams, Slack, ...) are
responsible for producing the canonical :class:`Conversation` format and posting
it to ``/ingest/conversation``. Authenticated data endpoints require an
``Authorization: Bearer`` MindLoom access JWT that scopes reads and writes to one
organization and user.
"""

from __future__ import annotations

import base64
import hmac
import json
import logging
from contextlib import asynccontextmanager
from typing import Annotated, AsyncIterator
from uuid import uuid4

from fastapi import BackgroundTasks, Body, Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from keeper.auth import (
    create_org,
    get_org_summary,
    get_user_access_tokens,
    google_signin,
    require_admin_context,
    require_org_id,
    require_user_context,
    verify_google_id_token,
)
from core.config import get_settings, validate_production_secrets
from core.database import close_pools
from core.llm_provider import LLMProviderError
from weaver.webhook_auth import verify_google_pubsub_oidc
from weaver.google_workspace import (
    connect_google_workspace_dev,
    find_drive_cursor_by_channel,
    find_gmail_cursor_by_email,
    handle_google_workspace_callback,
    run_workspace_sync_background,
    setup_drive_watch,
    setup_gmail_watch,
    start_google_workspace_oauth,
)
from weaver.microsoft_teams import (
    connect_microsoft_teams_dev,
    find_teams_cursor_by_subscription,
    graph_validation_response,
    handle_microsoft_teams_callback,
    run_teams_sync_background,
    setup_teams_channel_watch,
    start_microsoft_teams_oauth,
)
from weaver.integrations import (
    assert_dev_integrations_allowed,
    deferred_oauth_save,
    list_integrations,
)
from core.models import (
    AuthSessionResponse,
    Conversation,
    CreateOrgRequest,
    DirectoryIngestRequest,
    DirectoryIngestResult,
    DocumentMetadataInput,
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
    PublicAuthConfig,
    QueryRequest,
    QueryResponse,
    WorkspaceSyncStartResponse,
    WorkspaceWatchResponse,
    TeamsWatchRequest,
    TeamsWatchResponse,
    TeamsSyncStartResponse,
)
from api.routes.calendar import router as calendar_router
from api.routes.captures import router as captures_router
from api.routes.connection_setup import router as connection_setup_router
from api.routes.file_sources import router as file_sources_router
from api.routes.files import router as files_router
from api.routes.github import router as github_router
from api.routes.llm_settings import router as llm_settings_router
from api.routes.reviews import router as reviews_router
from api.routes.slack import router as slack_router
from api.routes.status import router as status_router
from api.routes.whatsapp import router as whatsapp_router
from api.routes.workflows import router as workflows_router
from api.routes.workspaces import router as workspaces_router
from api.routes.zoom import router as zoom_router
from brain.pipeline import run_ingestion_background, run_pdf_ingestion_background
from brain.retrieval import retrieve
from core.schema import ensure_schema
from brain.storage import fetch_knowledge_graph_debug, fetch_org_graph, upsert_directory
from core.jobs import job_store
from core.durable_jobs import enqueue, get_job
from brain.document_ingestion import ensure_supported_document
from core.health import check_readiness
from weaver.subscriptions import find_subscription

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Manage shared connection pools for the app lifecycle."""

    validate_production_secrets()
    logger.info("MindLoom ingestion service starting up")
    await ensure_schema()
    try:
        yield
    finally:
        await close_pools()
        logger.info("MindLoom ingestion service shut down")


_settings = get_settings()
_disable_docs = _settings.app_env == "production"
app = FastAPI(
    title="MindLoom — Conversation Ingestion",
    version="2.0.0",
    lifespan=lifespan,
    docs_url=None if _disable_docs else "/docs",
    redoc_url=None if _disable_docs else "/redoc",
    openapi_url=None if _disable_docs else "/openapi.json",
)

from core.uploads import (  # noqa: E402
    MAX_DOCUMENT_BYTES,
    MAX_EXTRACT_BYTES,
    read_upload,
)
from core.rate_limit import (  # noqa: E402
    actions_limit,
    auth_limit,
    ingest_limit,
    limit,
    limiter,
    query_limit,
    rate_limit,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.exception_handler(LLMProviderError)
async def llm_provider_error(_request: Request, exc: LLMProviderError) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": str(exc)})


app.add_middleware(SlowAPIMiddleware)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Headers that stop the API's responses being framed, sniffed or run as pages."""

    response = await call_next(request)
    headers = response.headers
    headers.setdefault("X-Content-Type-Options", "nosniff")
    headers.setdefault("X-Frame-Options", "DENY")
    headers.setdefault("Referrer-Policy", "no-referrer")
    if "content-disposition" in headers:
        # A downloaded or inline file is never allowed to run script on this origin.
        headers.setdefault("Content-Security-Policy", "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data: blob:; frame-ancestors 'none'")
    elif not request.url.path.startswith(("/docs", "/redoc")):
        headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
    if get_settings().app_env == "production":
        headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response

app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.resolved_cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["Authorization", "Content-Type", "X-Requested-With"],
)
app.include_router(calendar_router)
app.include_router(captures_router)
app.include_router(connection_setup_router)
app.include_router(file_sources_router)
app.include_router(files_router)
app.include_router(github_router)
app.include_router(llm_settings_router)
app.include_router(reviews_router)
app.include_router(slack_router)
app.include_router(status_router)
app.include_router(whatsapp_router)
app.include_router(workflows_router)
app.include_router(workspaces_router)
app.include_router(zoom_router)


@app.get("/health")
async def health() -> dict[str, str]:
    """Unauthenticated liveness probe for platform healthchecks."""

    return {"status": "ok"}


@app.get("/ready")
async def ready() -> dict[str, object]:
    """Readiness probe: Postgres, Redis, Neo4j, and fresh worker heartbeat."""

    return await check_readiness()


@app.get("/auth/public-config", response_model=PublicAuthConfig)
async def auth_public_config() -> PublicAuthConfig:
    """Google client id for GIS. Safe to expose (it is already in browser JS)."""

    settings = get_settings()
    return PublicAuthConfig(
        google_client_id=settings.google_client_id.strip(),
        google_oauth_enabled=settings.google_oauth_enabled,
    )


@app.post("/auth/google/signin", response_model=AuthSessionResponse)
@limit(auth_limit)
async def auth_google_signin(
    request: Request,
    body: Annotated[GoogleSignInRequest, Body()],
) -> AuthSessionResponse:
    """Verify a Google ID token and map the email domain to an existing organization."""

    identity = verify_google_id_token(body.id_token)
    return await google_signin(identity=identity)


@app.post("/orgs", response_model=AuthSessionResponse)
@limit(auth_limit)
async def create_organization(
    request: Request,
    body: Annotated[CreateOrgRequest, Body()],
) -> AuthSessionResponse:
    """Create a new organization; admin identity comes from a verified Google ID token."""

    identity = verify_google_id_token(body.id_token)
    try:
        return await create_org(
            name=body.name,
            domain=body.domain,
            identity=identity,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/auth/me", response_model=AuthSessionResponse)
async def auth_me(
    ctx: Annotated[tuple[str, str], Depends(require_user_context)],
) -> AuthSessionResponse:
    """Return the current session for a valid Bearer token (issues a fresh access token)."""

    from keeper.auth import get_org_by_id, get_user_by_id
    from keeper.session_tokens import issue_access_token

    org_id, user_id = ctx
    org = await get_org_by_id(org_id)
    user = await get_user_by_id(org_id, user_id)
    if org is None or user is None:
        raise HTTPException(status_code=401, detail="Invalid session.")
    return AuthSessionResponse(
        org_id=org.org_id,
        org_name=org.name,
        user_id=user.user_id,
        email=user.email,
        name=user.name,
        photo_url=user.photo_url,
        role=user.role,  # type: ignore[arg-type]
        access_token=issue_access_token(
            org_id=org.org_id,
            user_id=user.user_id,
            role=user.role,
            email=user.email,
        ),
    )


@app.get("/org/summary", response_model=OrgSummaryResponse)
async def org_summary(org_id: Annotated[str, Depends(require_org_id)]) -> OrgSummaryResponse:
    """Return org-scoped counts for dashboard and setup completion."""

    return await get_org_summary(org_id)


@app.post("/ingest/conversation")
@limit(ingest_limit)
async def ingest_conversation(
    request: Request,
    background_tasks: BackgroundTasks,
    conversation: Annotated[Conversation, Body()],
    ctx: Annotated[tuple[str, str], Depends(require_user_context)],
    visibility: Annotated[str | None, Query()] = None,
) -> dict[str, str]:
    """Ingest a canonical conversation produced by any connector."""

    if not conversation.participants:
        raise HTTPException(status_code=400, detail="Conversation must have at least one participant.")
    if not conversation.messages:
        raise HTTPException(status_code=400, detail="Conversation must have at least one message.")
    if visibility is not None and visibility not in ("private", "organization"):
        raise HTTPException(
            status_code=400,
            detail="visibility must be 'private' or 'organization'.",
        )

    org_id, user_id = ctx
    from keeper.visibility_acl import permissions_for_upload

    visible_to = permissions_for_upload(
        org_id=org_id,
        uploaded_by=user_id,
        visibility=visibility or "private",
    )
    job_id = await enqueue(
        "conversation",
        org_id=org_id,
        conversation_id=conversation.conversation_id,
        payload={
            "conversation_id": conversation.conversation_id,
            "conversation": conversation.model_dump(mode="json"),
            "uploaded_by": user_id,
            "visible_to": visible_to,
        },
    )

    return {"job_id": job_id, "status": "queued"}


@app.post("/ingest/pdf")
@limit(ingest_limit)
async def ingest_pdf(
    request: Request,
    background_tasks: BackgroundTasks,
    ctx: Annotated[tuple[str, str], Depends(require_user_context)],
    file: Annotated[UploadFile, File()],
    visibility: Annotated[str, Form()] = "private",
) -> dict[str, str]:
    """Ingest an uploaded PDF: store it, chunk it structurally, and classify.

    Like every manual upload it is private to the uploader unless ``visibility``
    is "organization".
    """

    from keeper.visibility_acl import permissions_for_upload

    org_id, user_id = ctx
    if visibility not in {"private", "organization"}:
        raise HTTPException(status_code=400, detail="visibility must be private or organization.")

    filename = file.filename or "upload.pdf"
    is_pdf = filename.lower().endswith(".pdf") or file.content_type == "application/pdf"
    if not is_pdf:
        raise HTTPException(status_code=400, detail="Expected a PDF file.")

    data = await read_upload(file, MAX_DOCUMENT_BYTES)
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded PDF is empty.")

    job_id = await enqueue(
        "pdf",
        org_id=org_id,
        conversation_id=filename,
        payload={
            "conversation_id": filename,
            "filename": filename,
            "data": base64.b64encode(data).decode("ascii"),
            "visible_to": permissions_for_upload(
                org_id=org_id, uploaded_by=user_id, visibility=visibility
            ),
            "uploaded_by": user_id,
        },
    )

    return {"job_id": job_id, "status": "queued"}


@app.post("/ingest/document")
@limit(ingest_limit)
async def ingest_company_document(
    request: Request,
    ctx: Annotated[tuple[str, str], Depends(require_user_context)],
    file: Annotated[UploadFile, File()],
    metadata_json: Annotated[str, Form()] = "{}",
) -> dict:
    """Queue a company document (or every document in a ZIP) with optional metadata."""

    org_id, user_id = ctx
    filename = file.filename or "document"
    is_archive = filename.lower().endswith(".zip")
    try:
        if not is_archive:
            ensure_supported_document(filename)
        metadata = DocumentMetadataInput.model_validate_json(metadata_json)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    data = await read_upload(file, MAX_DOCUMENT_BYTES)
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded document is empty.")
    if is_archive:
        return await _ingest_archive(filename, data, org_id, user_id, metadata)
    job_id = await enqueue(
        "document",
        org_id=org_id,
        conversation_id=filename,
        payload={
            "conversation_id": filename,
            "filename": filename,
            "mime_type": file.content_type or "application/octet-stream",
            "data": base64.b64encode(data).decode("ascii"),
            "uploaded_by": user_id,
            "metadata": metadata.model_dump(mode="json"),
        },
    )
    return {"job_id": job_id, "status": "queued"}


async def _ingest_archive(
    filename: str,
    data: bytes,
    org_id: str,
    user_id: str,
    metadata: DocumentMetadataInput,
) -> dict:
    """Queue every supported document inside a ZIP as its own ingestion job."""

    import mimetypes

    from brain.archives import ArchiveError, read_archive
    from brain.document_ingestion import SUPPORTED_DOCUMENT_EXTENSIONS

    def supported(path: str) -> bool:
        return any(path.lower().endswith(ext) for ext in SUPPORTED_DOCUMENT_EXTENSIONS)

    try:
        contents = read_archive(data, accept=supported)
    except ArchiveError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not contents.entries:
        raise HTTPException(status_code=400, detail="The ZIP has no supported documents.")
    base = metadata.model_dump(mode="json")
    job_ids: list[str] = []
    for entry in contents.entries:
        folder = entry.path.rsplit("/", 1)[0] if "/" in entry.path else ""
        entry_metadata = {
            **base,
            # One title for many files would hide which is which; use each file's name.
            "title": base.get("title") if len(contents.entries) == 1 and base.get("title") else entry.name,
            "folder_path": base.get("folder_path") or "/".join(p for p in (filename, folder) if p),
        }
        job_ids.append(await enqueue(
            "document",
            org_id=org_id,
            conversation_id=entry.name,
            payload={
                "conversation_id": entry.name,
                "filename": entry.name,
                "mime_type": mimetypes.guess_type(entry.name)[0] or "application/octet-stream",
                "data": base64.b64encode(entry.data).decode("ascii"),
                "uploaded_by": user_id,
                "metadata": entry_metadata,
            },
        ))
    return {
        "job_id": job_ids[0],
        "status": "queued",
        "job_ids": job_ids,
        "files": [entry.path for entry in contents.entries],
        "skipped": contents.skipped,
    }


@app.post("/ingest/directory", response_model=DirectoryIngestResult)
@limit(ingest_limit)
async def ingest_directory(
    request: Request,
    body: Annotated[DirectoryIngestRequest, Body()],
    ctx: Annotated[tuple[str, str], Depends(require_admin_context)],
) -> DirectoryIngestResult:
    """Upsert an org-directory import (CSV / Google / ...) into the graph."""

    if not body.people:
        raise HTTPException(status_code=400, detail="Directory import contains no people.")

    try:
        org_id, _ = ctx
        return await upsert_directory(body.people, org_id, source=body.source)
    except Exception as e:  # noqa: BLE001 - surface any failure as HTTP 500
        logger.exception("Request failed")
        raise HTTPException(status_code=500, detail="Something went wrong. Please try again.") from e


@app.get("/org/graph", response_model=OrgGraphResponse)
async def get_org_graph(
    org_id: Annotated[str, Depends(require_org_id)],
) -> OrgGraphResponse:
    """Return the organization graph (people + reporting edges) for the UI."""

    try:
        return await fetch_org_graph(org_id)
    except Exception as e:  # noqa: BLE001 - surface any failure as HTTP 500
        logger.exception("Request failed")
        raise HTTPException(status_code=500, detail="Something went wrong. Please try again.") from e


@app.get("/graph/debug", response_model=KnowledgeGraphResponse)
async def get_knowledge_graph_debug(
    ctx: Annotated[tuple[str, str], Depends(require_admin_context)],
) -> KnowledgeGraphResponse:
    """Return all knowledge-graph nodes and edges for dev/debug visualisation."""

    if get_settings().app_env == "production":
        raise HTTPException(status_code=404, detail="Not found.")
    try:
        org_id, _ = ctx
        return await fetch_knowledge_graph_debug(org_id)
    except Exception as e:  # noqa: BLE001 - surface any failure as HTTP 500
        logger.exception("Request failed")
        raise HTTPException(status_code=500, detail="Something went wrong. Please try again.") from e


@app.get("/ingest/status/{job_id}", response_model=JobStatus)
async def get_job_status(
    job_id: str,
    org_id: Annotated[str, Depends(require_org_id)],
) -> JobStatus:
    """Return the current status of an ingestion job, or 404 if unknown."""

    job = await get_job(job_id, org_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"No job found with id '{job_id}'.")
    if job.org_id and job.org_id != org_id:
        raise HTTPException(status_code=404, detail=f"No job found with id '{job_id}'.")
    return job


@app.post("/query", response_model=QueryResponse)
@limit(query_limit)
async def query(
    request: Request,
    body: Annotated[QueryRequest, Body()],
    ctx: Annotated[tuple[str, str], Depends(require_user_context)],
) -> QueryResponse:
    """Answer a natural-language question from the knowledge base."""

    try:
        org_id, user_id = ctx
        access_tokens = await get_user_access_tokens(org_id, user_id)
        retrieval = await retrieve(
            body.question, body.history, org_id, access_tokens
        )
        from loombot.ask_agent import request_expert_if_routed, run_ask_agent

        response = await run_ask_agent(
            question=body.question,
            retrieval=retrieval,
            history=body.history,
            org_id=org_id,
            user_id=user_id,
            ephemeral_documents=body.ephemeral_documents,
            attached_file_names=body.attached_file_names,
            user_timezone=body.timezone,
        )
        await request_expert_if_routed(
            response, org_id=org_id, user_id=user_id, question=body.question
        )
        return response
    except Exception as e:  # noqa: BLE001 - surface any failure as HTTP 500
        logger.exception("Request failed")
        raise HTTPException(status_code=500, detail="Something went wrong. Please try again.") from e


@app.post("/files/extract", response_model=FileExtractResponse)
@limit(ingest_limit)
async def extract_uploaded_file(
    request: Request,
    org_id: Annotated[str, Depends(require_org_id)],
    file: Annotated[UploadFile, File()],
) -> FileExtractResponse:
    """Extract text from an uploaded file for chat-only (ephemeral) context."""

    filename = file.filename or "upload"
    data = await read_upload(file, MAX_EXTRACT_BYTES)
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    try:
        from brain.media_text import extract_text_with_ocr, no_text_reason

        text, _method = await extract_text_with_ocr(filename, data)
    except Exception as exc:  # noqa: BLE001
        # Reader errors (password-protected PDF, bad archive) are written for people;
        # anything else is logged, not echoed.
        if not isinstance(exc, ValueError):
            logger.exception("Could not read uploaded file")
        message = str(exc) if isinstance(exc, ValueError) else "the file could not be read."
        raise HTTPException(status_code=400, detail=f"Could not read file: {message}") from exc
    if not text:
        raise HTTPException(status_code=400, detail=no_text_reason(filename, data))
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
        logger.exception("Request failed")
        raise HTTPException(status_code=500, detail="Something went wrong. Please try again.") from e


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
        logger.exception("Request failed")
        raise HTTPException(status_code=500, detail="Something went wrong. Please try again.") from e


@app.post("/integrations/google/workspace/connect-dev", response_model=IntegrationsListResponse)
async def dev_connect_google_workspace(
    ctx: Annotated[tuple[str, str], Depends(require_user_context)],
) -> IntegrationsListResponse:
    """Simulated Workspace connect when Google OAuth credentials are not configured."""

    assert_dev_integrations_allowed()
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
        logger.exception("Request failed")
        raise HTTPException(status_code=500, detail="Something went wrong. Please try again.") from e


@app.post("/integrations/microsoft/teams/connect-dev", response_model=IntegrationsListResponse)
async def dev_connect_microsoft_teams(
    ctx: Annotated[tuple[str, str], Depends(require_user_context)],
) -> IntegrationsListResponse:
    """Simulated Microsoft Teams connect when Microsoft OAuth is not configured."""

    assert_dev_integrations_allowed()
    org_id, user_id = ctx
    await connect_microsoft_teams_dev(org_id, user_id)
    return await list_integrations(org_id, user_id)


def _with_oauth_code(url: str, parked: dict[str, str]) -> str:
    """The web app finishes the connection with this one-time code (see weaver.integrations)."""

    code = parked.get("code")
    return f"{url}&oauth_code={code}" if code else url


class OAuthCompleteInput(BaseModel):
    code: str = Field(min_length=10, max_length=200)


@app.post("/integrations/oauth/complete")
async def complete_oauth(
    body: OAuthCompleteInput,
    ctx: Annotated[tuple[str, str], Depends(require_user_context)],
) -> dict[str, str]:
    """Save a connection the signed-in person just authorized (and only theirs)."""

    from weaver.integrations import complete_oauth_connection

    return await complete_oauth_connection(body.code, *ctx)


@app.get("/integrations/google/workspace/callback")
async def google_workspace_callback(
    code: str = Query(...),
    state: str = Query(...),
) -> RedirectResponse:
    """OAuth callback for Gmail/Drive sync consent."""

    try:
        with deferred_oauth_save() as parked:
            redirect_url = await handle_google_workspace_callback(code, state)
        return RedirectResponse(url=_with_oauth_code(redirect_url, parked), status_code=302)
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
        with deferred_oauth_save() as parked:
            redirect_url = await handle_microsoft_teams_callback(code, state)
        return RedirectResponse(url=_with_oauth_code(redirect_url, parked), status_code=302)
    except HTTPException as exc:
        settings = get_settings()
        detail = exc.detail if isinstance(exc.detail, str) else "oauth_failed"
        url = f"{settings.frontend_url.rstrip('/')}/dashboard?tab=apps&error={detail}"
        return RedirectResponse(url=url, status_code=302)


@app.post("/integrations/microsoft/teams/watch", response_model=TeamsWatchResponse)
async def watch_microsoft_teams(
    ctx: Annotated[tuple[str, str], Depends(require_user_context)],
    body: Annotated[TeamsWatchRequest, Body()],
) -> TeamsWatchResponse:
    """Start or renew a Microsoft Graph subscription for one Teams channel."""

    org_id, user_id = ctx
    return await setup_teams_channel_watch(
        org_id,
        user_id,
        team_id=body.team_id,
        channel_id=body.channel_id,
    )


async def _queue_teams_sync(
    *,
    background_tasks: BackgroundTasks,
    org_id: str,
    user_id: str,
    max_results: int,
) -> TeamsSyncStartResponse:
    job_id = await enqueue(
        "microsoft_sync",
        org_id=org_id,
        conversation_id="microsoft:teams-sharepoint",
        payload={"user_id": user_id, "max_results": max_results},
    )
    return TeamsSyncStartResponse(job_id=job_id, status="queued", source="teams")


@app.post("/integrations/microsoft/teams/sync", response_model=TeamsSyncStartResponse, dependencies=[rate_limit(actions_limit, "actions")])
async def sync_microsoft_teams_now(
    background_tasks: BackgroundTasks,
    ctx: Annotated[tuple[str, str], Depends(require_user_context)],
    max_results: int = Query(25, ge=1, le=100),
) -> TeamsSyncStartResponse:
    """Queue an immediate Microsoft Teams sync for the current user."""

    org_id, user_id = ctx
    return await _queue_teams_sync(
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


async def _queue_workspace_sync(
    *,
    background_tasks: BackgroundTasks,
    org_id: str,
    user_id: str,
    source: str,
    max_results: int,
    override_history_id: str | None = None,
) -> WorkspaceSyncStartResponse:
    job_id = await enqueue(
        "google_sync",
        org_id=org_id,
        conversation_id=f"google:{source}",
        payload={
            "user_id": user_id,
            "source": source,
            "max_results": max_results,
            "override_history_id": override_history_id,
        },
    )
    return WorkspaceSyncStartResponse(
        job_id=job_id,
        status="queued",
        source=source,  # type: ignore[arg-type]
    )


@app.post("/integrations/google/workspace/gmail/sync", response_model=WorkspaceSyncStartResponse, dependencies=[rate_limit(actions_limit, "actions")])
async def sync_gmail_now(
    background_tasks: BackgroundTasks,
    ctx: Annotated[tuple[str, str], Depends(require_user_context)],
    max_results: int = Query(25, ge=1, le=100),
) -> WorkspaceSyncStartResponse:
    """Queue an immediate Gmail incremental/backfill sync for the current user."""

    org_id, user_id = ctx
    return await _queue_workspace_sync(
        background_tasks=background_tasks,
        org_id=org_id,
        user_id=user_id,
        source="gmail",
        max_results=max_results,
    )


@app.post("/integrations/google/workspace/drive/sync", response_model=WorkspaceSyncStartResponse, dependencies=[rate_limit(actions_limit, "actions")])
async def sync_drive_now(
    background_tasks: BackgroundTasks,
    ctx: Annotated[tuple[str, str], Depends(require_user_context)],
    max_results: int = Query(25, ge=1, le=100),
) -> WorkspaceSyncStartResponse:
    """Queue an immediate Drive changes sync for the current user."""

    org_id, user_id = ctx
    return await _queue_workspace_sync(
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

    expected_state = get_settings().microsoft_graph_client_state.strip()
    if not expected_state:
        raise HTTPException(
            status_code=503,
            detail="Microsoft Graph client state is not configured.",
        )

    queued = False
    job_id: str | None = None
    for item in values:
        if not isinstance(item, dict):
            raise HTTPException(status_code=401, detail="Invalid Microsoft notification payload.")
        if not hmac.compare_digest(str(item.get("clientState") or ""), expected_state):
            raise HTTPException(status_code=401, detail="Invalid Microsoft clientState.")
        subscription_id = str(item.get("subscriptionId") or "")
        if not subscription_id:
            continue
        cursor = await find_teams_cursor_by_subscription(subscription_id)
        if cursor is None:
            cursor = await find_subscription("sharepoint", subscription_id)
        if cursor is None:
            for provider in ("outlook_mail", "outlook_calendar", "teams_chat"):
                cursor = await find_subscription(provider, subscription_id)
                if cursor is not None:
                    break
        if cursor is None:
            continue
        if item.get("changeType") == "deleted":
            from brain.source_registry import mark_external_source_deleted

            resource_data = item.get("resourceData") or {}
            message_id = (
                str(resource_data.get("id") or "")
                if isinstance(resource_data, dict)
                else ""
            )
            parts = cursor.resource.strip("/").split("/")
            if len(parts) >= 4 and message_id:
                await mark_external_source_deleted(
                    cursor.org_id,
                    "teams",
                    f"{parts[1]}:{parts[3]}:{message_id}",
                )
            continue
        response = await _queue_teams_sync(
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
    request: Request,
    envelope: Annotated[GooglePubSubEnvelope, Body()],
    background_tasks: BackgroundTasks,
) -> GoogleWebhookResponse:
    """Receive Google Pub/Sub push messages, currently Gmail mailbox updates."""

    verify_google_pubsub_oidc(request)
    payload = _decode_pubsub_data(envelope)
    email = str(payload.get("emailAddress") or "").lower()
    history_id = str(payload.get("historyId") or "")
    if not email or not history_id:
        return GoogleWebhookResponse(accepted=True, provider=None, queued=False)

    cursor = await find_gmail_cursor_by_email(email)
    if cursor is None:
        logger.info("Ignoring Gmail Pub/Sub update for unregistered mailbox %s", email)
        return GoogleWebhookResponse(accepted=True, provider="gmail", queued=False)

    response = await _queue_workspace_sync(
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
    x_goog_channel_token: str | None = Header(default=None, alias="X-Goog-Channel-Token"),
) -> GoogleWebhookResponse:
    """Receive Drive changes.watch callbacks and queue a Drive change sync."""

    expected_token = get_settings().google_drive_webhook_secret.strip()
    if not expected_token:
        raise HTTPException(
            status_code=503,
            detail="Drive webhook secret is not configured.",
        )
    if not hmac.compare_digest(str(x_goog_channel_token or ""), str(expected_token or "")):
        raise HTTPException(status_code=401, detail="Invalid Drive channel token.")
    if x_goog_resource_state == "sync":
        return GoogleWebhookResponse(accepted=True, provider="drive", queued=False)

    cursor = await find_drive_cursor_by_channel(x_goog_channel_id)
    if cursor is None:
        logger.info("Ignoring Drive webhook for unknown channel %s", x_goog_channel_id)
        return GoogleWebhookResponse(accepted=True, provider="drive", queued=False)

    response = await _queue_workspace_sync(
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
