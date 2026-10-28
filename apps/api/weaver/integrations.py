"""Shared OAuth connection persistence and integration inventory."""

from __future__ import annotations

import asyncio
import json
import contextvars
from contextlib import contextmanager
from typing import Any
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
from fastapi import HTTPException
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from sqlalchemy import DateTime, String, Text, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Mapped, mapped_column

from keeper.auth import Base, require_admin_context, require_org_id, require_user_context
from core.config import get_settings
from core.database import get_session_factory
from core.models import (
    IntegrationInfo,
    IntegrationsListResponse,
    OAuthAuthorizeResponse,
)

# Re-export auth dependencies so existing route modules can keep importing from here.
__all_auth_deps__ = (require_user_context, require_admin_context, require_org_id)

logger = logging.getLogger(__name__)

# Google may return a subset of requested scopes; avoid hard failures on exchange.
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

PROVIDER_GOOGLE_WORKSPACE = "google_workspace"
PROVIDER_MICROSOFT_TEAMS = "microsoft_teams"
PROVIDER_ZOOM = "zoom"
PROVIDER_GITHUB = "github"

# Dev-only fallback when Redis is unavailable (single-process).
_oauth_states: dict[str, tuple[str, str, datetime]] = {}
_OAUTH_STATE_TTL_SECONDS = 600
_OAUTH_STATE_KEY = "loom:oauth_state:{state}"


def assert_dev_integrations_allowed() -> None:
    """Raise 403 unless connect-dev endpoints are explicitly enabled for development."""

    settings = get_settings()
    if not settings.dev_integrations_allowed:
        raise HTTPException(
            status_code=403,
            detail="Dev integration connections are disabled in this environment.",
        )


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


# --- Binding an OAuth result to the signed-in person ----------------------------------
#
# The provider redirects to a public callback, which anyone can be tricked into
# opening (an attacker can send a victim their own consent link). So the callback
# never saves tokens: it parks them under a one-time code and redirects to the web
# app, which finishes the connection with the signed-in person's own session. The
# connection is saved only when that person is the one who started the flow.

_OAUTH_PENDING_KEY = "loom:oauth_pending:{code}"
_OAUTH_PENDING_TTL_SECONDS = 600
_oauth_pending: dict[str, tuple[str, datetime]] = {}
_defer_save: contextvars.ContextVar[dict[str, str] | None] = contextvars.ContextVar(
    "defer_oauth_save", default=None
)


@contextmanager
def deferred_oauth_save():
    """Inside this block ``_save_connection`` parks the tokens and yields a code."""

    holder: dict[str, str] = {}
    token = _defer_save.set(holder)
    try:
        yield holder
    finally:
        _defer_save.reset(token)


def _park_connection(kwargs: dict[str, Any]) -> str:
    from core.token_crypto import encrypt_token

    code = secrets.token_urlsafe(32)
    payload = {
        **kwargs,
        "access_token": encrypt_token(kwargs["access_token"]),
        "refresh_token": encrypt_token(kwargs["refresh_token"]) if kwargs.get("refresh_token") else None,
        "token_expiry": kwargs["token_expiry"].isoformat() if kwargs.get("token_expiry") else None,
    }
    raw = json.dumps(payload)
    try:
        from core.redis_client import get_redis_sync

        get_redis_sync().setex(_OAUTH_PENDING_KEY.format(code=code), _OAUTH_PENDING_TTL_SECONDS, raw)
    except Exception as exc:  # noqa: BLE001 - Redis optional in development only
        if get_settings().app_env == "production":
            raise HTTPException(status_code=503, detail="OAuth storage unavailable.") from exc
        _oauth_pending[code] = (raw, datetime.now(timezone.utc) + timedelta(seconds=_OAUTH_PENDING_TTL_SECONDS))
    return code


