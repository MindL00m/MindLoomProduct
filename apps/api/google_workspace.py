"""Google Workspace Gmail/Drive sync endpoints and connector helpers.

This module is intentionally a thin connector layer over the existing ingestion
pipeline. Gmail threads are normalized into canonical ``Conversation`` objects;
Drive Docs/Sheets/Slides are exported as text and represented as a synthetic
single-speaker conversation so they can reuse the same chunk/extract/embed/store
path without introducing a second document-ingestion pipeline yet.
"""

from __future__ import annotations

import base64
import json
import logging
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Literal
from uuid import uuid4

import httpx
from fastapi import HTTPException
from google_auth_oauthlib.flow import Flow
from sqlalchemy import DateTime, String, Text, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Mapped, mapped_column

from auth import Base, UserRow
from config import get_settings
from database import get_session_factory
from integrations import (
    _fetch_user_email,
    _get_connection,
    _pop_oauth_state,
    _refresh_token_if_needed,
    _save_connection,
    _store_oauth_state,
)
from models import (
    Conversation,
    IncomingMessage,
    JobStatus,
    OAuthAuthorizeResponse,
    Participant,
    WorkspaceWatchResponse,
)
from pipeline import DocumentInput, run_ingestion
from source_registry import ingest_external_source, mark_external_source_deleted
from provider_http import request_with_backoff
from file_extract import extract_file_text
from subscriptions import find_subscription, upsert_subscription

logger = logging.getLogger(__name__)

PROVIDER_GOOGLE_WORKSPACE = "google_workspace"
SYNC_PROVIDER_GMAIL = "gmail"
SYNC_PROVIDER_DRIVE = "drive"

WORKSPACE_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/drive.metadata.readonly",
]

GOOGLE_DOC = "application/vnd.google-apps.document"
GOOGLE_SHEET = "application/vnd.google-apps.spreadsheet"
GOOGLE_SLIDE = "application/vnd.google-apps.presentation"

EXPORT_FORMATS = {
    GOOGLE_DOC: ("text/plain", ".txt"),
    GOOGLE_SHEET: ("text/csv", ".csv"),
    GOOGLE_SLIDE: ("text/plain", ".txt"),
}


def _parse_google_datetime(value: object) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _google_application(mime_type: str) -> str:
    return {
        GOOGLE_DOC: "Google Docs",
        GOOGLE_SHEET: "Google Sheets",
        GOOGLE_SLIDE: "Google Slides",
        "application/pdf": "PDF",
    }.get(mime_type, "Google Drive")


class SyncCursorRow(Base):
    """Durable incremental sync cursor for one user/source."""

    __tablename__ = "sync_cursors"

    cursor_id: Mapped[str] = mapped_column(String, primary_key=True)
    org_id: Mapped[str] = mapped_column(String, nullable=False)
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    account_email: Mapped[str] = mapped_column(String, nullable=False)
    cursor_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    watch_resource: Mapped[str | None] = mapped_column(Text, nullable=True)
    watch_expiration: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


def _workspace_flow() -> Flow:
    settings = get_settings()
    return Flow.from_client_config(
        {
            "web": {
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=WORKSPACE_SCOPES,
        redirect_uri=settings.google_workspace_oauth_redirect_uri,
    )


async def start_google_workspace_oauth(org_id: str, user_id: str) -> OAuthAuthorizeResponse:
    """Return a Google OAuth URL requesting Gmail + Drive read scopes."""

    settings = get_settings()
    if not settings.google_oauth_enabled:
        raise HTTPException(
            status_code=503,
            detail=(
                "Google OAuth is not configured. Set GOOGLE_CLIENT_ID and "
                "GOOGLE_CLIENT_SECRET before connecting Workspace sync."
            ),
        )

    flow = _workspace_flow()
    state = _store_oauth_state(org_id, user_id)
    authorization_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=state,
    )
    return OAuthAuthorizeResponse(authorization_url=authorization_url)


async def handle_google_workspace_callback(code: str, state: str) -> str:
    """Exchange OAuth code for Gmail/Drive tokens and redirect to Apps."""

    settings = get_settings()
    org_id, user_id = _pop_oauth_state(state)

    flow = _workspace_flow()
    flow.fetch_token(code=code)
    creds = flow.credentials
    account_email = await _fetch_user_email(creds.token or "")

    expiry = creds.expiry
    if expiry is not None and expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)

    await _save_connection(
        org_id=org_id,
        user_id=user_id,
        provider=PROVIDER_GOOGLE_WORKSPACE,
        account_email=account_email,
        access_token=creds.token or "",
        refresh_token=creds.refresh_token,
        token_expiry=expiry,
        scopes=" ".join(creds.scopes or WORKSPACE_SCOPES),
    )

    return f"{settings.frontend_url.rstrip('/')}/dashboard?tab=apps&setup=google_workspace"


