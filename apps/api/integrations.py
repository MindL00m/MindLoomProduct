"""Shared OAuth connection persistence and integration inventory."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated
from uuid import uuid4

import httpx
from fastapi import Depends, Header, HTTPException
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from sqlalchemy import DateTime, String, Text, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Mapped, mapped_column

from auth import Base, UserRow, require_org_id
from config import get_settings
from database import get_session_factory
from models import (
    IntegrationInfo,
    IntegrationsListResponse,
    OAuthAuthorizeResponse,
)

logger = logging.getLogger(__name__)

# Google may return a subset of requested scopes; avoid hard failures on exchange.
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

PROVIDER_GOOGLE_WORKSPACE = "google_workspace"
PROVIDER_MICROSOFT_TEAMS = "microsoft_teams"
PROVIDER_ZOOM = "zoom"

# Short-lived OAuth state tokens (in-memory; fine for single-process dev).
_oauth_states: dict[str, tuple[str, str, datetime]] = {}

class AppConnectionRow(Base):
    __tablename__ = "app_connections"

    connection_id: Mapped[str] = mapped_column(String, primary_key=True)
    org_id: Mapped[str] = mapped_column(String, nullable=False)
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    account_email: Mapped[str | None] = mapped_column(String, nullable=True)
    access_token: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_expiry: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scopes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


async def require_user_context(
    org_id: Annotated[str, Depends(require_org_id)],
    x_user_id: str = Header(..., alias="X-User-Id"),
) -> tuple[str, str]:
    """Validate org + user headers and ensure the user belongs to the org."""

    user_id = x_user_id.strip()
    if not user_id:
        raise HTTPException(status_code=401, detail="Missing user context.")

    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(
            select(UserRow).where(UserRow.user_id == user_id, UserRow.org_id == org_id)
        )
        user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid user for this organization.")
    return org_id, user_id


async def require_admin_context(
    ctx: Annotated[tuple[str, str], Depends(require_user_context)],
) -> tuple[str, str]:
    """Require an organization administrator for connection-management APIs."""

    org_id, user_id = ctx
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(
            select(UserRow.role).where(
                UserRow.user_id == user_id, UserRow.org_id == org_id
            )
        )
        role = result.scalar_one_or_none()
    if role != "admin":
        raise HTTPException(status_code=403, detail="Organization admin access is required.")
    return ctx


def _store_oauth_state(org_id: str, user_id: str) -> str:
    state = secrets.token_urlsafe(32)
    _oauth_states[state] = (org_id, user_id, datetime.now(timezone.utc) + timedelta(minutes=10))
    return state


def _pop_oauth_state(state: str) -> tuple[str, str]:
    entry = _oauth_states.pop(state, None)
    if entry is None:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state.")
    org_id, user_id, expires = entry
    if datetime.now(timezone.utc) > expires:
        raise HTTPException(status_code=400, detail="OAuth state expired. Try connecting again.")
    return org_id, user_id


async def _get_connection(org_id: str, user_id: str, provider: str) -> AppConnectionRow | None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(
            select(AppConnectionRow).where(
                AppConnectionRow.org_id == org_id,
                AppConnectionRow.user_id == user_id,
                AppConnectionRow.provider == provider,
            )
        )
        return result.scalar_one_or_none()


async def _save_connection(
    *,
    org_id: str,
    user_id: str,
    provider: str,
    account_email: str | None,
    access_token: str,
    refresh_token: str | None,
    token_expiry: datetime | None,
    scopes: str | None,
) -> AppConnectionRow:
    now = datetime.now(timezone.utc)
    connection_id = str(uuid4())

    session_factory = get_session_factory()
    async with session_factory() as session:
        async with session.begin():
            stmt = pg_insert(AppConnectionRow).values(
                connection_id=connection_id,
                org_id=org_id,
                user_id=user_id,
                provider=provider,
                account_email=account_email,
                access_token=access_token,
                refresh_token=refresh_token,
                token_expiry=token_expiry,
                scopes=scopes,
                created_at=now,
                updated_at=now,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["org_id", "user_id", "provider"],
                set_={
                    "account_email": account_email,
                    "access_token": access_token,
                    "refresh_token": refresh_token,
                    "token_expiry": token_expiry,
                    "scopes": scopes,
                    "updated_at": now,
                },
            )
            await session.execute(stmt)
        result = await session.execute(
            select(AppConnectionRow).where(
                AppConnectionRow.org_id == org_id,
                AppConnectionRow.user_id == user_id,
                AppConnectionRow.provider == provider,
            )
        )
        return result.scalar_one()


async def _delete_connection(org_id: str, user_id: str, provider: str) -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        async with session.begin():
            result = await session.execute(
                select(AppConnectionRow).where(
                    AppConnectionRow.org_id == org_id,
                    AppConnectionRow.user_id == user_id,
                    AppConnectionRow.provider == provider,
                )
            )
            row = result.scalar_one_or_none()
            if row is not None:
                await session.delete(row)


def _credentials_from_row(row: AppConnectionRow) -> Credentials:
    settings = get_settings()
    return Credentials(
        token=row.access_token,
        refresh_token=row.refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        scopes=row.scopes.split() if row.scopes else None,
        expiry=row.token_expiry,
    )


async def _refresh_token_if_needed(row: AppConnectionRow) -> AppConnectionRow:
    """Refresh an expired access token and persist the new credentials."""

    if row.access_token.startswith("dev:"):
        return row

    creds = _credentials_from_row(row)
    if creds.valid:
        return row
    if not creds.refresh_token:
        raise HTTPException(
            status_code=401,
            detail="Google Calendar connection expired. Please reconnect.",
        )

    await asyncio.to_thread(creds.refresh, GoogleAuthRequest())

    return await _save_connection(
        org_id=row.org_id,
        user_id=row.user_id,
        provider=row.provider,
        account_email=row.account_email,
        access_token=creds.token or row.access_token,
        refresh_token=creds.refresh_token,
        token_expiry=creds.expiry,
        scopes=" ".join(creds.scopes or (row.scopes.split() if row.scopes else [])),
    )


async def _fetch_user_email(access_token: str) -> str | None:
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if resp.status_code != 200:
            return None
        return resp.json().get("email")


async def list_integrations(org_id: str, user_id: str) -> IntegrationsListResponse:
    # Imported locally to avoid making the low-level token module depend on the
    # higher-level controlled-setup service.
    from connection_setup import get_policy

    settings = get_settings()
    workspace_row = await _get_connection(org_id, user_id, PROVIDER_GOOGLE_WORKSPACE)
    teams_row = await _get_connection(org_id, user_id, PROVIDER_MICROSOFT_TEAMS)
    zoom_row = await _get_connection(org_id, user_id, PROVIDER_ZOOM)
    workspace_policy = await get_policy(org_id, user_id, PROVIDER_GOOGLE_WORKSPACE)
    teams_policy = await get_policy(org_id, user_id, PROVIDER_MICROSOFT_TEAMS)
    zoom_policy = await get_policy(org_id, user_id, PROVIDER_ZOOM)
    workspace = IntegrationInfo(
        provider=PROVIDER_GOOGLE_WORKSPACE,
        label="Google Workspace",
        connected=workspace_row is not None,
        account_email=workspace_row.account_email if workspace_row else None,
        connected_at=workspace_row.created_at.isoformat() if workspace_row else None,
        setup_status=(
            workspace_policy.status
            if workspace_policy
            else ("setup_required" if workspace_row else "not_connected")
        ),
        selected_resource_count=(
            len(json.loads(workspace_policy.included_resources)) if workspace_policy else 0
        ),
        last_synced_at=(
            workspace_policy.last_synced_at.isoformat()
            if workspace_policy and workspace_policy.last_synced_at
            else None
        ),
    )
    teams = IntegrationInfo(
        provider=PROVIDER_MICROSOFT_TEAMS,
        label="Microsoft Teams",
        connected=teams_row is not None,
        account_email=teams_row.account_email if teams_row else None,
        connected_at=teams_row.created_at.isoformat() if teams_row else None,
        setup_status=(
            teams_policy.status
            if teams_policy
            else ("setup_required" if teams_row else "not_connected")
        ),
        selected_resource_count=(
            len(json.loads(teams_policy.included_resources)) if teams_policy else 0
        ),
        last_synced_at=(
            teams_policy.last_synced_at.isoformat()
            if teams_policy and teams_policy.last_synced_at
            else None
        ),
    )
    zoom = IntegrationInfo(
        provider=PROVIDER_ZOOM,
        label="Zoom",
        connected=zoom_row is not None,
        account_email=zoom_row.account_email if zoom_row else None,
        connected_at=zoom_row.created_at.isoformat() if zoom_row else None,
        setup_status=(
            zoom_policy.status
            if zoom_policy
            else ("setup_required" if zoom_row else "not_connected")
        ),
        selected_resource_count=(
            len(json.loads(zoom_policy.included_resources)) if zoom_policy else 0
        ),
        last_synced_at=(
            zoom_policy.last_synced_at.isoformat()
            if zoom_policy and zoom_policy.last_synced_at
            else None
        ),
    )
    return IntegrationsListResponse(
        integrations=[workspace, teams, zoom],
        oauth_enabled=settings.google_oauth_enabled,
        microsoft_oauth_enabled=settings.microsoft_oauth_enabled,
        zoom_oauth_enabled=settings.zoom_oauth_enabled,
    )
