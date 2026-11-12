"""Live WhatsApp chats, forwarded by the user's own OpenClaw gateway.

OpenClaw links to WhatsApp as a companion device on the user's machine. The
MindLoom plugin for it (apps/openclaw-plugin) posts each inbound message here
with a per-user link token. Messages are buffered, and once a chat has been
quiet for a couple of minutes each changed chat-day is ingested as one document
through the same pipeline as a WhatsApp export. Everything a user links is
private to that user.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import secrets
from datetime import date, datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from core.database import get_session_factory

logger = logging.getLogger(__name__)

TOKEN_PREFIX = "mlwa_"
MAX_MESSAGES_PER_REQUEST = 100
MAX_TEXT_CHARS = 8000
# A chat is ingested once nothing new has arrived for this long, so a burst of
# messages becomes one ingestion instead of one per message.
QUIET_SECONDS = 120


class LiveMessage(BaseModel):
    """One inbound WhatsApp message as the OpenClaw plugin forwards it."""

    text: str = Field(default="", max_length=65_000)
    message_id: str | None = Field(default=None, max_length=200)
    chat_id: str | None = Field(default=None, max_length=200)
    chat_name: str | None = Field(default=None, max_length=300)
    is_group: bool | None = None
    sender_id: str | None = Field(default=None, max_length=200)
    sender_name: str | None = Field(default=None, max_length=300)
    timestamp: float | str | None = None


class LiveMessagesInput(BaseModel):
    messages: list[LiveMessage] = Field(min_length=1, max_length=MAX_MESSAGES_PER_REQUEST)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def parse_timestamp(value: float | str | None, fallback: datetime) -> datetime:
    """Epoch seconds, epoch milliseconds or ISO 8601, as an aware UTC datetime."""

    try:
        if isinstance(value, str) and not value.strip().replace(".", "", 1).isdigit():
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
        number = float(value)  # type: ignore[arg-type]
        if number > 1e11:  # milliseconds
            number /= 1000
        if number <= 0:
            return fallback
        return datetime.fromtimestamp(number, tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return fallback


_PHONE = re.compile(r"^\+?\d{6,15}$")


def normalize_whatsapp_id(value: str | None) -> str:
    """One stable form for a WhatsApp identity, whatever the country.

    A phone number becomes ``+<digits>`` (``919401264052@s.whatsapp.net``,
    ``whatsapp:+44 7700 900123`` and ``+1 (415) 555-2671`` all normalise). A group
    (``...@g.us``) or a contact WhatsApp identifies only by a private id
    (``...@lid``) keeps its JID. No country code is ever assumed or added.
    """

    raw = re.sub(r"^whatsapp:", "", (value or "").strip(), flags=re.I)
    if not raw:
        return ""
    if "@" in raw:
        local, _, domain = raw.partition("@")
        if domain.lower() not in ("s.whatsapp.net", "c.us"):
            return raw.lower()
        raw = local.split(":")[0]  # drop the device suffix (number:device@...)
    digits = re.sub(r"[\s().-]", "", raw)
    return "+" + digits.lstrip("+") if _PHONE.match(digits) else raw


def is_group_chat(chat_id: str) -> bool:
    return chat_id.endswith("@g.us")


def default_chat_name(chat_id: str) -> str:
    """What a chat is called until WhatsApp or the user gives it a name."""

    if is_group_chat(chat_id):
        return f"WhatsApp group {chat_id.split('@')[0][-4:]}"
    return chat_id


def normalize_message(message: LiveMessage, now: datetime) -> dict[str, Any] | None:
    """A storable row, or None for a message that is not chat text (media, broadcasts)."""

    body = (message.text or "").strip()[:MAX_TEXT_CHARS]
    if not body:
        return None
    sender_id = normalize_whatsapp_id(message.sender_id) or "unknown"
    # A direct chat has no separate conversation id: the chat is the sender.
    chat_id = normalize_whatsapp_id(message.chat_id) or sender_id
    if chat_id.endswith(("@broadcast", "@newsletter")):
        return None  # status updates and channels are not chats
    sender_name = (message.sender_name or "").strip() or sender_id
    is_group = is_group_chat(chat_id) or bool(message.is_group)
    sent_at = parse_timestamp(message.timestamp, now)
    message_id = (message.message_id or "").strip() or hashlib.sha256(
        f"{chat_id}|{sender_id}|{sent_at.isoformat()}|{body}".encode()
    ).hexdigest()[:32]
    return {
        "chat_id": chat_id,
        # A group is never named after whoever happened to speak in it.
        "chat_name": (message.chat_name or "").strip()
        or (default_chat_name(chat_id) if is_group else sender_name),
        "message_id": message_id,
        "sender_id": sender_id,
        "sender_name": sender_name,
        "sent_at": sent_at,
        "body": body,
    }


# --- Linking ------------------------------------------------------------------------


async def create_link(org_id: str, user_id: str) -> str:
    """Create (or rotate) this user's link token. The token is returned only here."""

    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    factory = get_session_factory()
    async with factory() as session, session.begin():
        await session.execute(text("""
            INSERT INTO whatsapp_live_links (link_id, org_id, user_id, token_hash, created_at)
            VALUES (:id, :org, :user, :hash, now())
            ON CONFLICT (org_id, user_id) DO UPDATE
              SET token_hash=excluded.token_hash, created_at=now(), last_seen_at=NULL
        """), {"id": str(uuid4()), "org": org_id, "user": user_id, "hash": _hash(token)})
    return token


