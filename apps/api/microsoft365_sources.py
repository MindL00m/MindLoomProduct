"""Outlook mail/calendar and Teams private-chat ingestion for Microsoft 365."""

from __future__ import annotations

import html
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from fastapi import HTTPException

from google_workspace import _get_cursor, _upsert_cursor
from microsoft_teams import (
    PROVIDER_MICROSOFT_TEAMS,
    _graph_get,
    _parse_graph_datetime,
    _teams_token,
)
from models import Conversation, IncomingMessage, Participant
from pipeline import DocumentInput
from provider_http import request_with_backoff
from source_registry import ingest_external_source, mark_external_source_deleted

SYNC_PROVIDER_OUTLOOK_MAIL = "outlook_mail"
SYNC_PROVIDER_OUTLOOK_CALENDAR = "outlook_calendar"
SYNC_PROVIDER_TEAMS_CHAT = "teams_chat"


def _plain_html(value: str) -> str:
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    return html.unescape(re.sub(r"<[^>]+>", " ", value)).strip()


def _email(address: dict[str, Any] | None) -> str:
    return str(((address or {}).get("emailAddress") or {}).get("address") or "").lower()


async def _delta(
    client: httpx.AsyncClient, token: str, url: str, *, limit: int
) -> tuple[list[dict[str, Any]], str | None]:
    items: list[dict[str, Any]] = []
    next_url: str | None = url
    delta_url: str | None = None
    while next_url and len(items) < limit:
        response = await request_with_backoff(
            client, "GET", next_url,
            headers={"Authorization": f"Bearer {token}", "Prefer": 'odata.maxpagesize=50'},
        )
        if response.status_code >= 400:
            raise HTTPException(status_code=502, detail="Microsoft incremental sync failed.")
        payload = response.json()
        items.extend(payload.get("value", []))
        next_url = payload.get("@odata.nextLink")
        delta_url = payload.get("@odata.deltaLink") or delta_url
    return items[:limit], delta_url or next_url


async def sync_outlook_mail(org_id: str, user_id: str, *, max_results: int = 100) -> int:
    from connection_setup import get_policy, visibility_for_policy

    token, account = await _teams_token(org_id, user_id)
    if token.startswith("dev:"):
        return 0
    policy = await get_policy(org_id, user_id, PROVIDER_MICROSOFT_TEAMS)
    selected = set(json.loads(policy.included_resources)) if policy else set()
    if not policy or policy.status == "paused" or "mailbox:outlook" not in selected:
        return 0
    cursor = await _get_cursor(org_id, user_id, SYNC_PROVIDER_OUTLOOK_MAIL)
    url = cursor.cursor_value if cursor and cursor.cursor_value.startswith("http") else (
        "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages/delta"
        "?$select=id,conversationId,subject,body,bodyPreview,from,toRecipients,ccRecipients,"
        "createdDateTime,lastModifiedDateTime,receivedDateTime,webLink,isDraft"
    )
    visible_to = visibility_for_policy(policy, org_id=org_id, source_account=account)
    ingested = 0
    async with httpx.AsyncClient(timeout=30) as client:
        messages, next_cursor = await _delta(client, token, url, limit=max_results)
        for message in messages:
            message_id = str(message.get("id") or "")
            if not message_id:
                continue
            if "@removed" in message:
                await mark_external_source_deleted(org_id, SYNC_PROVIDER_OUTLOOK_MAIL, message_id)
                continue
            if message.get("isDraft"):
                continue
            created = _parse_graph_datetime(
                message.get("receivedDateTime") or message.get("createdDateTime")
            ) or datetime.now(timezone.utc)
            if policy.history_start_date and created.date().isoformat() < policy.history_start_date:
                continue
            sender = _email(message.get("from")) or account.lower()
            recipients = [
                _email(item) for item in
                [*(message.get("toRecipients") or []), *(message.get("ccRecipients") or [])]
                if _email(item)
            ]
            body = _plain_html(str((message.get("body") or {}).get("content") or ""))
            text = f"Subject: {message.get('subject') or '(no subject)'}\nFrom: {sender}\nTo: {', '.join(recipients)}\n\n{body or message.get('bodyPreview') or ''}"
            conversation = Conversation(
                source="outlook_email",
                conversation_id=f"outlook:{message.get('conversationId') or message_id}",
                title=str(message.get("subject") or "Outlook email"),
                participants=[Participant(id=sender, name=sender)],
                messages=[IncomingMessage(id=message_id, sender=sender, timestamp=created, text=text)],
            )
            await ingest_external_source(
                org_id=org_id, provider=SYNC_PROVIDER_OUTLOOK_MAIL,
                external_id=message_id,
                version=str(message.get("lastModifiedDateTime") or created.isoformat()),
                conversation=conversation,
                document=DocumentInput(
                    data=json.dumps(message, ensure_ascii=False).encode(), source="outlook_email",
                    source_label=conversation.title or "Outlook email",
                    original_filename=f"{message_id}.json", mime_type="application/json",
                    visible_to=visible_to, title=conversation.title, author=sender,
                    owners=[account.lower()], source_created_at=created,
                    source_updated_at=_parse_graph_datetime(message.get("lastModifiedDateTime")),
                    source_application="Microsoft Outlook", source_location="Outlook Inbox",
                    folder_path="mailFolders/inbox",
                    version=str(message.get("lastModifiedDateTime") or created.isoformat()),
                    contributors=[sender, *recipients], permissions=visible_to,
                    source_url=message.get("webLink"),
                ),
            )
            ingested += 1
        if next_cursor:
            await _upsert_cursor(
                org_id=org_id, user_id=user_id, provider=SYNC_PROVIDER_OUTLOOK_MAIL,
                account_email=account.lower(), cursor_value=next_cursor, mark_synced=True,
            )
    return ingested