async def connect_google_workspace_dev(org_id: str, user_id: str) -> None:
    """Create a non-network dev Workspace connection."""

    settings = get_settings()
    if settings.google_oauth_enabled:
        raise HTTPException(
            status_code=400,
            detail="Google OAuth is configured — use the real Workspace connect flow.",
        )

    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(select(UserRow).where(UserRow.user_id == user_id))
        user = result.scalar_one()

    await _save_connection(
        org_id=org_id,
        user_id=user_id,
        provider=PROVIDER_GOOGLE_WORKSPACE,
        account_email=user.email,
        access_token=f"dev:{user_id}",
        refresh_token=None,
        token_expiry=None,
        scopes="dev",
    )


async def _workspace_access_token(org_id: str, user_id: str) -> tuple[str, str]:
    row = await _get_connection(org_id, user_id, PROVIDER_GOOGLE_WORKSPACE)
    if row is None:
        raise HTTPException(status_code=404, detail="Google Workspace is not connected.")
    if row.access_token.startswith("dev:"):
        return row.access_token, row.account_email or "dev@example.com"
    row = await _refresh_token_if_needed(row)
    return row.access_token, row.account_email or ""


async def _upsert_cursor(
    *,
    org_id: str,
    user_id: str,
    provider: str,
    account_email: str,
    cursor_value: str | None = None,
    watch_resource: str | None = None,
    watch_expiration: datetime | None = None,
    status: str = "active",
    mark_synced: bool = False,
) -> SyncCursorRow:
    now = datetime.now(timezone.utc)
    values = {
        "cursor_id": str(uuid4()),
        "org_id": org_id,
        "user_id": user_id,
        "provider": provider,
        "account_email": account_email,
        "cursor_value": cursor_value,
        "watch_resource": watch_resource,
        "watch_expiration": watch_expiration,
        "status": status,
        "last_synced_at": now if mark_synced else None,
        "created_at": now,
        "updated_at": now,
    }
    update_values = {
        "account_email": account_email,
        "status": status,
        "updated_at": now,
    }
    if cursor_value is not None:
        update_values["cursor_value"] = cursor_value
    if watch_resource is not None:
        update_values["watch_resource"] = watch_resource
    if watch_expiration is not None:
        update_values["watch_expiration"] = watch_expiration
    if mark_synced:
        update_values["last_synced_at"] = now

    session_factory = get_session_factory()
    async with session_factory() as session:
        async with session.begin():
            stmt = pg_insert(SyncCursorRow).values(**values)
            stmt = stmt.on_conflict_do_update(
                index_elements=["org_id", "user_id", "provider"],
                set_=update_values,
            )
            await session.execute(stmt)
        result = await session.execute(
            select(SyncCursorRow).where(
                SyncCursorRow.org_id == org_id,
                SyncCursorRow.user_id == user_id,
                SyncCursorRow.provider == provider,
            )
        )
        return result.scalar_one()


async def _get_cursor(
    org_id: str, user_id: str, provider: str
) -> SyncCursorRow | None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(
            select(SyncCursorRow).where(
                SyncCursorRow.org_id == org_id,
                SyncCursorRow.user_id == user_id,
                SyncCursorRow.provider == provider,
            )
        )
        return result.scalar_one_or_none()