async def link_status(org_id: str, user_id: str) -> dict[str, Any]:
    factory = get_session_factory()
    async with factory() as session:
        link = (await session.execute(text("""
            SELECT created_at, last_seen_at FROM whatsapp_live_links
            WHERE org_id=:org AND user_id=:user
        """), {"org": org_id, "user": user_id})).mappings().one_or_none()
        counts = (await session.execute(text("""
            SELECT count(*) AS messages, count(DISTINCT chat_id) AS chats,
                   count(*) FILTER (WHERE ingested_at IS NULL) AS pending,
                   max(sent_at) AS latest
            FROM whatsapp_live_messages WHERE org_id=:org AND user_id=:user
        """), {"org": org_id, "user": user_id})).mappings().one()
    return {
        "linked": link is not None,
        "linked_at": link["created_at"] if link else None,
        "last_seen_at": link["last_seen_at"] if link else None,
        "messages": int(counts["messages"] or 0),
        "chats": int(counts["chats"] or 0),
        "pending": int(counts["pending"] or 0),
        "latest_message_at": counts["latest"],
    }


async def remove_link(org_id: str, user_id: str) -> None:
    """Stop accepting messages and drop the buffer. Already ingested chats stay searchable."""

    factory = get_session_factory()
    async with factory() as session, session.begin():
        params = {"org": org_id, "user": user_id}
        await session.execute(text(
            "DELETE FROM whatsapp_live_links WHERE org_id=:org AND user_id=:user"
        ), params)
        await session.execute(text(
            "DELETE FROM whatsapp_live_messages WHERE org_id=:org AND user_id=:user"
        ), params)
        await session.execute(text(
            "DELETE FROM whatsapp_live_chats WHERE org_id=:org AND user_id=:user"
        ), params)


async def require_link(request: Request) -> tuple[str, str]:
    """Authenticate the OpenClaw plugin by its link token: (org_id, user_id)."""

    scheme, _, token = (request.headers.get("Authorization") or "").partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token.startswith(TOKEN_PREFIX):
        raise HTTPException(status_code=401, detail="Missing WhatsApp link token.")
    factory = get_session_factory()
    async with factory() as session, session.begin():
        row = (await session.execute(text("""
            UPDATE whatsapp_live_links l SET last_seen_at=now()
            FROM users u
            WHERE l.token_hash=:hash AND u.user_id=l.user_id AND u.org_id=l.org_id
            RETURNING l.org_id, l.user_id
        """), {"hash": _hash(token)})).mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=401, detail="This WhatsApp link token is not valid.")
    return str(row["org_id"]), str(row["user_id"])


# --- Receiving ----------------------------------------------------------------------


async def store_messages(org_id: str, user_id: str, messages: list[LiveMessage]) -> dict[str, int]:
    now = datetime.now(timezone.utc)
    rows = [row for row in (normalize_message(m, now) for m in messages) if row]
    stored = 0
    factory = get_session_factory()
    async with factory() as session, session.begin():
        for row in rows:
            inserted = (await session.execute(text("""
                INSERT INTO whatsapp_live_messages
                  (org_id, user_id, chat_id, message_id, chat_name, sender_id, sender_name,
                   sent_at, body, received_at)
                VALUES (:org, :user, :chat_id, :message_id, :chat_name, :sender_id,
                        :sender_name, :sent_at, :body, now())
                ON CONFLICT (org_id, user_id, chat_id, message_id) DO NOTHING
                RETURNING message_id
            """), {"org": org_id, "user": user_id, **row})).scalar_one_or_none()
            stored += 1 if inserted else 0
    return {
        "received": len(messages), "stored": stored,
        "duplicates": len(rows) - stored, "skipped": len(messages) - len(rows),
    }