async def sync_outlook_calendar(org_id: str, user_id: str, *, max_results: int = 100) -> int:
    from connection_setup import get_policy, visibility_for_policy

    token, account = await _teams_token(org_id, user_id)
    if token.startswith("dev:"):
        return 0
    policy = await get_policy(org_id, user_id, PROVIDER_MICROSOFT_TEAMS)
    selected = set(json.loads(policy.included_resources)) if policy else set()
    if not policy or policy.status == "paused" or "calendar:outlook" not in selected:
        return 0
    cursor = await _get_cursor(org_id, user_id, SYNC_PROVIDER_OUTLOOK_CALENDAR)
    start = (
        f"{policy.history_start_date}T00:00:00Z" if policy.include_history and policy.history_start_date
        else (datetime.now(timezone.utc) - (timedelta(days=365) if policy.include_history else timedelta())).isoformat()
    )
    end = (datetime.now(timezone.utc) + timedelta(days=730)).isoformat()
    url = cursor.cursor_value if cursor and cursor.cursor_value.startswith("http") else (
        "https://graph.microsoft.com/v1.0/me/calendarView/delta"
        f"?startDateTime={start}&endDateTime={end}"
    )
    visible_to = visibility_for_policy(policy, org_id=org_id, source_account=account)
    ingested = 0
    async with httpx.AsyncClient(timeout=30) as client:
        events, next_cursor = await _delta(client, token, url, limit=max_results)
        for event in events:
            event_id = str(event.get("id") or "")
            if not event_id:
                continue
            if "@removed" in event or event.get("isCancelled"):
                await mark_external_source_deleted(
                    org_id, SYNC_PROVIDER_OUTLOOK_CALENDAR, event_id
                )
                continue
            organizer = _email(event.get("organizer")) or account.lower()
            attendees = [_email(item) for item in event.get("attendees", []) if _email(item)]
            start_at = _parse_graph_datetime((event.get("start") or {}).get("dateTime"))
            text = (
                f"Event: {event.get('subject') or '(untitled)'}\n"
                f"Start: {(event.get('start') or {}).get('dateTime') or ''}\n"
                f"End: {(event.get('end') or {}).get('dateTime') or ''}\n"
                f"Location: {((event.get('location') or {}).get('displayName') or '')}\n"
                f"Attendees: {', '.join(attendees)}\n\n"
                f"{_plain_html(str((event.get('body') or {}).get('content') or '')) or event.get('bodyPreview') or ''}"
            )
            conversation = Conversation(
                source="outlook_calendar", conversation_id=f"outlook-event:{event_id}",
                title=str(event.get("subject") or "Outlook event"),
                participants=[Participant(id=organizer, name=organizer)],
                messages=[IncomingMessage(
                    id=event_id, sender=organizer,
                    timestamp=start_at or datetime.now(timezone.utc), text=text,
                )],
            )
            await ingest_external_source(
                org_id=org_id, provider=SYNC_PROVIDER_OUTLOOK_CALENDAR,
                external_id=event_id,
                version=str(event.get("lastModifiedDateTime") or ""),
                conversation=conversation,
                document=DocumentInput(
                    data=json.dumps(event, ensure_ascii=False).encode(), source="outlook_calendar",
                    source_label=conversation.title or "Outlook event",
                    original_filename=f"{event_id}.json", mime_type="application/json",
                    visible_to=visible_to, title=conversation.title, author=organizer,
                    owners=[organizer],
                    source_created_at=_parse_graph_datetime(event.get("createdDateTime")),
                    source_updated_at=_parse_graph_datetime(event.get("lastModifiedDateTime")),
                    source_application="Microsoft Outlook Calendar",
                    source_location="Outlook Calendar", folder_path="calendar",
                    version=str(event.get("lastModifiedDateTime") or ""),
                    contributors=[organizer, *attendees], permissions=visible_to,
                    source_url=event.get("webLink"),
                ),
            )
            ingested += 1
        if next_cursor:
            await _upsert_cursor(
                org_id=org_id, user_id=user_id, provider=SYNC_PROVIDER_OUTLOOK_CALENDAR,
                account_email=account.lower(), cursor_value=next_cursor, mark_synced=True,
            )
    return ingested