async def find_gmail_cursor_by_email(email: str) -> SyncCursorRow | None:
    """Find the org/user cursor for a Gmail Pub/Sub payload email."""

    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(
            select(SyncCursorRow).where(
                SyncCursorRow.provider == SYNC_PROVIDER_GMAIL,
                SyncCursorRow.account_email == email.lower(),
            )
        )
        return result.scalar_one_or_none()


async def find_drive_cursor_by_channel(channel_id: str):
    """Find the persistent routing record for a Drive webhook channel."""

    return await find_subscription(SYNC_PROVIDER_DRIVE, channel_id)


def _watch_response(provider: Literal["gmail", "drive"], row: SyncCursorRow) -> WorkspaceWatchResponse:
    return WorkspaceWatchResponse(
        provider=provider,
        account_email=row.account_email,
        cursor=row.cursor_value,
        expiration=row.watch_expiration,
        status=row.status,
    )


def _expiration_from_millis(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
    except (TypeError, ValueError):
        return None


async def setup_gmail_watch(org_id: str, user_id: str) -> WorkspaceWatchResponse:
    """Call Gmail users.watch for INBOX changes and persist the history cursor."""

    settings = get_settings()
    access_token, account_email = await _workspace_access_token(org_id, user_id)
    if access_token.startswith("dev:"):
        row = await _upsert_cursor(
            org_id=org_id,
            user_id=user_id,
            provider=SYNC_PROVIDER_GMAIL,
            account_email=account_email.lower(),
            cursor_value="dev-history",
        )
        return _watch_response(SYNC_PROVIDER_GMAIL, row)
    if not settings.google_pubsub_topic:
        raise HTTPException(status_code=400, detail="GOOGLE_PUBSUB_TOPIC is not configured.")

    body = {
        "topicName": settings.google_pubsub_topic,
        "labelIds": ["INBOX"],
        "labelFilterBehavior": "INCLUDE",
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            "https://gmail.googleapis.com/gmail/v1/users/me/watch",
            json=body,
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if resp.status_code >= 400:
        logger.warning("Gmail watch failed %s: %s", resp.status_code, resp.text[:300])
        raise HTTPException(status_code=502, detail="Could not start Gmail watch.")

    payload = resp.json()
    row = await _upsert_cursor(
        org_id=org_id,
        user_id=user_id,
        provider=SYNC_PROVIDER_GMAIL,
        account_email=account_email.lower(),
        cursor_value=str(payload.get("historyId")) if payload.get("historyId") else None,
        watch_expiration=_expiration_from_millis(payload.get("expiration")),
    )
    return _watch_response(SYNC_PROVIDER_GMAIL, row)


async def setup_drive_watch(org_id: str, user_id: str) -> WorkspaceWatchResponse:
    """Create a Drive changes channel and persist the current startPageToken."""

    settings = get_settings()
    access_token, account_email = await _workspace_access_token(org_id, user_id)
    if access_token.startswith("dev:"):
        row = await _upsert_cursor(
            org_id=org_id,
            user_id=user_id,
            provider=SYNC_PROVIDER_DRIVE,
            account_email=account_email.lower(),
            cursor_value="dev-start-token",
        )
        return _watch_response(SYNC_PROVIDER_DRIVE, row)
    if not settings.google_drive_webhook_url:
        raise HTTPException(status_code=400, detail="GOOGLE_DRIVE_WEBHOOK_URL is not configured.")

    async with httpx.AsyncClient(timeout=20.0) as client:
        token_resp = await client.get(
            "https://www.googleapis.com/drive/v3/changes/startPageToken",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"supportsAllDrives": "true"},
        )
        if token_resp.status_code >= 400:
            logger.warning("Drive startPageToken failed %s: %s", token_resp.status_code, token_resp.text[:300])
            raise HTTPException(status_code=502, detail="Could not get Drive start page token.")
        start_token = token_resp.json().get("startPageToken")

        channel_id = str(uuid4())
        watch_resp = await client.post(
            "https://www.googleapis.com/drive/v3/changes/watch",
            headers={"Authorization": f"Bearer {access_token}"},
            params={
                "pageToken": start_token,
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
            },
            json={
                "id": channel_id,
                "type": "web_hook",
                "address": settings.google_drive_webhook_url,
                "token": settings.google_drive_webhook_secret,
                "expiration": str(
                    int((datetime.now(timezone.utc) + timedelta(days=6)).timestamp() * 1000)
                ),
            },
        )
    if watch_resp.status_code >= 400:
        logger.warning("Drive watch failed %s: %s", watch_resp.status_code, watch_resp.text[:300])
        raise HTTPException(status_code=502, detail="Could not start Drive watch.")

    payload = watch_resp.json()
    expiration = _expiration_from_millis(payload.get("expiration"))
    row = await _upsert_cursor(
        org_id=org_id,
        user_id=user_id,
        provider=SYNC_PROVIDER_DRIVE,
        account_email=account_email.lower(),
        cursor_value=str(start_token) if start_token else None,
        watch_resource=f"{channel_id}:{payload.get('resourceId', '')}",
        watch_expiration=expiration,
    )
    await upsert_subscription(
        org_id=org_id,
        user_id=user_id,
        provider=SYNC_PROVIDER_DRIVE,
        external_id=channel_id,
        resource="changes",
        resource_id=str(payload.get("resourceId") or ""),
        expiration=expiration,
    )
    return _watch_response(SYNC_PROVIDER_DRIVE, row)


def _b64decode(data: str) -> bytes:
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def _gmail_headers(message: dict[str, Any]) -> dict[str, str]:
    headers = message.get("payload", {}).get("headers", [])
    result: dict[str, str] = {}
    for header in headers:
        name = str(header.get("name", "")).lower()
        value = str(header.get("value", ""))
        if name:
            result[name] = value
    return result


def _gmail_body_from_part(part: dict[str, Any]) -> str:
    body = part.get("body", {})
    data = body.get("data")
    mime_type = part.get("mimeType")
    if data and mime_type in {"text/plain", "text/html"}:
        text = _b64decode(str(data)).decode("utf-8", errors="replace")
        return text
    for child in part.get("parts", []) or []:
        text = _gmail_body_from_part(child)
        if text:
            return text
    return ""


def _gmail_timestamp(headers: dict[str, str], fallback_ms: str | None) -> datetime:
    date_header = headers.get("date")
    if date_header:
        try:
            return parsedate_to_datetime(date_header).astimezone(timezone.utc)
        except (TypeError, ValueError):
            pass
    if fallback_ms:
        try:
            return datetime.fromtimestamp(int(fallback_ms) / 1000, tz=timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _sender_id(email_or_name: str) -> str:
    return email_or_name.strip().lower() or "unknown"


def _message_to_conversation(message: dict[str, Any], account_email: str) -> Conversation:
    headers = _gmail_headers(message)
    sender = headers.get("from", "Unknown sender")
    subject = headers.get("subject", "(No subject)")
    timestamp = _gmail_timestamp(headers, message.get("internalDate"))
    body = _gmail_body_from_part(message.get("payload", {})).strip()
    if not body:
        body = message.get("snippet", "")
    text = f"Subject: {subject}\nFrom: {sender}\nTo: {headers.get('to', account_email)}\n\n{body}"
    participant_id = _sender_id(sender)
    return Conversation(
        source="gmail",
        conversation_id=f"gmail:{message.get('threadId') or message.get('id')}",
        title=subject,
        participants=[Participant(id=participant_id, name=sender)],
        messages=[
            IncomingMessage(
                id=str(message.get("id") or uuid4()),
                sender=participant_id,
                timestamp=timestamp,
                text=text,
            )
        ],
    )


async def _fetch_gmail_message(client: httpx.AsyncClient, access_token: str, message_id: str) -> dict[str, Any] | None:
    resp = await client.get(
        f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}",
        headers={"Authorization": f"Bearer {access_token}"},
        params={"format": "full"},
    )
    if resp.status_code == 404:
        return None
    if resp.status_code >= 400:
        logger.warning("Gmail message fetch failed %s: %s", resp.status_code, resp.text[:200])
        return None
    return resp.json()


async def sync_gmail(
    org_id: str,
    user_id: str,
    *,
    max_results: int = 25,
    override_history_id: str | None = None,
) -> int:
    """Fetch new Gmail INBOX messages and ingest them into the graph."""

    access_token, account_email = await _workspace_access_token(org_id, user_id)
    if access_token.startswith("dev:"):
        await _upsert_cursor(
            org_id=org_id,
            user_id=user_id,
            provider=SYNC_PROVIDER_GMAIL,
            account_email=account_email.lower(),
            cursor_value=override_history_id or "dev-history",
            mark_synced=True,
        )
        return 0

    cursor = await _get_cursor(org_id, user_id, SYNC_PROVIDER_GMAIL)
    start_history_id = override_history_id or (cursor.cursor_value if cursor else None)
    message_ids: list[str] = []
    newest_history_id: str | None = None

    async with httpx.AsyncClient(timeout=30.0) as client:
        if start_history_id:
            params = {
                "startHistoryId": start_history_id,
                "historyTypes": "messageAdded",
                "labelId": "INBOX",
                "maxResults": str(max_results),
            }
            resp = await client.get(
                "https://gmail.googleapis.com/gmail/v1/users/me/history",
                headers={"Authorization": f"Bearer {access_token}"},
                params=params,
            )
            if resp.status_code == 404:
                start_history_id = None
            elif resp.status_code >= 400:
                logger.warning("Gmail history failed %s: %s", resp.status_code, resp.text[:300])
                raise HTTPException(status_code=502, detail="Could not list Gmail history.")
            else:
                payload = resp.json()
                newest_history_id = payload.get("historyId")
                for item in payload.get("history", []) or []:
                    for added in item.get("messagesAdded", []) or []:
                        msg = added.get("message") or {}
                        mid = msg.get("id")
                        if mid and mid not in message_ids:
                            message_ids.append(mid)

        if not start_history_id:
            resp = await client.get(
                "https://gmail.googleapis.com/gmail/v1/users/me/messages",
                headers={"Authorization": f"Bearer {access_token}"},
                params={"labelIds": "INBOX", "maxResults": str(max_results)},
            )
            if resp.status_code >= 400:
                logger.warning("Gmail list failed %s: %s", resp.status_code, resp.text[:300])
                raise HTTPException(status_code=502, detail="Could not list Gmail messages.")
            for item in resp.json().get("messages", []) or []:
                mid = item.get("id")
                if mid:
                    message_ids.append(mid)

        ingested = 0
        for message_id in message_ids[:max_results]:
            message = await _fetch_gmail_message(client, access_token, message_id)
            if message is None:
                continue
            conversation = _message_to_conversation(message, account_email)
            raw = json.dumps(message, ensure_ascii=False).encode("utf-8")
            document = DocumentInput(
                data=raw,
                source="gmail",
                source_label=conversation.title or "Gmail message",
                original_filename=f"{message_id}.json",
                mime_type="application/json",
                visible_to=[account_email.lower()],
                title=conversation.title,
                author=_gmail_headers(message).get("from"),
                owners=[account_email.lower()],
                source_created_at=conversation.messages[0].timestamp,
                source_updated_at=conversation.messages[0].timestamp,
                source_application="Gmail",
                source_location="Gmail INBOX",
                version=str(message.get("historyId") or ""),
                contributors=[
                    value
                    for value in (
                        _gmail_headers(message).get("from"),
                        _gmail_headers(message).get("to"),
                    )
                    if value
                ],
                permissions=[account_email.lower()],
            )
            await run_ingestion(conversation, org_id, document=document)
            ingested += 1
            newest_history_id = str(message.get("historyId") or newest_history_id or "")

    await _upsert_cursor(
        org_id=org_id,
        user_id=user_id,
        provider=SYNC_PROVIDER_GMAIL,
        account_email=account_email.lower(),
        cursor_value=newest_history_id or start_history_id,
        mark_synced=True,
    )
    return ingested


async def _drive_export_file(
    client: httpx.AsyncClient, access_token: str, file: dict[str, Any]
) -> tuple[bytes, str, str] | None:
    mime_type = str(file.get("mimeType", ""))
    file_id = str(file.get("id", ""))
    name = str(file.get("name", file_id))
    if mime_type in EXPORT_FORMATS:
        export_mime, suffix = EXPORT_FORMATS[mime_type]
        resp = await request_with_backoff(
            client,
            "GET",
            f"https://www.googleapis.com/drive/v3/files/{file_id}/export",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"mimeType": export_mime},
        )
        if resp.status_code >= 400:
            logger.warning("Drive export failed %s: %s", resp.status_code, resp.text[:200])
            return None
        return resp.content, export_mime, f"{name}{suffix}"

    if mime_type in {"text/plain", "text/csv", "application/pdf"}:
        resp = await request_with_backoff(
            client,
            "GET",
            f"https://www.googleapis.com/drive/v3/files/{file_id}",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"alt": "media", "supportsAllDrives": "true"},
        )
        if resp.status_code >= 400:
            logger.warning("Drive download failed %s: %s", resp.status_code, resp.text[:200])
            return None
        return resp.content, mime_type, name
    return None