# --- Chats ---------------------------------------------------------------------------


async def list_chats(org_id: str, user_id: str) -> list[dict[str, Any]]:
    """Every chat received so far, newest first, with the name MindLoom files it under."""

    factory = get_session_factory()
    async with factory() as session:
        rows = (await session.execute(text("""
            SELECT m.chat_id, count(*) AS messages, max(m.sent_at) AS latest_message_at,
                   count(DISTINCT m.sender_id) AS participants,
                   (array_agg(m.chat_name ORDER BY m.sent_at DESC))[1] AS received_name,
                   c.name AS custom_name
            FROM whatsapp_live_messages m
            LEFT JOIN whatsapp_live_chats c
              ON c.org_id=m.org_id AND c.user_id=m.user_id AND c.chat_id=m.chat_id
            WHERE m.org_id=:org AND m.user_id=:user
            GROUP BY m.chat_id, c.name
            ORDER BY max(m.sent_at) DESC
        """), {"org": org_id, "user": user_id})).mappings().all()
    return [
        {
            "chat_id": row["chat_id"],
            "name": row["custom_name"] or row["received_name"] or default_chat_name(row["chat_id"]),
            "named_by_you": bool(row["custom_name"]),
            "is_group": is_group_chat(row["chat_id"]),
            "messages": int(row["messages"]),
            "participants": int(row["participants"]),
            "latest_message_at": row["latest_message_at"],
        }
        for row in rows
    ]


async def rename_chat(org_id: str, user_id: str, chat_id: str, name: str) -> list[dict[str, Any]]:
    """Name a chat (WhatsApp does not tell OpenClaw plugins what a group is called).

    An empty name goes back to the received one. The chat's days are read in
    again so the documents already in MindLoom pick up the new name.
    """

    cleaned = " ".join((name or "").split())[:200]
    key = {"org": org_id, "user": user_id, "chat": chat_id}
    factory = get_session_factory()
    async with factory() as session, session.begin():
        known = (await session.execute(text("""
            SELECT 1 FROM whatsapp_live_messages
            WHERE org_id=:org AND user_id=:user AND chat_id=:chat LIMIT 1
        """), key)).scalar_one_or_none()
        if known is None:
            raise HTTPException(status_code=404, detail="That WhatsApp chat was not found.")
        if cleaned:
            await session.execute(text("""
                INSERT INTO whatsapp_live_chats (org_id, user_id, chat_id, name, updated_at)
                VALUES (:org, :user, :chat, :name, now())
                ON CONFLICT (org_id, user_id, chat_id)
                  DO UPDATE SET name=excluded.name, updated_at=now()
            """), {**key, "name": cleaned})
        else:
            await session.execute(text("""
                DELETE FROM whatsapp_live_chats WHERE org_id=:org AND user_id=:user AND chat_id=:chat
            """), key)
        await session.execute(text("""
            UPDATE whatsapp_live_messages SET ingested_at=NULL
            WHERE org_id=:org AND user_id=:user AND chat_id=:chat
        """), key)
    return await list_chats(org_id, user_id)


# --- Ingesting ----------------------------------------------------------------------


def render_chat_day(rows: list[dict[str, Any]]) -> bytes:
    """A chat-day in WhatsApp's own export layout, so it reads like an export."""

    lines = [
        f"{row['sent_at'].astimezone(timezone.utc):%d/%m/%Y, %H:%M} - "
        f"{row['sender_name']}: {row['body']}"
        for row in rows
    ]
    return ("\n".join(lines) + "\n").encode("utf-8")


