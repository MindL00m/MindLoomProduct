"""Google Calendar and other workspace app integrations via OAuth."""

from __future__ import annotations

import asyncio
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
from google_auth_oauthlib.flow import Flow
from sqlalchemy import DateTime, String, Text, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Mapped, mapped_column

from auth import Base, UserRow, require_org_id
from config import get_settings
from database import get_session_factory
from models import (
    CalendarEvent,
    CalendarEventsResponse,
    IntegrationInfo,
    IntegrationsListResponse,
    OAuthAuthorizeResponse,
)

logger = logging.getLogger(__name__)

# Google may return a subset of requested scopes; avoid hard failures on exchange.
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

PROVIDER_GOOGLE_CALENDAR = "google_calendar"
CALENDAR_SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]

# Short-lived OAuth state tokens (in-memory; fine for single-process dev).
_oauth_states: dict[str, tuple[str, str, datetime]] = {}

_DEV_MOCK_EVENTS = [
    CalendarEvent(
        id="dev-1",
        title="Team standup",
        start=(datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
        end=(datetime.now(timezone.utc) + timedelta(hours=2, minutes=30)).isoformat(),
        location="Google Meet",
    ),
    CalendarEvent(
        id="dev-2",
        title="Product review",
        start=(datetime.now(timezone.utc) + timedelta(days=1, hours=3)).isoformat(),
        end=(datetime.now(timezone.utc) + timedelta(days=1, hours=4)).isoformat(),
        location="Conference Room A",
    ),
    CalendarEvent(
        id="dev-3",
        title="Company Brain planning",
        start=(datetime.now(timezone.utc) + timedelta(days=2)).replace(
            hour=14, minute=0, second=0, microsecond=0
        ).isoformat(),
        end=(datetime.now(timezone.utc) + timedelta(days=2)).replace(
            hour=15, minute=0, second=0, microsecond=0
        ).isoformat(),
        all_day=False,
    ),
]


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


def _google_flow() -> Flow:
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
        scopes=CALENDAR_SCOPES,
        redirect_uri=settings.google_oauth_redirect_uri,
    )


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
        scopes=row.scopes.split() if row.scopes else CALENDAR_SCOPES,
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
        scopes=" ".join(creds.scopes or CALENDAR_SCOPES),
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


async def _fetch_calendar_events(access_token: str) -> list[CalendarEvent]:
    now = datetime.now(timezone.utc)
    time_min = now.isoformat()
    time_max = (now + timedelta(days=14)).isoformat()
    params = {
        "calendarId": "primary",
        "timeMin": time_min,
        "timeMax": time_max,
        "maxResults": "25",
        "singleEvents": "true",
        "orderBy": "startTime",
    }
    url = "https://www.googleapis.com/calendar/v3/calendars/primary/events"

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(
            url,
            params=params,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if resp.status_code == 401:
            raise HTTPException(status_code=401, detail="Google Calendar authorization expired.")
        if resp.status_code != 200:
            logger.warning("Calendar API error %s: %s", resp.status_code, resp.text[:200])
            raise HTTPException(status_code=502, detail="Could not fetch calendar events from Google.")

    items = resp.json().get("items", [])
    events: list[CalendarEvent] = []
    for item in items:
        start = item.get("start", {})
        end = item.get("end", {})
        all_day = "date" in start
        start_val = start.get("dateTime") or start.get("date")
        end_val = end.get("dateTime") or end.get("date")
        if not start_val:
            continue
        events.append(
            CalendarEvent(
                id=item.get("id", str(uuid4())),
                title=item.get("summary") or "(No title)",
                start=start_val,
                end=end_val,
                all_day=all_day,
                location=item.get("location"),
                html_link=item.get("htmlLink"),
            )
        )
    return events


async def list_integrations(org_id: str, user_id: str) -> IntegrationsListResponse:
    settings = get_settings()
    row = await _get_connection(org_id, user_id, PROVIDER_GOOGLE_CALENDAR)
    calendar = IntegrationInfo(
        provider=PROVIDER_GOOGLE_CALENDAR,
        label="Google Calendar",
        connected=row is not None,
        account_email=row.account_email if row else None,
        connected_at=row.created_at.isoformat() if row else None,
    )
    return IntegrationsListResponse(
        integrations=[calendar],
        oauth_enabled=settings.google_oauth_enabled,
    )


async def start_google_calendar_oauth(org_id: str, user_id: str) -> OAuthAuthorizeResponse:
    settings = get_settings()
    if not settings.google_oauth_enabled:
        raise HTTPException(
            status_code=503,
            detail=(
                "Google OAuth is not configured. Set GOOGLE_CLIENT_ID and "
                "GOOGLE_CLIENT_SECRET, or use dev connect."
            ),
        )

    flow = _google_flow()
    state = _store_oauth_state(org_id, user_id)
    authorization_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=state,
    )
    return OAuthAuthorizeResponse(authorization_url=authorization_url)


async def connect_google_calendar_dev(org_id: str, user_id: str) -> IntegrationInfo:
    """Simulated connect when Google OAuth credentials are not configured."""

    settings = get_settings()
    if settings.google_oauth_enabled:
        raise HTTPException(
            status_code=400,
            detail="Google OAuth is configured — use the real connect flow instead.",
        )

    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(select(UserRow).where(UserRow.user_id == user_id))
        user = result.scalar_one()

    row = await _save_connection(
        org_id=org_id,
        user_id=user_id,
        provider=PROVIDER_GOOGLE_CALENDAR,
        account_email=user.email,
        access_token=f"dev:{user_id}",
        refresh_token=None,
        token_expiry=None,
        scopes="dev",
    )
    return IntegrationInfo(
        provider=PROVIDER_GOOGLE_CALENDAR,
        label="Google Calendar",
        connected=True,
        account_email=row.account_email,
        connected_at=row.created_at.isoformat(),
    )


async def handle_google_calendar_callback(code: str, state: str) -> str:
    """Exchange OAuth code for tokens and redirect back to the frontend."""

    settings = get_settings()
    org_id, user_id = _pop_oauth_state(state)

    flow = _google_flow()
    flow.fetch_token(code=code)
    creds = flow.credentials

    account_email = await _fetch_user_email(creds.token or "")
    await _save_connection(
        org_id=org_id,
        user_id=user_id,
        provider=PROVIDER_GOOGLE_CALENDAR,
        account_email=account_email,
        access_token=creds.token or "",
        refresh_token=creds.refresh_token,
        token_expiry=creds.expiry,
        scopes=" ".join(creds.scopes or CALENDAR_SCOPES),
    )

    return f"{settings.frontend_url.rstrip('/')}/dashboard?tab=apps&connected=google_calendar"


async def disconnect_google_calendar(org_id: str, user_id: str) -> None:
    await _delete_connection(org_id, user_id, PROVIDER_GOOGLE_CALENDAR)


async def get_calendar_events(org_id: str, user_id: str) -> CalendarEventsResponse:
    row = await _get_connection(org_id, user_id, PROVIDER_GOOGLE_CALENDAR)
    if row is None:
        raise HTTPException(status_code=404, detail="Google Calendar is not connected.")

    if row.access_token.startswith("dev:"):
        return CalendarEventsResponse(
            account_email=row.account_email,
            events=_DEV_MOCK_EVENTS,
        )

    row = await _refresh_token_if_needed(row)
    events = await _fetch_calendar_events(row.access_token)
    return CalendarEventsResponse(account_email=row.account_email, events=events)