async def sync_teams_chats(org_id: str, user_id: str, *, max_results: int = 100) -> int:
    from connection_setup import get_policy, visibility_for_policy

    token, account = await _teams_token(org_id, user_id)
    if token.startswith("dev:"):
        return 0
    policy = await get_policy(org_id, user_id, PROVIDER_MICROSOFT_TEAMS)
    selected = set(json.loads(policy.included_resources)) if policy else set()
    if not policy or policy.status == "paused":
        return 0
    visible_to = visibility_for_policy(policy, org_id=org_id, source_account=account)
    ingested = 0
    async with httpx.AsyncClient(timeout=30) as client:
        chats = (await _graph_get(client, token, "/me/chats", {"$select": "id,topic,chatType,webUrl"})).get("value", [])
        for chat in chats:
            chat_id = str(chat.get("id") or "")
            if not chat_id or (
                "chat:all" not in selected and f"chat:{chat_id}" not in selected
            ):
                continue
            members = (await _graph_get(
                client, token, f"/chats/{chat_id}/members",
                {"$select": "id,displayName,email,userId"},
            )).get("value", [])
            member_emails = [
                str(item.get("email") or "").lower() for item in members if item.get("email")
            ]
            acl = member_emails if policy.access_mode == "respect_source_permissions" else visible_to
            messages = (await _graph_get(
                client, token, f"/chats/{chat_id}/messages",
                {"$top": str(min(max_results - ingested, 50))},
            )).get("value", [])
            for message in messages:
                if ingested >= max_results:
                    break
                message_id = str(message.get("id") or "")
                if not message_id or message.get("deletedDateTime"):
                    if message_id:
                        await mark_external_source_deleted(
                            org_id, SYNC_PROVIDER_TEAMS_CHAT, f"{chat_id}:{message_id}"
                        )
                    continue
                sender = str((((message.get("from") or {}).get("user") or {}).get("displayName")) or "Unknown")
                created = _parse_graph_datetime(message.get("createdDateTime")) or datetime.now(timezone.utc)
                text = _plain_html(str((message.get("body") or {}).get("content") or ""))
                if not text:
                    continue
                conversation = Conversation(
                    source="microsoft_teams_chat", conversation_id=f"teams-chat:{chat_id}",
                    title=str(chat.get("topic") or f"{chat.get('chatType', 'Private')} Teams chat"),
                    participants=[Participant(id=sender, name=sender)],
                    messages=[IncomingMessage(id=message_id, sender=sender, timestamp=created, text=text)],
                )
                await ingest_external_source(
                    org_id=org_id, provider=SYNC_PROVIDER_TEAMS_CHAT,
                    external_id=f"{chat_id}:{message_id}",
                    version=str(message.get("lastModifiedDateTime") or created.isoformat()),
                    conversation=conversation,
                    document=DocumentInput(
                        data=json.dumps(message, ensure_ascii=False).encode(),
                        source="microsoft_teams_chat", source_label=conversation.title or "Teams chat",
                        original_filename=f"{message_id}.json", mime_type="application/json",
                        visible_to=acl or [account.lower()], title=conversation.title,
                        author=sender, owners=member_emails,
                        source_created_at=created,
                        source_updated_at=_parse_graph_datetime(message.get("lastModifiedDateTime")),
                        source_application="Microsoft Teams",
                        source_location=f"Private chat {chat_id}", folder_path=f"chats/{chat_id}",
                        version=str(message.get("lastModifiedDateTime") or created.isoformat()),
                        contributors=[str(item.get("displayName") or item.get("email") or "") for item in members],
                        permissions=acl or [account.lower()],
                        source_url=chat.get("webUrl"),
                    ),
                )
                ingested += 1
    await _upsert_cursor(
        org_id=org_id, user_id=user_id, provider=SYNC_PROVIDER_TEAMS_CHAT,
        account_email=account.lower(), cursor_value=datetime.now(timezone.utc).isoformat(),
        mark_synced=True,
    )
    return ingested
