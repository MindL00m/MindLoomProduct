"""Microsoft Teams connector built on Microsoft Graph.

The MVP supports delegated OAuth, manual sync of channel messages from teams the
connected user has joined, and per-channel Graph subscriptions. Tenant-wide
Teams capture is an admin/application-permission path and should be added after
the product has durable workers and permission filtering.
"""

from __future__ import annotations

import html
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import httpx
from fastapi import HTTPException
from sqlalchemy import select

from auth import UserRow
from config import get_settings
from database import get_session_factory
from google_workspace import SyncCursorRow, _upsert_cursor
from integrations import (
    _get_connection,
    _pop_oauth_state,
    _save_connection,
    _store_oauth_state,
)
from models import (
    Conversation,
    IncomingMessage,
    JobStatus,
    OAuthAuthorizeResponse,
    Participant,
    TeamsWatchResponse,
)
from pipeline import DocumentInput, run_ingestion
from source_registry import ingest_external_source
from provider_http import graph_get_all, request_with_backoff
from subscriptions import find_subscription, upsert_subscription

logger = logging.getLogger(__name__)

PROVIDER_MICROSOFT_TEAMS = "microsoft_teams"
SYNC_PROVIDER_TEAMS = "teams"

TEAMS_SCOPES = [
    "offline_access",
    "User.Read",
    "Team.ReadBasic.All",
    "Channel.ReadBasic.All",
    "ChannelMessage.Read.All",
    "TeamMember.Read.All",
    "ChannelMember.Read.All",
    "Chat.Read",
    "Mail.Read",
    "Mail.Send",
    "Calendars.Read",
    "Chat.Create",
    "ChatMessage.Send",
    "Sites.Read.All",
    "Files.Read.All",
]


def _parse_graph_datetime(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _authority() -> str:
    settings = get_settings()
    tenant = settings.microsoft_tenant_id.strip() or "common"
    return f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0"


async def start_microsoft_teams_oauth(org_id: str, user_id: str) -> OAuthAuthorizeResponse:
    """Return a Microsoft OAuth URL requesting Teams read scopes."""

    settings = get_settings()
    if not settings.microsoft_oauth_enabled:
        raise HTTPException(
            status_code=503,
            detail=(
                "Microsoft OAuth is not configured. Set MICROSOFT_CLIENT_ID and "
                "MICROSOFT_CLIENT_SECRET before connecting Teams."
            ),
        )

    state = _store_oauth_state(org_id, user_id)
    scope = " ".join(TEAMS_SCOPES)
    url = (
        f"{_authority()}/authorize"
        f"?client_id={settings.microsoft_client_id}"
        f"&response_type=code"
        f"&redirect_uri={settings.microsoft_oauth_redirect_uri}"
        f"&response_mode=query"
        f"&scope={scope}"
        f"&state={state}"
    )
    return OAuthAuthorizeResponse(authorization_url=url)


async def handle_microsoft_teams_callback(code: str, state: str) -> str:
    """Exchange Microsoft OAuth code and store Teams connection."""

    settings = get_settings()
    org_id, user_id = _pop_oauth_state(state)
    data = {
        "client_id": settings.microsoft_client_id,
        "client_secret": settings.microsoft_client_secret,
        "code": code,
        "redirect_uri": settings.microsoft_oauth_redirect_uri,
        "grant_type": "authorization_code",
        "scope": " ".join(TEAMS_SCOPES),
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(f"{_authority()}/token", data=data)
    if resp.status_code >= 400:
        logger.warning("Microsoft token exchange failed %s: %s", resp.status_code, resp.text[:300])
        raise HTTPException(status_code=502, detail="Could not connect Microsoft Teams.")

    payload = resp.json()
    access_token = payload.get("access_token") or ""
    refresh_token = payload.get("refresh_token")
    expires_in = int(payload.get("expires_in") or 3600)
    account_email = await _fetch_me_email(access_token)
    await _save_connection(
        org_id=org_id,
        user_id=user_id,
        provider=PROVIDER_MICROSOFT_TEAMS,
        account_email=account_email,
        access_token=access_token,
        refresh_token=refresh_token,
        token_expiry=datetime.now(timezone.utc) + timedelta(seconds=expires_in),
        scopes=" ".join(TEAMS_SCOPES),
    )
    return f"{settings.frontend_url.rstrip('/')}/dashboard?tab=apps&setup=microsoft_teams"


async def connect_microsoft_teams_dev(org_id: str, user_id: str) -> None:
    """Create a non-network dev Teams connection."""

    settings = get_settings()
    if settings.microsoft_oauth_enabled:
        raise HTTPException(
            status_code=400,
            detail="Microsoft OAuth is configured — use the real Teams connect flow.",
        )
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(select(UserRow).where(UserRow.user_id == user_id))
        user = result.scalar_one()
    await _save_connection(
        org_id=org_id,
        user_id=user_id,
        provider=PROVIDER_MICROSOFT_TEAMS,
        account_email=user.email,
        access_token=f"dev:{user_id}",
        refresh_token=None,
        token_expiry=None,
        scopes="dev",
    )


async def _fetch_me_email(access_token: str) -> str | None:
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            "https://graph.microsoft.com/v1.0/me",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"$select": "mail,userPrincipalName,displayName"},
        )
    if resp.status_code != 200:
        return None
    payload = resp.json()
    return payload.get("mail") or payload.get("userPrincipalName")