async def ingest_chat_day(
    *, org_id: str, user_id: str, chat_id: str, day: date, rows: list[dict[str, Any]],
    chat_name: str | None = None,
) -> None:
    from brain.pipeline import DocumentInput
    from brain.source_registry import ingest_external_source
    from core.models import Conversation, IncomingMessage, Participant
    from keeper.visibility_acl import permissions_for_upload

    chat_name = chat_name or str(rows[-1]["chat_name"] or default_chat_name(chat_id))
    title = f"{chat_name} - WhatsApp, {day:%d %b %Y}"
    senders = {str(row["sender_id"]): str(row["sender_name"]) for row in rows}
    conversation = Conversation(
        source="whatsapp_export",
        conversation_id=f"whatsapp-live:{user_id}:{chat_id}:{day.isoformat()}",
        title=title,
        participants=[Participant(id=f"whatsapp:{sid}", name=name) for sid, name in senders.items()],
        messages=[
            IncomingMessage(
                id=f"wa:{row['message_id']}", sender=f"whatsapp:{row['sender_id']}",
                timestamp=row["sent_at"], text=row["body"],
            )
            for row in rows
        ],
    )
    # A linked WhatsApp account is personal: only the person who linked it sees it.
    visible_to = permissions_for_upload(org_id=org_id, uploaded_by=user_id)
    data = render_chat_day(rows)
    document = DocumentInput(
        data=data, source="whatsapp_export", source_label=title,
        original_filename=f"{title}.txt", mime_type="text/plain",
        visible_to=visible_to, uploaded_by=user_id, title=title,
        source_created_at=rows[0]["sent_at"], source_updated_at=rows[-1]["sent_at"],
        source_application="WhatsApp", source_location="WhatsApp (live, via OpenClaw)",
        contributors=list(senders.values()), permissions=visible_to,
    )
    await ingest_external_source(
        org_id=org_id, provider="whatsapp_live",
        # One source per chat-day: later messages that day replace it, not duplicate it.
        external_id=f"{user_id}:{chat_id}:{day.isoformat()}",
        version=rows[-1]["sent_at"].isoformat(),
        conversation=conversation, document=document,
    )


async def flush_quiet_chats(quiet_seconds: int = QUIET_SECONDS) -> int:
    """Ingest every chat-day with new messages that has gone quiet. Returns how many."""

    factory = get_session_factory()
    async with factory() as session:
        groups = (await session.execute(text("""
            SELECT org_id, user_id, chat_id, (sent_at AT TIME ZONE 'UTC')::date AS day
            FROM whatsapp_live_messages
            GROUP BY 1, 2, 3, 4
            HAVING bool_or(ingested_at IS NULL)
               AND max(received_at) < now() - make_interval(secs => :quiet)
            LIMIT 50
        """), {"quiet": quiet_seconds})).mappings().all()
    ingested = 0
    for group in groups:
        key = {
            "org": group["org_id"], "user": group["user_id"],
            "chat": group["chat_id"], "day": group["day"],
        }
        try:
            async with factory() as session:
                rows = [dict(row) for row in (await session.execute(text("""
                    SELECT message_id, chat_name, sender_id, sender_name, sent_at, body
                    FROM whatsapp_live_messages
                    WHERE org_id=:org AND user_id=:user AND chat_id=:chat
                      AND (sent_at AT TIME ZONE 'UTC')::date = :day
                    ORDER BY sent_at, message_id
                """), key)).mappings().all()]
                custom_name = (await session.execute(text("""
                    SELECT name FROM whatsapp_live_chats
                    WHERE org_id=:org AND user_id=:user AND chat_id=:chat
                """), {k: key[k] for k in ("org", "user", "chat")})).scalar_one_or_none()
            if not rows:
                continue
            await ingest_chat_day(
                org_id=str(key["org"]), user_id=str(key["user"]), chat_id=str(key["chat"]),
                day=key["day"], rows=rows, chat_name=custom_name,
            )
            async with factory() as session, session.begin():
                await session.execute(text("""
                    UPDATE whatsapp_live_messages SET ingested_at=now()
                    WHERE org_id=:org AND user_id=:user AND chat_id=:chat
                      AND message_id = ANY(:ids)
                """), {**{k: key[k] for k in ("org", "user", "chat")},
                       "ids": [row["message_id"] for row in rows]})
            ingested += 1
        except Exception:  # noqa: BLE001 - one bad chat must not block the rest
            logger.exception("WhatsApp live chat ingestion failed")
    return ingested


async def run_periodic_whatsapp_flush() -> None:
    """Worker loop: ingest quiet WhatsApp chats about once a minute."""

    while True:
        await asyncio.sleep(60)
        try:
            ingested = await flush_quiet_chats()
            if ingested:
                logger.info("Ingested %s WhatsApp chat-day(s)", ingested)
        except Exception:  # noqa: BLE001
            logger.exception("WhatsApp live flush failed")
