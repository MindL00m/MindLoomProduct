"""Zoom OAuth and continuous cloud-recording knowledge ingestion."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import httpx
from fastapi import HTTPException

from config import get_settings
from integrations import (
    _get_connection,
    _pop_oauth_state,
    _save_connection,
    _store_oauth_state,
)
from models import Conversation, IncomingMessage, Participant
from pipeline import DocumentInput
from provider_http import request_with_backoff
from source_registry import ingest_external_source

PROVIDER_ZOOM = "zoom"
ZOOM_SCOPES = (
    "user:read:user cloud_recording:read:list_user_recordings "
    "cloud_recording:read:meeting_transcript"
)


async def start_zoom_oauth(org_id: str, user_id: str) -> str:
    settings = get_settings()
    if not settings.zoom_oauth_enabled:
        raise HTTPException(status_code=503, detail="Zoom OAuth is not configured.")
    state = _store_oauth_state(org_id, user_id)
    return str(httpx.URL("https://zoom.us/oauth/authorize").copy_add_params({
        "response_type": "code",
        "client_id": settings.zoom_client_id,
        "redirect_uri": settings.zoom_oauth_redirect_uri,
        "state": state,
    }))


async def handle_zoom_callback(code: str, state: str) -> str:
    settings = get_settings()
    org_id, user_id = _pop_oauth_state(state)
    basic = base64.b64encode(
        f"{settings.zoom_client_id}:{settings.zoom_client_secret}".encode()
    ).decode()
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            "https://zoom.us/oauth/token",
            headers={"Authorization": f"Basic {basic}"},
            params={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.zoom_oauth_redirect_uri,
            },
        )
        response.raise_for_status()
        token = response.json()
        profile = await client.get(
            "https://api.zoom.us/v2/users/me",
            headers={"Authorization": f"Bearer {token['access_token']}"},
        )
        profile.raise_for_status()
    await _save_connection(
        org_id=org_id,
        user_id=user_id,
        provider=PROVIDER_ZOOM,
        account_email=profile.json().get("email"),
        access_token=token["access_token"],
        refresh_token=token.get("refresh_token"),
        token_expiry=datetime.now(timezone.utc)
        + timedelta(seconds=int(token.get("expires_in") or 3600)),
        scopes=str(token.get("scope") or ZOOM_SCOPES),
    )
    return f"{settings.frontend_url.rstrip('/')}/dashboard?tab=apps&setup=zoom"


async def connect_zoom_dev(org_id: str, user_id: str, email: str) -> None:
    await _save_connection(
        org_id=org_id,
        user_id=user_id,
        provider=PROVIDER_ZOOM,
        account_email=email,
        access_token=f"dev:{user_id}",
        refresh_token=None,
        token_expiry=None,
        scopes="dev",
    )


async def _zoom_token(org_id: str, user_id: str) -> tuple[str, str]:
    row = await _get_connection(org_id, user_id, PROVIDER_ZOOM)
    if row is None:
        raise HTTPException(status_code=404, detail="Zoom is not connected.")
    if row.access_token.startswith("dev:"):
        return row.access_token, row.account_email or "dev@example.com"
    if row.token_expiry and row.token_expiry > datetime.now(timezone.utc) + timedelta(minutes=2):
        return row.access_token, row.account_email or ""
    if not row.refresh_token:
        raise HTTPException(status_code=401, detail="Zoom connection expired. Reconnect it.")
    settings = get_settings()
    basic = base64.b64encode(
        f"{settings.zoom_client_id}:{settings.zoom_client_secret}".encode()
    ).decode()
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            "https://zoom.us/oauth/token",
            headers={"Authorization": f"Basic {basic}"},
            params={"grant_type": "refresh_token", "refresh_token": row.refresh_token},
        )
        response.raise_for_status()
    token = response.json()
    refreshed = await _save_connection(
        org_id=org_id,
        user_id=user_id,
        provider=PROVIDER_ZOOM,
        account_email=row.account_email,
        access_token=token["access_token"],
        refresh_token=token.get("refresh_token") or row.refresh_token,
        token_expiry=datetime.now(timezone.utc)
        + timedelta(seconds=int(token.get("expires_in") or 3600)),
        scopes=str(token.get("scope") or row.scopes or ZOOM_SCOPES),
    )
    return refreshed.access_token, refreshed.account_email or ""


def _visible_to(org_id: str, user_id: str, policy: object) -> list[str]:
    if getattr(policy, "access_mode", "") == "selected":
        users = json.loads(getattr(policy, "allowed_user_ids"))
        departments = json.loads(getattr(policy, "allowed_departments"))
        return [
            *[f"user:{item}" for item in users],
            *[f"department:{item}" for item in departments],
        ]
    if getattr(policy, "access_mode", "") == "organization":
        return [f"org:{org_id}"]
    # Zoom recording APIs do not expose a complete per-viewer ACL. Respecting
    # source permissions therefore defaults conservatively to the connected
    # host instead of widening access to the organization.
    return [f"user:{user_id}"]


async def sync_zoom(
    org_id: str,
    user_id: str,
    *,
    max_results: int = 100,
    meeting_uuid: str | None = None,
) -> int:
    token, email = await _zoom_token(org_id, user_id)
    if token.startswith("dev:"):
        return 0
    from connection_setup import get_policy

    policy = await get_policy(org_id, user_id, PROVIDER_ZOOM)
    if policy is None or policy.status == "paused":
        return 0
    included = set(json.loads(policy.included_resources))
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        if meeting_uuid:
            response = await request_with_backoff(
                client,
                "GET",
                f"https://api.zoom.us/v2/meetings/{quote(meeting_uuid, safe='')}/recordings",
                headers=headers,
            )
            response.raise_for_status()
            meetings = [response.json()]
        else:
            response = await request_with_backoff(
                client,
                "GET",
                "https://api.zoom.us/v2/users/me/recordings",
                headers=headers,
                params={
                    "page_size": min(max_results, 300),
                    "from": policy.history_start_date
                    or (datetime.now(timezone.utc) - timedelta(days=30)).date().isoformat(),
                    "to": datetime.now(timezone.utc).date().isoformat(),
                },
            )
            response.raise_for_status()
            meetings = response.json().get("meetings", [])[:max_results]
        imported = 0
        for meeting in meetings:
            files = meeting.get("recording_files") or []
            parts: list[str] = []
            for item in files:
                file_type = item.get("file_type")
                if file_type not in {"TRANSCRIPT", "CHAT", "SUMMARY"}:
                    continue
                if file_type == "CHAT" and "recording:chat" not in included:
                    continue
                if file_type != "CHAT" and "recording:transcripts" not in included:
                    continue
                if not item.get("download_url"):
                    continue
                downloaded = await request_with_backoff(
                    client, "GET", item["download_url"], headers=headers
                )
                downloaded.raise_for_status()
                parts.append(f"--- {file_type} ---\n{downloaded.text}")
            if not parts:
                continue
            content = "\n\n".join(parts)
            uuid = str(meeting.get("uuid") or meeting.get("id"))
            start = datetime.fromisoformat(
                str(meeting.get("start_time")).replace("Z", "+00:00")
            )
            host = str(meeting.get("host_email") or email)
            title = str(meeting.get("topic") or "Zoom meeting")
            visible_to = _visible_to(org_id, user_id, policy)
            result = await ingest_external_source(
                org_id=org_id,
                provider=PROVIDER_ZOOM,
                external_id=uuid,
                version=max(
                    (str(item.get("recording_end") or "") for item in files),
                    default=start.isoformat(),
                ),
                conversation=Conversation(
                    source=PROVIDER_ZOOM,
                    conversation_id=f"zoom:{uuid}",
                    title=title,
                    participants=[Participant(id="zoom-host", name=host)],
                    messages=[IncomingMessage(
                        id=f"zoom:{uuid}",
                        sender="zoom-host",
                        timestamp=start,
                        text=content,
                    )],
                ),
                document=DocumentInput(
                    data=content.encode(),
                    source=PROVIDER_ZOOM,
                    source_label=title,
                    original_filename=f"{uuid}.txt",
                    mime_type="text/plain",
                    visible_to=visible_to,
                    title=title,
                    author=host,
                    owners=[host],
                    source_created_at=start,
                    source_updated_at=start,
                    source_application="Zoom",
                    source_location="Zoom cloud recordings",
                    version=uuid,
                    contributors=[host],
                    permissions=visible_to,
                    source_url=str(meeting.get("share_url") or ""),
                ),
            )
            imported += int(result is not None)
    return imported


def validate_zoom_webhook(body: bytes, timestamp: str, signature: str) -> bool:
    secret = get_settings().zoom_webhook_secret_token
    if not secret:
        return False
    expected = "v0=" + hmac.new(
        secret.encode(),
        f"v0:{timestamp}:{body.decode()}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)