async def complete_oauth_connection(code: str, org_id: str, user_id: str) -> dict[str, str]:
    """Save a parked connection for the signed-in person who started it."""

    from core.token_crypto import decrypt_token

    raw: str | None = None
    try:
        from core.redis_client import get_redis_sync

        raw = get_redis_sync().getdel(_OAUTH_PENDING_KEY.format(code=code))
    except Exception:  # noqa: BLE001
        if get_settings().app_env == "production":
            raise HTTPException(status_code=503, detail="OAuth storage unavailable.") from None
    if raw is None:
        entry = _oauth_pending.pop(code, None)
        if entry and entry[1] > datetime.now(timezone.utc):
            raw = entry[0]
    if raw is None:
        raise HTTPException(status_code=400, detail="This connection link expired. Connect again.")
    data = json.loads(raw)
    if data["org_id"] != org_id or data["user_id"] != user_id:
        logger.warning("OAuth completion by a different user than started it (provider %s)", data.get("provider"))
        raise HTTPException(
            status_code=403,
            detail="This connection was started by someone else. Start it from your own account.",
        )
    await _save_connection(
        org_id=org_id,
        user_id=user_id,
        provider=data["provider"],
        account_email=data.get("account_email"),
        access_token=decrypt_token(data["access_token"]),
        refresh_token=decrypt_token(data["refresh_token"]) if data.get("refresh_token") else None,
        token_expiry=datetime.fromisoformat(data["token_expiry"]) if data.get("token_expiry") else None,
        scopes=data.get("scopes"),
    )
    return {"provider": data["provider"]}


def _store_oauth_state(org_id: str, user_id: str) -> str:
    """Persist a short-lived OAuth CSRF state (Redis; memory fallback in development)."""

    state = secrets.token_urlsafe(32)
    payload = json.dumps({"org_id": org_id, "user_id": user_id})
    key = _OAUTH_STATE_KEY.format(state=state)
    settings = get_settings()
    try:
        from core.redis_client import get_redis_sync

        get_redis_sync().setex(key, _OAUTH_STATE_TTL_SECONDS, payload)
        return state
    except Exception as exc:  # noqa: BLE001 — Redis optional in development only
        if settings.app_env == "production":
            logger.exception("Redis unavailable for OAuth state store")
            raise HTTPException(
                status_code=503,
                detail="OAuth state storage unavailable. Try again shortly.",
            ) from exc
        logger.warning("Redis unavailable for OAuth state; using in-memory fallback: %s", exc)
        _oauth_states[state] = (
            org_id,
            user_id,
            datetime.now(timezone.utc) + timedelta(seconds=_OAUTH_STATE_TTL_SECONDS),
        )
        return state


def _pop_oauth_state(state: str) -> tuple[str, str]:
    """Consume OAuth state exactly once (atomic GETDEL when using Redis)."""

    key = _OAUTH_STATE_KEY.format(state=state)
    settings = get_settings()
    raw: str | None = None
    try:
        from core.redis_client import get_redis_sync

        client = get_redis_sync()
        # GETDEL is atomic on Redis 6.2+; fall back to GET+DEL.
        if hasattr(client, "getdel"):
            raw = client.getdel(key)
        else:
            pipe = client.pipeline()
            pipe.get(key)
            pipe.delete(key)
            raw, _ = pipe.execute()
    except Exception as exc:  # noqa: BLE001
        if settings.app_env == "production":
            logger.exception("Redis unavailable for OAuth state pop")
            raise HTTPException(
                status_code=503,
                detail="OAuth state storage unavailable. Try again shortly.",
            ) from exc
        logger.warning("Redis unavailable for OAuth state pop; trying memory: %s", exc)

    if raw is not None:
        try:
            data = json.loads(raw)
            return str(data["org_id"]), str(data["user_id"])
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise HTTPException(status_code=400, detail="Invalid or expired OAuth state.") from exc

    entry = _oauth_states.pop(state, None)
    if entry is None:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state.")
    org_id, user_id, expires = entry
    if datetime.now(timezone.utc) > expires:
        raise HTTPException(status_code=400, detail="OAuth state expired. Try connecting again.")
    return org_id, user_id


def _decrypt_connection_row(row: AppConnectionRow | None) -> AppConnectionRow | None:
    if row is None:
        return None
    from core.token_crypto import decrypt_token

    row.access_token = decrypt_token(row.access_token) or ""
    row.refresh_token = decrypt_token(row.refresh_token)
    return row


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
        return _decrypt_connection_row(result.scalar_one_or_none())


