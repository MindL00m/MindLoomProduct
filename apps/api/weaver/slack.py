"""Per-organization Slack bot ("Loombot").

An admin creates a Slack app from a manifest this module generates, then pastes
the bot token and signing secret into MindLoom. Slack delivers events to
``/webhooks/slack/events/{install_id}``.

Mentions are answered in the API process with the same retrieval path as Ask.
Channel messages are marked dirty and ingested by the worker, one thread at a
time, so a long import never delays a reply.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import httpx
from fastapi import HTTPException
from redis.asyncio import Redis
from sqlalchemy import text

from core.config import get_settings
from core.database import get_session_factory
from core.token_crypto import decrypt_token, encrypt_token

logger = logging.getLogger(__name__)

PROVIDER = "slack"
REQUIRED_SCOPES = (
    "app_mentions:read",
    "chat:write",
    "channels:history",
    "groups:history",
    "im:history",
    "channels:read",
    "groups:read",
    "users:read",
    "users:read.email",
)
BOT_EVENTS = ("app_mention", "message.channels", "message.groups", "message.im")
_DRAFT_CHALLENGE_WINDOW = timedelta(minutes=30)
_SIGNATURE_MAX_AGE_SECONDS = 300
_EVENT_DEDUPE_SECONDS = 3600
_DIRTY_KEY = "slack:dirty"
_FATAL_AUTH_ERRORS = {"invalid_auth", "token_revoked", "account_inactive", "not_authed"}
_MENTION_RE = re.compile(r"<@([A-Z0-9]+)(?:\|[^>]+)?>")
_SOURCE_RE = re.compile(r"\[SOURCE:\s*([^\]\s]+)\s*\]", re.IGNORECASE)
_THINKING = "Thinking…"
_MAX_THREAD_MESSAGES = 500
_MAX_BACKFILL_THREADS = 200

Schedule = Callable[..., None]


class SlackApiError(Exception):
    """Slack Web API returned ok=false or an HTTP error."""

    def __init__(self, error: str) -> None:
        super().__init__(error)
        self.error = error


class SlackIngestSkipped(Exception):
    """This thread cannot be ingested until something about it changes."""


def verify_slack_signature(
    body: bytes,
    timestamp: str,
    signature: str,
    secret: str,
    *,
    now: float | None = None,
) -> bool:
    """Slack's v0 HMAC check. Requests older than five minutes are rejected."""

    if not secret or not timestamp or not signature:
        return False
    try:
        sent = int(timestamp)
    except (TypeError, ValueError):
        return False
    current = time.time() if now is None else now
    if abs(current - sent) > _SIGNATURE_MAX_AGE_SECONDS:
        return False
    digest = hmac.new(
        secret.encode("utf-8"),
        b"v0:" + timestamp.encode("utf-8") + b":" + body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(f"v0={digest}", signature)


def strip_bot_mention(text_value: str, bot_user_id: str | None) -> str:
    """Remove the @Loombot mention Slack wraps as ``<@U123>``."""

    if bot_user_id:
        text_value = re.sub(
            rf"<@{re.escape(bot_user_id)}(?:\|[^>]+)?>",
            " ",
            text_value,
        )
    return " ".join(text_value.split())


def public_api_warning() -> str | None:
    url = (get_settings().public_api_url or "").strip().lower()
    if not url or "localhost" in url or "127.0.0.1" in url:
        return (
            "PUBLIC_API_URL is a localhost address, so Slack cannot deliver events. "
            "Run a tunnel (for example `cloudflared tunnel --url http://localhost:8000`) "
            "and set PUBLIC_API_URL to that HTTPS address, then create the app."
        )
    if not url.startswith("https://"):
        return "PUBLIC_API_URL must be https so Slack can verify the event URL."
    return None


def event_url(install_id: str) -> str:
    base = get_settings().public_api_url.rstrip("/")
    return f"{base}/webhooks/slack/events/{install_id}"


def build_manifest(install_id: str) -> dict[str, Any]:
    """Slack app manifest. The admin pastes this at api.slack.com/apps."""

    return {
        "display_information": {
            "name": "Loombot",
            "description": "Ask MindLoom from Slack and save channel knowledge.",
            "background_color": "#003135",
        },
        "features": {
            "bot_user": {"display_name": "Loombot", "always_online": True},
        },
        "oauth_config": {"scopes": {"bot": list(REQUIRED_SCOPES)}},
        "settings": {
            "event_subscriptions": {
                "request_url": event_url(install_id),
                "bot_events": list(BOT_EVENTS),
            },
            "org_deploy_enabled": False,
            "socket_mode_enabled": False,
            "token_rotation_enabled": False,
        },
    }


def _header(headers: Mapping[str, str], name: str) -> str:
    wanted = name.lower()
    for key, value in headers.items():
        if str(key).lower() == wanted:
            return str(value)
    return ""


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _channels(install: Mapping[str, Any]) -> list[str]:
    raw = install.get("ingest_channel_ids") or "[]"
    if isinstance(raw, list):
        return [str(item) for item in raw]
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def _scope_set(header_value: str | None) -> set[str]:
    return {part.strip() for part in str(header_value or "").split(",") if part.strip()}


def _token(install: Mapping[str, Any]) -> str:
    return decrypt_token(install.get("bot_token_enc")) or ""


def _signing_secret(install: Mapping[str, Any]) -> str:
    return decrypt_token(install.get("signing_secret_enc")) or ""


def _display_name(user: Mapping[str, Any]) -> str:
    profile = user.get("profile") or {}
    if not isinstance(profile, Mapping):
        profile = {}
    return str(
        profile.get("display_name")
        or profile.get("real_name")
        or user.get("real_name")
        or user.get("name")
        or user.get("id")
        or "Someone"
    )


def _slack_time(ts: str) -> datetime:
    return datetime.fromtimestamp(float(ts), tz=timezone.utc)


# --- Storage ----------------------------------------------------------------


async def _fetch(sql: str, params: dict[str, Any]) -> dict[str, Any] | None:
    factory = get_session_factory()
    async with factory() as session:
        row = (await session.execute(text(sql), params)).mappings().one_or_none()
    return dict(row) if row else None


async def fetch_install(install_id: str) -> dict[str, Any] | None:
    return await _fetch(
        "SELECT * FROM slack_installations WHERE install_id=:id",
        {"id": install_id},
    )


async def fetch_install_for_org(org_id: str) -> dict[str, Any] | None:
    return await _fetch(
        "SELECT * FROM slack_installations WHERE org_id=:org",
        {"org": org_id},
    )


async def fetch_install_by_team(team_id: str) -> dict[str, Any] | None:
    return await _fetch(
        "SELECT * FROM slack_installations WHERE team_id=:team",
        {"team": team_id},
    )


async def _user_by_email(org_id: str, email: str) -> dict[str, Any] | None:
    return await _fetch(
        """
        SELECT user_id, email, coalesce(name, email) AS name, role
        FROM users
        WHERE org_id=:org AND lower(email)=lower(:email)
        LIMIT 1
        """,
        {"org": org_id, "email": email},
    )


def installation_view(install: dict[str, Any] | None, *, admin: bool) -> dict[str, Any]:
    warning = public_api_warning()
    if install is None:
        return {
            "connected": False,
            "status": "not_connected",
            "public_api_warning": warning,
            "event_url": None,
            "manifest": None,
            "missing_scopes": [],
            "granted_scopes": [],
            "settings": None,
        }
    granted = [part for part in str(install.get("scopes") or "").split(",") if part]
    missing = [scope for scope in REQUIRED_SCOPES if granted and scope not in granted]
    view: dict[str, Any] = {
        "connected": install.get("status") == "active",
        "status": install.get("status") or "draft",
        "install_id": install.get("install_id"),
        "team_name": install.get("team_name"),
        "bot_user_id": install.get("bot_user_id"),
        "last_event_at": _iso(install.get("last_event_at")),
        "last_error": install.get("last_error"),
        "public_api_warning": warning,
        "missing_scopes": missing,
        "granted_scopes": granted,
        "settings": {
            "ingest_channel_ids": _channels(install),
            "ingest_mention_threads": bool(install.get("ingest_mention_threads", True)),
            "answer_mentions": bool(install.get("answer_mentions", True)),
            "unlinked_user_policy": install.get("unlinked_user_policy") or "ignore",
        },
    }
    if admin:
        install_id = str(install["install_id"])
        view["event_url"] = event_url(install_id)
        view["manifest"] = build_manifest(install_id)
    else:
        view["event_url"] = None
        view["manifest"] = None
    return view


async def create_draft(org_id: str, user_id: str) -> dict[str, Any]:
    existing = await fetch_install_for_org(org_id)
    if existing is not None:
        # Restart the unsigned challenge window without changing the event URL.
        if not _signing_secret(existing):
            factory = get_session_factory()
            async with factory() as session, session.begin():
                await session.execute(
                    text("""
                        UPDATE slack_installations
                        SET created_at=now(), updated_at=now()
                        WHERE install_id=:id AND signing_secret_enc IS NULL
                    """),
                    {"id": existing["install_id"]},
                )
            existing = await fetch_install(str(existing["install_id"]))
        return installation_view(existing, admin=True)
    install_id = str(uuid4())
    factory = get_session_factory()
    async with factory() as session, session.begin():
        await session.execute(
            text("""
                INSERT INTO slack_installations (install_id, org_id, installed_by)
                VALUES (:id, :org, :user)
            """),
            {"id": install_id, "org": org_id, "user": user_id},
        )
    created = await fetch_install(install_id)
    return installation_view(created, admin=True)


async def save_credentials(
    org_id: str, bot_token: str, signing_secret: str
) -> dict[str, Any]:
    """Validate a bot token with Slack and store it encrypted."""

    row = await fetch_install_for_org(org_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Create the Slack app draft first.")
    token = bot_token.strip()
    secret = signing_secret.strip()
    if not token.startswith("xoxb-"):
        raise HTTPException(
            status_code=400,
            detail="Paste the Bot User OAuth Token. It starts with xoxb-.",
        )
    if len(secret) < 8:
        raise HTTPException(status_code=400, detail="Paste the Slack signing secret.")
    try:
        auth = await slack_api(token, "auth.test")
    except SlackApiError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Slack rejected that token ({exc.error}).",
        ) from exc
    team_id = str(auth.get("team_id") or "").strip()
    if not team_id:
        raise HTTPException(
            status_code=400,
            detail="Slack did not return a workspace id for this token.",
        )
    other = await fetch_install_by_team(team_id)
    if other is not None and other.get("org_id") != org_id:
        raise HTTPException(
            status_code=409,
            detail="This Slack workspace is already connected to another MindLoom organization.",
        )
    granted = _scope_set(str(auth.get("_scopes") or ""))
    factory = get_session_factory()
    async with factory() as session, session.begin():
        await session.execute(
            text("""
                UPDATE slack_installations
                SET team_id=:team_id,
                    team_name=:team_name,
                    bot_user_id=:bot_user_id,
                    bot_token_enc=:token,
                    signing_secret_enc=:secret,
                    scopes=:scopes,
                    status='active',
                    last_error=NULL,
                    updated_at=now()
                WHERE org_id=:org
            """),
            {
                "org": org_id,
                "team_id": team_id,
                "team_name": str(auth.get("team") or ""),
                "bot_user_id": str(auth.get("user_id") or ""),
                "token": encrypt_token(token),
                "secret": encrypt_token(secret),
                "scopes": ",".join(sorted(granted)),
            },
        )
    saved = await fetch_install_for_org(org_id)
    return installation_view(saved, admin=True)


async def save_settings(org_id: str, settings: Mapping[str, Any]) -> dict[str, Any]:
    channels = [str(item).strip() for item in settings.get("ingest_channel_ids") or [] if str(item).strip()]
    policy = str(settings.get("unlinked_user_policy") or "ignore")
    if policy not in {"ignore", "org"}:
        raise HTTPException(status_code=422, detail="Unknown unlinked-user policy.")
    factory = get_session_factory()
    async with factory() as session, session.begin():
        result = await session.execute(
            text("""
                UPDATE slack_installations
                SET ingest_channel_ids=:channels,
                    ingest_mention_threads=:threads,
                    answer_mentions=:answers,
                    unlinked_user_policy=:policy,
                    updated_at=now()
                WHERE org_id=:org AND status IN ('active', 'error')
                RETURNING install_id
            """),
            {
                "org": org_id,
                "channels": json.dumps(channels),
                "threads": bool(settings.get("ingest_mention_threads", True)),
                "answers": bool(settings.get("answer_mentions", True)),
                "policy": policy,
            },
        )
        if result.scalar_one_or_none() is None:
            raise HTTPException(status_code=409, detail="Connect Slack before changing these settings.")
    saved = await fetch_install_for_org(org_id)
    return installation_view(saved, admin=True)


async def disconnect(org_id: str) -> dict[str, Any]:
    factory = get_session_factory()
    async with factory() as session, session.begin():
        await session.execute(
            text("DELETE FROM slack_installations WHERE org_id=:org"),
            {"org": org_id},
        )
    return installation_view(None, admin=True)


async def _touch(install_id: str) -> None:
    factory = get_session_factory()
    async with factory() as session, session.begin():
        await session.execute(
            text("""
                UPDATE slack_installations
                SET last_event_at=now(), updated_at=now()
                WHERE install_id=:id
            """),
            {"id": install_id},
        )


async def set_error(install_id: str, message: str, *, fatal: bool = False) -> None:
    factory = get_session_factory()
    async with factory() as session, session.begin():
        await session.execute(
            text("""
                UPDATE slack_installations
                SET last_error=:message,
                    status=CASE WHEN :fatal THEN 'error' ELSE status END,
                    updated_at=now()
                WHERE install_id=:id
            """),
            {"id": install_id, "message": message[:500], "fatal": fatal},
        )


async def clear_error(install_id: str) -> None:
    factory = get_session_factory()
    async with factory() as session, session.begin():
        await session.execute(
            text("""
                UPDATE slack_installations
                SET last_error=NULL,
                    status=CASE WHEN status='error' THEN 'active' ELSE status END,
                    updated_at=now()
                WHERE install_id=:id
            """),
            {"id": install_id},
        )


# --- Slack Web API ----------------------------------------------------------


@asynccontextmanager
async def _redis():
    client = Redis.from_url(get_settings().redis_url, decode_responses=True)
    try:
        yield client
    finally:
        await client.aclose()


async def slack_api(token: str, method: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    """Call a Slack Web API method. Honours HTTP 429 Retry-After."""

    body = {key: value for key, value in (args or {}).items() if value is not None}
    async with httpx.AsyncClient(timeout=20) as client:
        for _attempt in range(3):
            response = await client.post(
                f"https://slack.com/api/{method}",
                headers={"Authorization": f"Bearer {token}"},
                json=body,
            )
            if response.status_code == 429:
                await asyncio.sleep(float(response.headers.get("Retry-After") or "1"))
                continue
            if response.status_code >= 400:
                raise SlackApiError(f"http_{response.status_code}")
            payload = response.json()
            if not payload.get("ok"):
                raise SlackApiError(str(payload.get("error") or "slack_error"))
            scopes = response.headers.get("x-oauth-scopes")
            if scopes:
                payload["_scopes"] = scopes
            return payload
    raise SlackApiError("rate_limited")


async def _cache_get(key: str) -> str | None:
    try:
        async with _redis() as redis:
            return await redis.get(key)
    except Exception:  # noqa: BLE001 - cache misses must not break Slack calls
        logger.warning("Slack cache read failed for %s", key, exc_info=True)
        return None


async def _cache_set(key: str, value: str, ttl: int) -> None:
    try:
        async with _redis() as redis:
            await redis.set(key, value, ex=ttl)
    except Exception:  # noqa: BLE001
        logger.warning("Slack cache write failed for %s", key, exc_info=True)


async def cached_user(token: str, team_id: str, user_id: str) -> dict[str, Any]:
    user_id = str(user_id or "").strip()
    if not user_id:
        return {}
    key = f"slack:user:{team_id}:{user_id}"
    cached = await _cache_get(key)
    if cached:
        parsed = json.loads(cached)
        if isinstance(parsed, dict):
            return parsed
    payload = await slack_api(token, "users.info", {"user": user_id})
    user = payload.get("user") or {}
    if isinstance(user, dict):
        await _cache_set(key, json.dumps(user), 3600)
        return user
    return {}


async def claim_event(event_id: str) -> bool:
    """True when this process is the first to see ``event_id`` within an hour."""

    if not event_id:
        return True
    try:
        async with _redis() as redis:
            return bool(await redis.set(f"slack:event:{event_id}", "1", nx=True, ex=_EVENT_DEDUPE_SECONDS))
    except Exception:  # noqa: BLE001
        logger.warning("Slack event dedupe unavailable", exc_info=True)
        return True


async def _sadd(key: str, member: str, ttl: int | None = None) -> None:
    async with _redis() as redis:
        await redis.sadd(key, member)
        if ttl:
            await redis.expire(key, ttl)


async def remember_mention_thread(install_id: str, channel: str, thread_ts: str) -> None:
    await _sadd(f"slack:mention-threads:{install_id}", f"{channel}|{thread_ts}", 90 * 24 * 3600)


async def is_mention_thread(install_id: str, channel: str, thread_ts: str) -> bool:
    try:
        async with _redis() as redis:
            return bool(await redis.sismember(
                f"slack:mention-threads:{install_id}", f"{channel}|{thread_ts}"
            ))
    except Exception:  # noqa: BLE001
        logger.warning("Slack mention-thread lookup failed", exc_info=True)
        return False


async def mark_dirty(install_id: str, channel: str, thread_ts: str) -> None:
    try:
        await _sadd(_DIRTY_KEY, f"{install_id}|{channel}|{thread_ts}")
    except Exception:  # noqa: BLE001
        logger.warning("Could not queue Slack thread %s/%s", channel, thread_ts, exc_info=True)


# --- Events -----------------------------------------------------------------


async def accept_slack_request(
    install_id: str,
    body: bytes,
    headers: Mapping[str, str],
    schedule: Schedule,
) -> dict[str, Any]:
    """Verify one Slack delivery and queue the work. Returns Slack's ack body."""

    install = await fetch_install(install_id)
    if install is None:
        raise HTTPException(status_code=404, detail="Unknown Slack installation.")
    secret = _signing_secret(install)
    if secret and not verify_slack_signature(
        body,
        _header(headers, "x-slack-request-timestamp"),
        _header(headers, "x-slack-signature"),
        secret,
    ):
        raise HTTPException(status_code=401, detail="Invalid Slack signature.")
    try:
        payload = json.loads(body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid Slack payload.") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid Slack payload.")

    if not secret:
        allowed = payload.get("type") == "url_verification"
        created = install.get("created_at")
        fresh = isinstance(created, datetime) and (
            datetime.now(timezone.utc) - _aware(created) <= _DRAFT_CHALLENGE_WINDOW
        )
        if not (allowed and fresh):
            raise HTTPException(
                status_code=401,
                detail="Paste the Slack signing secret in MindLoom before saving the event URL.",
            )

    if payload.get("type") == "url_verification":
        challenge = payload.get("challenge")
        if not isinstance(challenge, str):
            raise HTTPException(status_code=400, detail="Missing Slack challenge.")
        return {"challenge": challenge}

    team = str(payload.get("team_id") or "")
    if install.get("team_id") and team and team != install["team_id"]:
        return {"ok": True}
    if payload.get("type") != "event_callback":
        return {"ok": True}
    if _header(headers, "x-slack-retry-num"):
        return {"ok": True}
    if not await claim_event(str(payload.get("event_id") or "")):
        return {"ok": True}
    if install.get("status") != "active":
        return {"ok": True}
    schedule(process_slack_event, install_id, payload)
    return {"ok": True}


def _is_ours(event: Mapping[str, Any], install: Mapping[str, Any]) -> bool:
    if event.get("bot_id") or event.get("subtype"):
        return True
    bot_user = str(install.get("bot_user_id") or "")
    return bool(bot_user) and event.get("user") == bot_user


async def handle_event(install: Mapping[str, Any], payload: Mapping[str, Any]) -> None:
    event = payload.get("event") or {}
    if not isinstance(event, Mapping):
        return
    if _is_ours(event, install):
        return
    kind = str(event.get("type") or "")
    channel = str(event.get("channel") or "")
    ts = str(event.get("ts") or "")
    thread_ts = str(event.get("thread_ts") or ts)
    install_id = str(install["install_id"])
    if not channel or not thread_ts:
        return
    if kind == "app_mention":
        if install.get("answer_mentions", True):
            await answer_mention(install, event)
        if install.get("ingest_mention_threads", True):
            await remember_mention_thread(install_id, channel, thread_ts)
            await mark_dirty(install_id, channel, thread_ts)
        elif channel in set(_channels(install)):
            await mark_dirty(install_id, channel, thread_ts)
        return
    if kind != "message":
        return
    selected = channel in set(_channels(install))
    mentioned = (
        bool(install.get("ingest_mention_threads", True))
        and await is_mention_thread(install_id, channel, thread_ts)
    )
    if selected or mentioned:
        await mark_dirty(install_id, channel, thread_ts)


async def process_slack_event(install_id: str, payload: Mapping[str, Any]) -> None:
    install = await fetch_install(install_id)
    if install is None or install.get("status") != "active":
        return
    try:
        await handle_event(install, payload)
    except Exception as exc:  # noqa: BLE001 - one event must not crash the process
        logger.exception("Slack event failed for %s", install_id)
        fatal = isinstance(exc, SlackApiError) and exc.error in _FATAL_AUTH_ERRORS
        await set_error(install_id, str(exc), fatal=fatal)
        return
    await _touch(install_id)


# --- Answering --------------------------------------------------------------


def _mrkdwn(text_value: str) -> str:
    text_value = re.sub(
        r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
        r"<\2|\1>",
        text_value,
    )
    text_value = re.sub(r"^#{1,6}\s+(.+)$", r"*\1*", text_value, flags=re.MULTILINE)
    text_value = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text_value, flags=re.DOTALL)
    return text_value


def _draft_note(response: Any) -> str:
    labels: list[str] = []
    if getattr(response, "proposed_email", None) or getattr(response, "proposed_message", None):
        labels.append("a message")
    if getattr(response, "proposed_workspace", None):
        labels.append("a workspace")
    if getattr(response, "proposed_pull_request", None):
        labels.append("a pull request")
    if not labels:
        return ""
    base = get_settings().frontend_url.rstrip("/")
    return (
        f"\n\nI drafted {', '.join(labels)}, but it can't be approved from Slack. "
        f"Ask again in MindLoom to review it: {base}/dashboard?tab=ask"
    )


async def format_slack_answer(answer: str, sources: list[Any], org_id: str) -> str:
    """Turn Ask's ``[SOURCE: id]`` markers into numbered Slack footnotes."""

    by_id: dict[str, Any] = {}
    for source in sources or []:
        chunk_id = getattr(source, "chunk_id", None)
        if chunk_id:
            by_id[str(chunk_id)] = source
    footnotes: list[str] = []
    seen: dict[str, int] = {}

    async def _label(chunk_id: str) -> tuple[str, str | None]:
        source = by_id.get(chunk_id)
        citation = getattr(source, "citation", None) if source is not None else None
        if citation is None:
            try:
                from brain.documents import get_citation

                citation = await get_citation(chunk_id, org_id=org_id)
            except Exception:  # noqa: BLE001 - a missing citation still leaves the answer
                citation = None
        label = str(getattr(citation, "source_label", "") or "Source")
        url = getattr(citation, "source_url", None)
        return label, str(url) if url else None

    async def _replace(match: re.Match[str]) -> str:
        chunk_id = match.group(1)
        if chunk_id not in seen:
            label, url = await _label(chunk_id)
            seen[chunk_id] = len(footnotes) + 1
            rendered = f"<{url}|{label}>" if url else label
            footnotes.append(f"[{seen[chunk_id]}] {rendered}")
        return f"[{seen[chunk_id]}]"

    parts: list[str] = []
    cursor = 0
    for match in _SOURCE_RE.finditer(answer or ""):
        parts.append(answer[cursor:match.start()])
        parts.append(await _replace(match))
        cursor = match.end()
    parts.append((answer or "")[cursor:])
    text_value = _mrkdwn("".join(parts)).strip()
    if len(text_value) > 3200:
        text_value = text_value[:3200].rstrip() + "…"
    if footnotes:
        text_value = f"{text_value}\n\n" + "\n".join(footnotes)
    return text_value[:3900]


async def _thread_messages(token: str, channel: str, thread_ts: str) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    cursor: str | None = None
    while len(messages) < _MAX_THREAD_MESSAGES:
        args: dict[str, Any] = {"channel": channel, "ts": thread_ts, "limit": 200}
        if cursor:
            args["cursor"] = cursor
        payload = await slack_api(token, "conversations.replies", args)
        messages.extend(item for item in (payload.get("messages") or []) if isinstance(item, dict))
        cursor = str((payload.get("response_metadata") or {}).get("next_cursor") or "")
        if not cursor:
            break
    messages.sort(key=lambda item: float(item.get("ts") or 0))
    return messages


async def _history(install: Mapping[str, Any], token: str, channel: str, thread_ts: str, current_ts: str) -> list[Any]:
    from core.models import ChatMessage

    bot_user = str(install.get("bot_user_id") or "")
    team_id = str(install.get("team_id") or "")
    turns: list[Any] = []
    names: dict[str, str] = {}
    try:
        messages = await _thread_messages(token, channel, thread_ts)
    except SlackApiError as exc:
        logger.warning("Could not load Slack thread history (%s)", exc.error)
        return []
    for message in messages:
        if str(message.get("ts") or "") == current_ts:
            continue
        text_value = strip_bot_mention(str(message.get("text") or ""), bot_user)
        if not text_value or text_value == _THINKING:
            continue
        if message.get("bot_id") or message.get("user") == bot_user:
            turns.append(ChatMessage(role="assistant", content=text_value))
            continue
        user_id = str(message.get("user") or "")
        if user_id and user_id not in names:
            try:
                names[user_id] = _display_name(await cached_user(token, team_id, user_id))
            except SlackApiError:
                names[user_id] = user_id
        speaker = names.get(user_id) or "Someone"
        turns.append(ChatMessage(role="user", content=f"{speaker}: {text_value}"))
    return turns[-12:]


async def _slack_user(install: Mapping[str, Any], slack_user_id: str) -> dict[str, Any] | None:
    """Match a Slack user to a MindLoom user by email."""

    token = _token(install)
    try:
        info = await cached_user(token, str(install.get("team_id") or ""), slack_user_id)
    except SlackApiError:
        return None
    profile = info.get("profile") or {}
    email = str(profile.get("email") or "").strip().lower() if isinstance(profile, Mapping) else ""
    if not email:
        return None
    return await _user_by_email(str(install["org_id"]), email)


async def _post_or_update(
    token: str,
    *,
    channel: str,
    thread_ts: str,
    message_ts: str | None,
    text_value: str,
) -> str | None:
    if message_ts:
        await slack_api(token, "chat.update", {
            "channel": channel, "ts": message_ts, "text": text_value,
        })
        return message_ts
    posted = await slack_api(token, "chat.postMessage", {
        "channel": channel, "thread_ts": thread_ts, "text": text_value,
    })
    return str(posted.get("ts") or "") or None


async def answer_mention(install: Mapping[str, Any], event: Mapping[str, Any]) -> None:
    """Reply in the thread using the sender's MindLoom permissions."""

    token = _token(install)
    channel = str(event.get("channel") or "")
    current_ts = str(event.get("ts") or "")
    thread_ts = str(event.get("thread_ts") or current_ts)
    if not token or not channel or not thread_ts:
        return
    thinking_ts = await _post_or_update(
        token, channel=channel, thread_ts=thread_ts, message_ts=None, text_value=_THINKING,
    )
    org_id = str(install["org_id"])
    install_id = str(install["install_id"])
    try:
        question = strip_bot_mention(str(event.get("text") or ""), str(install.get("bot_user_id") or ""))
        if not question:
            await _post_or_update(
                token, channel=channel, thread_ts=thread_ts, message_ts=thinking_ts,
                text_value="Mention me with a question, for example: @Loombot what is the status of project tiramisu?",
            )
            return
        user = await _slack_user(install, str(event.get("user") or ""))
        policy = str(install.get("unlinked_user_policy") or "ignore")
        if user is None and policy != "org":
            await _post_or_update(
                token, channel=channel, thread_ts=thread_ts, message_ts=thinking_ts,
                text_value=(
                    "I can only answer people with a MindLoom account. "
                    "Ask an admin to invite you, using the same email as Slack."
                ),
            )
            return
        from brain.retrieval import retrieve
        from keeper.auth import get_user_access_tokens

        if user is None:
            access_tokens = [f"org:{org_id}"]
        else:
            access_tokens = await get_user_access_tokens(org_id, str(user["user_id"]))
        history = await _history(install, token, channel, thread_ts, current_ts)
        retrieval = await retrieve(question, history, org_id, access_tokens)
        if user is None:
            # Org-wide answers stay on the plain answer path. The Ask tool loop
            # would otherwise act as a real MindLoom user and see their data.
            from loombot.answerer import generate_answer

            response = await generate_answer(question, retrieval, history, org_id)
        else:
            from loombot.ask_agent import run_ask_agent

            response = await run_ask_agent(
                question=question,
                retrieval=retrieval,
                history=history,
                org_id=org_id,
                user_id=str(user["user_id"]),
            )
        rendered = await format_slack_answer(response.answer, response.sources, org_id)
        rendered = (rendered + _draft_note(response))[:3900]
        await _post_or_update(
            token, channel=channel, thread_ts=thread_ts, message_ts=thinking_ts, text_value=rendered,
        )
        await clear_error(install_id)
    except Exception as exc:  # noqa: BLE001 - the thread should get an error, not silence
        logger.exception("Slack answer failed")
        fatal = isinstance(exc, SlackApiError) and exc.error in _FATAL_AUTH_ERRORS
        await set_error(install_id, str(exc), fatal=fatal)
        if thinking_ts:
            try:
                await _post_or_update(
                    token, channel=channel, thread_ts=thread_ts, message_ts=thinking_ts,
                    text_value="I couldn't answer that just now. Try again in a moment.",
                )
            except SlackApiError:
                logger.warning("Could not update the Slack thinking message")


# --- Ingestion --------------------------------------------------------------


async def _member_emails(token: str, team_id: str, channel: str) -> list[str]:
    key = f"slack:members:{team_id}:{channel}"
    cached = await _cache_get(key)
    if cached:
        parsed = json.loads(cached)
        if isinstance(parsed, list) and parsed:
            return [str(item) for item in parsed]
    member_ids: list[str] = []
    cursor: str | None = None
    while len(member_ids) < 500:
        args: dict[str, Any] = {"channel": channel, "limit": 200}
        if cursor:
            args["cursor"] = cursor
        payload = await slack_api(token, "conversations.members", args)
        member_ids.extend(str(item) for item in (payload.get("members") or []))
        cursor = str((payload.get("response_metadata") or {}).get("next_cursor") or "")
        if not cursor:
            break
    emails: list[str] = []
    for user_id in member_ids:
        try:
            info = await cached_user(token, team_id, user_id)
        except SlackApiError:
            continue
        profile = info.get("profile") or {}
        email = str(profile.get("email") or "").strip().lower() if isinstance(profile, Mapping) else ""
        if email and email not in emails:
            emails.append(email)
    if emails:
        await _cache_set(key, json.dumps(emails), 600)
    return emails


async def _channel_name(token: str, team_id: str, channel: str) -> str:
    key = f"slack:channel:{team_id}:{channel}"
    cached = await _cache_get(key)
    if cached:
        return cached
    try:
        payload = await slack_api(token, "conversations.info", {"channel": channel})
        name = str((payload.get("channel") or {}).get("name") or channel)
    except SlackApiError:
        name = channel
    await _cache_set(key, name, 3600)
    return name


async def _permalink(token: str, channel: str, thread_ts: str) -> str | None:
    try:
        payload = await slack_api(token, "chat.getPermalink", {
            "channel": channel, "message_ts": thread_ts,
        })
    except SlackApiError:
        return None
    link = payload.get("permalink")
    return str(link) if link else None


async def ingest_thread(install: Mapping[str, Any], channel: str, thread_ts: str) -> bool:
    """Ingest one Slack thread. Returns False when there is nothing new to store."""

    token = _token(install)
    if not token:
        return False
    team_id = str(install.get("team_id") or "")
    org_id = str(install["org_id"])
    bot_user = str(install.get("bot_user_id") or "")
    messages = [
        message for message in await _thread_messages(token, channel, thread_ts)
        if not message.get("bot_id")
        and message.get("user") != bot_user
        and not message.get("subtype")
        and str(message.get("text") or "").strip()
    ]
    if not messages:
        return False
    emails = await _member_emails(token, team_id, channel)
    if not emails:
        raise SlackIngestSkipped(
            "Could not read channel member emails, so this thread was not ingested. "
            "Grant users:read.email and reinstall the Slack app."
        )
    names: dict[str, str] = {}
    for message in messages:
        user_id = str(message.get("user") or "")
        if user_id and user_id not in names:
            try:
                names[user_id] = _display_name(await cached_user(token, team_id, user_id))
            except SlackApiError:
                names[user_id] = user_id or "Someone"
    from brain.pipeline import DocumentInput
    from brain.source_registry import ingest_external_source
    from core.models import Conversation, IncomingMessage, Participant

    participants = [
        Participant(id=user_id or "unknown", name=name)
        for user_id, name in names.items()
    ]
    incoming = [
        IncomingMessage(
            id=str(message.get("ts") or ""),
            sender=str(message.get("user") or "unknown"),
            timestamp=_slack_time(str(message.get("ts") or thread_ts)),
            text=strip_bot_mention(str(message.get("text") or ""), bot_user),
        )
        for message in messages
    ]
    incoming = [message for message in incoming if message.text]
    if not incoming:
        return False
    incoming.sort(key=lambda message: message.timestamp)
    name = await _channel_name(token, team_id, channel)
    label = f"#{name}" if not name.startswith("#") else name
    latest = messages[-1].get("ts") or thread_ts
    external_id = f"{team_id}:{channel}:{thread_ts}"
    started = incoming[0].timestamp
    updated = incoming[-1].timestamp
    authors = list(dict.fromkeys(names.values()))
    conversation = Conversation(
        source=PROVIDER,
        conversation_id=external_id,
        title=label,
        participants=participants or [Participant(id="unknown", name="Someone")],
        messages=incoming,
    )
    document = DocumentInput(
        data=json.dumps(
            [{"user": message.sender, "ts": message.timestamp.isoformat(), "text": message.text} for message in incoming],
            ensure_ascii=False,
        ).encode("utf-8"),
        source=PROVIDER,
        source_label=label,
        original_filename="Slack thread",
        mime_type="application/json",
        visible_to=emails,
        title=f"{label} thread",
        author=authors[0] if authors else None,
        owners=authors[:8],
        source_created_at=started,
        source_updated_at=updated,
        source_application="Slack",
        source_location=label,
        folder_path=channel,
        version=str(latest),
        contributors=authors[:20],
        source_url=await _permalink(token, channel, thread_ts),
    )
    await ingest_external_source(
        org_id=org_id,
        provider=PROVIDER,
        external_id=external_id,
        version=str(latest),
        conversation=conversation,
        document=document,
    )
    return True


async def list_joined_channels(org_id: str) -> list[dict[str, Any]]:
    install = await fetch_install_for_org(org_id)
    if install is None or install.get("status") not in {"active", "error"}:
        raise HTTPException(status_code=409, detail="Connect Slack before listing channels.")
    token = _token(install)
    channels: list[dict[str, Any]] = []
    cursor: str | None = None
    while len(channels) < 400:
        args: dict[str, Any] = {
            "types": "public_channel,private_channel",
            "exclude_archived": True,
            "limit": 200,
        }
        if cursor:
            args["cursor"] = cursor
        try:
            payload = await slack_api(token, "conversations.list", args)
        except SlackApiError as exc:
            raise HTTPException(status_code=502, detail=f"Slack channel list failed ({exc.error}).") from exc
        for channel in payload.get("channels") or []:
            if not isinstance(channel, dict) or not channel.get("is_member"):
                continue
            channels.append({
                "id": str(channel.get("id") or ""),
                "name": str(channel.get("name") or ""),
                "is_private": bool(channel.get("is_private")),
            })
        cursor = str((payload.get("response_metadata") or {}).get("next_cursor") or "")
        if not cursor:
            break
    return channels


async def send_test_message(org_id: str, channel: str) -> dict[str, Any]:
    install = await fetch_install_for_org(org_id)
    if install is None or not _token(install):
        raise HTTPException(status_code=409, detail="Connect Slack before sending a test message.")
    try:
        await slack_api(_token(install), "chat.postMessage", {
            "channel": channel,
            "text": "Loombot is connected to MindLoom. Mention me in a thread to ask a question.",
        })
    except SlackApiError as exc:
        hint = " Invite @Loombot to the channel first." if exc.error == "not_in_channel" else ""
        raise HTTPException(status_code=400, detail=f"Slack said {exc.error}.{hint}") from exc
    return {"ok": True}


async def backfill_install(install_id: str, days: int) -> int:
    """Import recent threads from the channels an admin selected."""

    install = await fetch_install(install_id)
    if install is None or install.get("status") != "active":
        return 0
    token = _token(install)
    oldest = str(time.time() - max(1, days) * 86400)
    count = 0
    seen: set[str] = set()
    for channel in _channels(install):
        cursor: str | None = None
        while count < _MAX_BACKFILL_THREADS:
            args: dict[str, Any] = {"channel": channel, "oldest": oldest, "limit": 100}
            if cursor:
                args["cursor"] = cursor
            try:
                payload = await slack_api(token, "conversations.history", args)
            except SlackApiError as exc:
                await set_error(install_id, f"{channel}: {exc.error}")
                break
            for message in payload.get("messages") or []:
                if not isinstance(message, dict):
                    continue
                thread_ts = str(message.get("thread_ts") or message.get("ts") or "")
                if not thread_ts or thread_ts in seen:
                    continue
                seen.add(thread_ts)
                try:
                    if await ingest_thread(install, channel, thread_ts):
                        count += 1
                except SlackIngestSkipped as exc:
                    await set_error(install_id, str(exc))
                    return count
                if count >= _MAX_BACKFILL_THREADS:
                    break
            cursor = str((payload.get("response_metadata") or {}).get("next_cursor") or "")
            if not cursor:
                break
    return count


async def flush_dirty_threads() -> int:
    """Ingest threads that changed since the last pass. Safe for one worker."""

    count = 0
    try:
        async with _redis() as redis:
            while True:
                member = await redis.spop(_DIRTY_KEY)
                if member is None:
                    break
                parts = str(member).split("|", 2)
                if len(parts) != 3:
                    continue
                install_id, channel, thread_ts = parts
                install = await fetch_install(install_id)
                if install is None or install.get("status") != "active":
                    continue
                try:
                    if await ingest_thread(install, channel, thread_ts):
                        count += 1
                except SlackIngestSkipped as exc:
                    await set_error(install_id, str(exc))
                except SlackApiError as exc:
                    fatal = exc.error in _FATAL_AUTH_ERRORS
                    await set_error(install_id, str(exc), fatal=fatal)
                    if not fatal:
                        await redis.sadd(_DIRTY_KEY, member)
                    break
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Slack ingest failed for %s", member)
                    await set_error(install_id, str(exc))
                    await redis.sadd(_DIRTY_KEY, member)
                    break
    except Exception:  # noqa: BLE001
        logger.exception("Slack thread flush failed")
    return count


async def run_periodic_slack_flush() -> None:
    """Worker loop: ingest changed Slack threads about once a minute."""

    while True:
        await asyncio.sleep(60)
        try:
            ingested = await flush_dirty_threads()
            if ingested:
                logger.info("Ingested %s changed Slack thread(s)", ingested)
        except Exception:  # noqa: BLE001
            logger.exception("Slack thread flush failed")