async def _teams_token(org_id: str, user_id: str) -> tuple[str, str]:
    row = await _get_connection(org_id, user_id, PROVIDER_MICROSOFT_TEAMS)
    if row is None:
        raise HTTPException(status_code=404, detail="Microsoft Teams is not connected.")
    if row.access_token.startswith("dev:"):
        return row.access_token, row.account_email or "dev@example.com"
    if row.token_expiry and row.token_expiry > datetime.now(timezone.utc) + timedelta(minutes=2):
        return row.access_token, row.account_email or ""
    if not row.refresh_token:
        raise HTTPException(status_code=401, detail="Microsoft Teams connection expired. Reconnect.")

    settings = get_settings()
    data = {
        "client_id": settings.microsoft_client_id,
        "client_secret": settings.microsoft_client_secret,
        "grant_type": "refresh_token",
        "refresh_token": row.refresh_token,
        "scope": " ".join(TEAMS_SCOPES),
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(f"{_authority()}/token", data=data)
    if resp.status_code >= 400:
        raise HTTPException(status_code=401, detail="Microsoft Teams connection expired. Reconnect.")
    payload = resp.json()
    access_token = payload.get("access_token") or row.access_token
    refresh_token = payload.get("refresh_token") or row.refresh_token
    expires_in = int(payload.get("expires_in") or 3600)
    saved = await _save_connection(
        org_id=org_id,
        user_id=user_id,
        provider=PROVIDER_MICROSOFT_TEAMS,
        account_email=row.account_email,
        access_token=access_token,
        refresh_token=refresh_token,
        token_expiry=datetime.now(timezone.utc) + timedelta(seconds=expires_in),
        scopes=row.scopes,
    )
    return saved.access_token, saved.account_email or ""


def _clean_body(message: dict[str, Any]) -> str:
    body = message.get("body") or {}
    content = str(body.get("content") or "")
    return html.unescape(content.replace("<br>", "\n").replace("<br/>", "\n")).strip()


def _message_time(message: dict[str, Any]) -> datetime:
    raw = message.get("createdDateTime") or message.get("lastModifiedDateTime")
    if raw:
        try:
            return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _sender(message: dict[str, Any]) -> tuple[str, str]:
    user = ((message.get("from") or {}).get("user") or {}) if isinstance(message.get("from"), dict) else {}
    user_id = str(user.get("id") or "unknown")
    name = str(user.get("displayName") or "Unknown")
    return user_id, name


def _teams_conversation(resource: str, message: dict[str, Any]) -> Conversation:
    sender_id, sender_name = _sender(message)
    message_id = str(message.get("id") or uuid4())
    text = _clean_body(message) or str(message.get("summary") or "")
    return Conversation(
        source="microsoft_teams",
        conversation_id=f"teams:{resource}",
        title=f"Teams message {message_id}",
        participants=[Participant(id=sender_id, name=sender_name)],
        messages=[
            IncomingMessage(
                id=message_id,
                sender=sender_id,
                timestamp=_message_time(message),
                text=text,
            )
        ],
    )


async def _graph_get(client: httpx.AsyncClient, access_token: str, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
    resp = await request_with_backoff(
        client,
        "GET",
        f"https://graph.microsoft.com/v1.0{path}",
        headers={"Authorization": f"Bearer {access_token}"},
        params=params,
    )
    if resp.status_code >= 400:
        logger.warning("Graph GET %s failed %s: %s", path, resp.status_code, resp.text[:300])
        raise HTTPException(status_code=502, detail="Microsoft Graph request failed.")
    return resp.json()


async def sync_teams(org_id: str, user_id: str, *, max_results: int = 25) -> int:
    """Sync recent channel messages from joined Teams into the graph."""

    from connection_setup import get_policy, visibility_for_policy

    access_token, account_email = await _teams_token(org_id, user_id)
    if access_token.startswith("dev:"):
        await _upsert_cursor(
            org_id=org_id,
            user_id=user_id,
            provider=SYNC_PROVIDER_TEAMS,
            account_email=account_email.lower(),
            cursor_value="dev-teams",
            mark_synced=True,
        )
        return 0

    policy = await get_policy(org_id, user_id, PROVIDER_MICROSOFT_TEAMS)
    if policy and policy.status == "paused":
        return 0
    included = set(json.loads(policy.included_resources)) if policy else set()
    excluded = set(json.loads(policy.excluded_resources)) if policy else set()
    history_start = (
        datetime.fromisoformat(policy.history_start_date).replace(tzinfo=timezone.utc)
        if policy and policy.history_start_date
        else None
    )
    visible_to = visibility_for_policy(
        policy, org_id=org_id, source_account=account_email
    )
    ingested = 0
    async with httpx.AsyncClient(timeout=30.0) as client:
        teams = await graph_get_all(
            client, access_token, "/me/joinedTeams", {"$select": "id,displayName"}
        )
        for team in teams:
            team_id = team.get("id")
            if not team_id:
                continue
            team_selected = f"team:{team_id}" in included
            if f"team:{team_id}" in excluded:
                continue
            channels = await graph_get_all(
                client,
                access_token,
                f"/teams/{team_id}/channels",
                {"$select": "id,displayName,membershipType"},
            )
            for channel in channels:
                channel_id = channel.get("id")
                if not channel_id:
                    continue
                channel_key = f"channel:{team_id}:{channel_id}"
                if not team_selected and channel_key not in included:
                    continue
                if channel_key in excluded:
                    continue
                message_visibility = visible_to
                if policy and policy.access_mode == "respect_source_permissions":
                    membership_path = (
                        f"/teams/{team_id}/channels/{channel_id}/members"
                        if channel.get("membershipType") in {"private", "shared"}
                        else f"/teams/{team_id}/members"
                    )
                    members = await graph_get_all(
                        client,
                        access_token,
                        membership_path,
                        {"$select": "email,userId"},
                    )
                    message_visibility = sorted(
                        {
                            str(member.get("email") or "").lower()
                            for member in members
                            if member.get("email")
                        }
                    ) or [account_email.lower()]
                messages = await graph_get_all(
                    client,
                    access_token,
                    f"/teams/{team_id}/channels/{channel_id}/messages",
                    {"$top": str(max(1, min(max_results - ingested, 50)))},
                    item_limit=max(1, max_results - ingested),
                )
                resource = f"teams/{team_id}/channels/{channel_id}"
                for message in messages:
                    if ingested >= max_results:
                        break
                    if history_start and _message_time(message) < history_start:
                        continue
                    conversation = _teams_conversation(resource, message)
                    raw = json.dumps(message, ensure_ascii=False).encode("utf-8")
                    sender_id, sender_name = _sender(message)
                    await ingest_external_source(
                        org_id=org_id,
                        provider=SYNC_PROVIDER_TEAMS,
                        external_id=f"{team_id}:{channel_id}:{message.get('id')}",
                        version=str(
                            message.get("lastModifiedDateTime")
                            or message.get("createdDateTime")
                            or ""
                        ),
                        conversation=conversation,
                        document=DocumentInput(
                            data=raw,
                            source="microsoft_teams",
                            source_label=conversation.title or "Teams message",
                            original_filename=f"{message.get('id', uuid4())}.json",
                            mime_type="application/json",
                            visible_to=message_visibility,
                            title=conversation.title,
                            author=sender_name,
                            owners=[sender_name],
                            source_created_at=_message_time(message),
                            source_updated_at=_parse_graph_datetime(
                                message.get("lastModifiedDateTime")
                            ),
                            source_application="Microsoft Teams",
                            source_location=f"Team {team_id} / Channel {channel_id}",
                            folder_path=f"teams/{team_id}/channels/{channel_id}",
                            version=str(
                                message.get("lastModifiedDateTime")
                                or message.get("createdDateTime")
                                or ""
                            ),
                            contributors=[sender_name],
                            permissions=message_visibility,
                            source_url=str(message.get("webUrl") or "") or None,
                        ),
                    )
                    ingested += 1
                if ingested >= max_results:
                    break
            if ingested >= max_results:
                break

    await _upsert_cursor(
        org_id=org_id,
        user_id=user_id,
        provider=SYNC_PROVIDER_TEAMS,
        account_email=account_email.lower(),
        cursor_value=datetime.now(timezone.utc).isoformat(),
        mark_synced=True,
    )
    return ingested


async def setup_teams_channel_watch(
    org_id: str, user_id: str, *, team_id: str, channel_id: str
) -> TeamsWatchResponse:
    """Create a Microsoft Graph subscription for one Teams channel."""

    settings = get_settings()
    access_token, account_email = await _teams_token(org_id, user_id)
    resource = f"/teams/{team_id}/channels/{channel_id}/messages"
    if access_token.startswith("dev:"):
        await _upsert_cursor(
            org_id=org_id,
            user_id=user_id,
            provider=SYNC_PROVIDER_TEAMS,
            account_email=account_email.lower(),
            cursor_value=datetime.now(timezone.utc).isoformat(),
        )
        subscription_id = f"dev:{team_id}:{channel_id}"
        subscription = await upsert_subscription(
            org_id=org_id,
            user_id=user_id,
            provider=SYNC_PROVIDER_TEAMS,
            external_id=subscription_id,
            resource=resource,
        )
        return TeamsWatchResponse(
            provider="teams", resource=resource, subscription_id=subscription.external_id
        )
    if not settings.microsoft_graph_webhook_url:
        raise HTTPException(status_code=400, detail="MICROSOFT_GRAPH_WEBHOOK_URL is not configured.")

    expiration = datetime.now(timezone.utc) + timedelta(minutes=55)
    body = {
        "changeType": "created,updated,deleted",
        "notificationUrl": settings.microsoft_graph_webhook_url,
        "resource": resource,
        "expirationDateTime": expiration.isoformat().replace("+00:00", "Z"),
        "clientState": settings.microsoft_graph_client_state,
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            "https://graph.microsoft.com/v1.0/subscriptions",
            headers={"Authorization": f"Bearer {access_token}"},
            json=body,
        )
    if resp.status_code >= 400:
        logger.warning("Teams subscription failed %s: %s", resp.status_code, resp.text[:300])
        raise HTTPException(status_code=502, detail="Could not start Teams channel watch.")
    payload = resp.json()
    subscription_id = str(payload.get("id") or "")
    await _upsert_cursor(
        org_id=org_id,
        user_id=user_id,
        provider=SYNC_PROVIDER_TEAMS,
        account_email=account_email.lower(),
        cursor_value=datetime.now(timezone.utc).isoformat(),
    )
    subscription = await upsert_subscription(
        org_id=org_id,
        user_id=user_id,
        provider=SYNC_PROVIDER_TEAMS,
        external_id=subscription_id,
        resource=resource,
        expiration=_parse_graph_datetime(payload.get("expirationDateTime")) or expiration,
    )
    return TeamsWatchResponse(
        provider="teams",
        resource=resource,
        subscription_id=subscription.external_id,
        expiration=subscription.expiration,
        status=subscription.status,
    )


async def find_teams_cursor_by_subscription(subscription_id: str):
    """Return the persistent subscription routing record for a webhook."""

    return await find_subscription(SYNC_PROVIDER_TEAMS, subscription_id)


def graph_validation_response(validation_token: str | None) -> str | None:
    """Return the Microsoft Graph webhook validation token as plain text."""

    return validation_token


async def run_teams_sync_background(
    *,
    job_id: str,
    org_id: str,
    user_id: str,
    job_store: dict[str, JobStatus],
    max_results: int = 25,
) -> None:
    job = job_store[job_id]
    job.status = "processing"
    job.progress = "Syncing Microsoft Teams"
    try:
        count = await sync_teams(org_id, user_id, max_results=max_results)
        job.status = "complete"
        job.progress = f"Microsoft Teams sync complete ({count} item(s) ingested)"
    except Exception as exc:  # noqa: BLE001
        job.status = "failed"
        job.progress = None
        job.error = str(exc)
        logger.exception("Microsoft Teams sync job %s failed", job_id)