async def has_google_workspace_connection(org_id: str, user_id: str) -> bool:
    """True when this user has a stored Google Workspace connection."""

    row = await _get_connection(org_id, user_id, PROVIDER_GOOGLE_WORKSPACE)
    return row is not None and bool(row.access_token)


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
) -> AppConnectionRow | None:
    holder = _defer_save.get()
    if holder is not None:
        holder["code"] = _park_connection({
            "org_id": org_id, "user_id": user_id, "provider": provider,
            "account_email": account_email, "access_token": access_token,
            "refresh_token": refresh_token, "token_expiry": token_expiry, "scopes": scopes,
        })
        return None
    from core.token_crypto import encrypt_token

    now = datetime.now(timezone.utc)
    connection_id = str(uuid4())
    stored_access = encrypt_token(access_token) or ""
    stored_refresh = encrypt_token(refresh_token)

    session_factory = get_session_factory()
    async with session_factory() as session:
        async with session.begin():
            stmt = pg_insert(AppConnectionRow).values(
                connection_id=connection_id,
                org_id=org_id,
                user_id=user_id,
                provider=provider,
                account_email=account_email,
                access_token=stored_access,
                refresh_token=stored_refresh,
                token_expiry=token_expiry,
                scopes=scopes,
                created_at=now,
                updated_at=now,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["org_id", "user_id", "provider"],
                set_={
                    "account_email": account_email,
                    "access_token": stored_access,
                    "refresh_token": stored_refresh,
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
        return _decrypt_connection_row(result.scalar_one())  # type: ignore[return-value]


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


def _credentials_from_row(
    row: AppConnectionRow, *, include_scopes: bool = True
) -> Credentials:
    settings = get_settings()
    # google-auth compares expiry to naive UTC; strip tzinfo from TIMESTAMPTZ.
    expiry = row.token_expiry
    if expiry is not None and expiry.tzinfo is not None:
        expiry = expiry.astimezone(timezone.utc).replace(tzinfo=None)
    # Omitting scopes on refresh avoids Google's invalid_scope errors when the
    # stored grant includes openid/email/profile (or incremental grants).
    scopes = None
    if include_scopes and row.scopes and row.scopes != "dev":
        scopes = row.scopes.split()
    return Credentials(
        token=row.access_token,
        refresh_token=row.refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        scopes=scopes,
        expiry=expiry,
    )


def _normalize_token_expiry(expiry: datetime | None) -> datetime | None:
    if expiry is None:
        return None
    if expiry.tzinfo is None:
        return expiry.replace(tzinfo=timezone.utc)
    return expiry.astimezone(timezone.utc)


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
            detail="Google Workspace connection expired. Please reconnect.",
        )

    refresh_creds = _credentials_from_row(row, include_scopes=False)
    try:
        await asyncio.to_thread(refresh_creds.refresh, GoogleAuthRequest())
    except Exception as exc:  # noqa: BLE001 - surface provider errors cleanly
        from google.auth.exceptions import RefreshError

        if isinstance(exc, RefreshError):
            logger.warning("Google token refresh failed for %s: %s", row.provider, exc)
            raise HTTPException(
                status_code=401,
                detail=(
                    "Google Workspace authorization is no longer valid. "
                    "Disconnect and reconnect Google Workspace, then try again."
                ),
            ) from exc
        raise

    retained_scopes = row.scopes.split() if row.scopes else []
    return await _save_connection(
        org_id=row.org_id,
        user_id=row.user_id,
        provider=row.provider,
        account_email=row.account_email,
        access_token=refresh_creds.token or row.access_token,
        refresh_token=refresh_creds.refresh_token or row.refresh_token,
        token_expiry=_normalize_token_expiry(refresh_creds.expiry),
        scopes=" ".join(refresh_creds.scopes or retained_scopes),
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
    from weaver.connection_setup import get_policy

    settings = get_settings()
    workspace_row = await _get_connection(org_id, user_id, PROVIDER_GOOGLE_WORKSPACE)
    teams_row = await _get_connection(org_id, user_id, PROVIDER_MICROSOFT_TEAMS)
    zoom_row = await _get_connection(org_id, user_id, PROVIDER_ZOOM)
    github_row = await _get_connection(org_id, user_id, PROVIDER_GITHUB)
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
    from weaver.github_access import connection_public_view, policy_from_scopes

    if github_row is not None:
        github_policy = policy_from_scopes(github_row.scopes)
        github_public = connection_public_view(github_row.account_email or "", github_policy)
        github = IntegrationInfo(
            provider=PROVIDER_GITHUB,
            label="GitHub",
            connected=True,
            account_email=github_row.account_email,
            connected_at=github_row.created_at.isoformat(),
            setup_status=str(github_public["setup_status"]),
            selected_resource_count=int(github_public["selected_resource_count"]),
        )
    else:
        github = IntegrationInfo(
            provider=PROVIDER_GITHUB,
            label="GitHub",
            connected=False,
            setup_status="not_connected",
        )
    return IntegrationsListResponse(
        integrations=[workspace, teams, zoom, github],
        oauth_enabled=settings.google_oauth_enabled,
        microsoft_oauth_enabled=settings.microsoft_oauth_enabled,
        zoom_oauth_enabled=settings.zoom_oauth_enabled,
        dev_integrations_allowed=settings.dev_integrations_allowed,
    )