def _drive_conversation(file: dict[str, Any], text: str) -> Conversation:
    file_id = str(file.get("id") or uuid4())
    name = str(file.get("name") or "Drive file")
    modified = file.get("modifiedTime")
    try:
        timestamp = datetime.fromisoformat(str(modified).replace("Z", "+00:00")) if modified else datetime.now(timezone.utc)
    except ValueError:
        timestamp = datetime.now(timezone.utc)
    return Conversation(
        source="google_drive",
        conversation_id=f"drive:{file_id}",
        title=name,
        participants=[Participant(id="google_drive", name="Google Drive")],
        messages=[
            IncomingMessage(
                id=f"drive:{file_id}:content",
                sender="google_drive",
                timestamp=timestamp,
                text=text,
            )
        ],
    )


async def sync_drive(org_id: str, user_id: str, *, max_results: int = 25) -> int:
    """Fetch changed Drive Docs/Sheets/Slides and ingest exported text."""

    from connection_setup import get_policy, visibility_for_policy

    access_token, account_email = await _workspace_access_token(org_id, user_id)
    if access_token.startswith("dev:"):
        await _upsert_cursor(
            org_id=org_id,
            user_id=user_id,
            provider=SYNC_PROVIDER_DRIVE,
            account_email=account_email.lower(),
            cursor_value="dev-start-token",
            mark_synced=True,
        )
        return 0

    policy = await get_policy(org_id, user_id, PROVIDER_GOOGLE_WORKSPACE)
    if policy and policy.status == "paused":
        return 0
    included = set(json.loads(policy.included_resources)) if policy else set()
    excluded = set(json.loads(policy.excluded_resources)) if policy else set()
    visible_to = visibility_for_policy(
        policy, org_id=org_id, source_account=account_email
    )

    parent_cache: dict[str, list[str]] = {}

    async def folder_parents(client: httpx.AsyncClient, folder_id: str) -> list[str]:
        if folder_id in parent_cache:
            return parent_cache[folder_id]
        response = await request_with_backoff(
            client,
            "GET",
            f"https://www.googleapis.com/drive/v3/files/{folder_id}",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"fields": "id,parents", "supportsAllDrives": "true"},
        )
        if response.status_code >= 400:
            parent_cache[folder_id] = []
        else:
            parent_cache[folder_id] = [
                str(item) for item in response.json().get("parents", []) or []
            ]
        return parent_cache[folder_id]

    async def is_selected(client: httpx.AsyncClient, file: dict[str, Any]) -> bool:
        if not included:
            return False
        identifiers = {f"drive:{file.get('driveId')}"} if file.get("driveId") else set()
        pending = [str(parent) for parent in file.get("parents", []) or []]
        visited: set[str] = set()
        while pending:
            parent = pending.pop()
            if parent in visited:
                continue
            visited.add(parent)
            identifiers.add(f"folder:{parent}")
            pending.extend(await folder_parents(client, parent))
        return bool(identifiers & included) and not bool(identifiers & excluded)

    async def ingest_file(client: httpx.AsyncClient, file: dict[str, Any]) -> bool:
        if not await is_selected(client, file):
            return False
        exported = await _drive_export_file(client, access_token, file)
        if exported is None:
            return False
        data, mime_type, filename = exported
        text = extract_file_text(filename, data).strip()
        if not text:
            return False
        file_visibility = visible_to
        if policy and policy.access_mode == "respect_source_permissions":
            permissions_response = await request_with_backoff(
                client,
                "GET",
                f"https://www.googleapis.com/drive/v3/files/{file.get('id')}/permissions",
                headers={"Authorization": f"Bearer {access_token}"},
                params={
                    "fields": "permissions(type,emailAddress,domain,deleted)",
                    "supportsAllDrives": "true",
                },
            )
            if permissions_response.status_code < 400:
                permission_tokens: list[str] = []
                for permission in permissions_response.json().get("permissions", []) or []:
                    if permission.get("deleted"):
                        continue
                    email = str(permission.get("emailAddress") or "").lower()
                    domain = str(permission.get("domain") or "").lower()
                    if email:
                        permission_tokens.append(email)
                    elif permission.get("type") == "domain" and domain:
                        permission_tokens.append(f"domain:{domain}")
                # Fail closed when Graph/Drive does not return a usable ACL.
                file_visibility = permission_tokens or [account_email.lower()]
        conversation = _drive_conversation(file, text)
        owners = [
            str(owner.get("emailAddress") or owner.get("displayName") or "")
            for owner in file.get("owners", []) or []
            if owner.get("emailAddress") or owner.get("displayName")
        ]
        modifier = file.get("lastModifyingUser") or {}
        modifier_name = str(
            modifier.get("emailAddress") or modifier.get("displayName") or ""
        )
        contributors = sorted({*owners, *([modifier_name] if modifier_name else [])})
        created_at = _parse_google_datetime(file.get("createdTime"))
        updated_at = _parse_google_datetime(file.get("modifiedTime"))
        await ingest_external_source(
            org_id=org_id,
            provider=SYNC_PROVIDER_DRIVE,
            external_id=str(file.get("id")),
            version=str(file.get("version") or file.get("modifiedTime") or ""),
            conversation=conversation,
            document=DocumentInput(
                data=data,
                source="google_drive",
                source_label=conversation.title or "Google Drive file",
                original_filename=filename,
                mime_type=mime_type,
                visible_to=file_visibility,
                title=str(file.get("name") or filename),
                author=owners[0] if owners else None,
                owners=owners,
                source_created_at=created_at,
                source_updated_at=updated_at,
                source_application=_google_application(str(file.get("mimeType") or mime_type)),
                source_location=f"Google Drive {file.get('driveId') or 'My Drive'}",
                folder_path="/".join(str(parent) for parent in file.get("parents", []) or []),
                version=str(file.get("version") or file.get("modifiedTime") or ""),
                contributors=contributors,
                permissions=file_visibility,
                source_url=str(file.get("webViewLink") or "") or None,
            ),
        )
        return True

    cursor = await _get_cursor(org_id, user_id, SYNC_PROVIDER_DRIVE)
    page_token = cursor.cursor_value if cursor else None

    async with httpx.AsyncClient(timeout=30.0) as client:
        ingested = 0
        if not page_token:
            if policy and policy.include_history:
                params = {
                    "pageSize": str(max_results),
                    "supportsAllDrives": "true",
                    "includeItemsFromAllDrives": "true",
                    "q": "trashed=false",
                    "fields": "files(id,name,mimeType,createdTime,modifiedTime,version,"
                    "webViewLink,owners(displayName,emailAddress),"
                    "lastModifyingUser(displayName,emailAddress),parents,driveId)",
                }
                if policy.history_start_date:
                    params["q"] += f" and modifiedTime >= '{policy.history_start_date}T00:00:00Z'"
                history_response = await request_with_backoff(
                    client,
                    "GET",
                    "https://www.googleapis.com/drive/v3/files",
                    headers={"Authorization": f"Bearer {access_token}"},
                    params=params,
                )
                if history_response.status_code >= 400:
                    raise HTTPException(status_code=502, detail="Could not list Drive history.")
                for file in history_response.json().get("files", []):
                    if await ingest_file(client, file):
                        ingested += 1
            token_resp = await request_with_backoff(
                client,
                "GET",
                "https://www.googleapis.com/drive/v3/changes/startPageToken",
                headers={"Authorization": f"Bearer {access_token}"},
                params={"supportsAllDrives": "true"},
            )
            if token_resp.status_code >= 400:
                raise HTTPException(status_code=502, detail="Could not get Drive start page token.")
            page_token = token_resp.json().get("startPageToken")

        resp = await request_with_backoff(
            client,
            "GET",
            "https://www.googleapis.com/drive/v3/changes",
            headers={"Authorization": f"Bearer {access_token}"},
            params={
                "pageToken": page_token,
                "pageSize": str(max_results),
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
                "fields": (
                    "newStartPageToken,nextPageToken,"
                    "changes(fileId,removed,file(id,name,mimeType,createdTime,modifiedTime,version,"
                    "trashed,webViewLink,owners(displayName,emailAddress),"
                    "lastModifyingUser(displayName,emailAddress),parents,driveId))"
                ),
            },
        )
        if resp.status_code >= 400:
            logger.warning("Drive changes failed %s: %s", resp.status_code, resp.text[:300])
            raise HTTPException(status_code=502, detail="Could not list Drive changes.")

        payload = resp.json()
        for change in payload.get("changes", []) or []:
            file = change.get("file") or {}
            if change.get("removed") or file.get("trashed"):
                file_id = str(change.get("fileId") or file.get("id") or "")
                if file_id:
                    await mark_external_source_deleted(
                        org_id, SYNC_PROVIDER_DRIVE, file_id
                    )
                continue
            if await ingest_file(client, file):
                ingested += 1

    await _upsert_cursor(
        org_id=org_id,
        user_id=user_id,
        provider=SYNC_PROVIDER_DRIVE,
        account_email=account_email.lower(),
        cursor_value=payload.get("newStartPageToken") or payload.get("nextPageToken") or page_token,
        mark_synced=True,
    )
    return ingested


async def run_workspace_sync_background(
    *,
    job_id: str,
    org_id: str,
    user_id: str,
    source: Literal["gmail", "drive"],
    job_store: dict[str, JobStatus],
    max_results: int = 25,
    override_history_id: str | None = None,
) -> None:
    """Run Gmail/Drive sync as a FastAPI background task."""

    job = job_store[job_id]
    job.status = "processing"
    job.progress = f"Syncing Google {source}"
    try:
        if source == SYNC_PROVIDER_GMAIL:
            count = await sync_gmail(
                org_id,
                user_id,
                max_results=max_results,
                override_history_id=override_history_id,
            )
        else:
            count = await sync_drive(org_id, user_id, max_results=max_results)
        job.status = "complete"
        job.progress = f"Google {source} sync complete ({count} item(s) ingested)"
    except Exception as exc:  # noqa: BLE001
        job.status = "failed"
        job.progress = None
        job.error = str(exc)
        logger.exception("Google %s sync job %s failed", source, job_id)
