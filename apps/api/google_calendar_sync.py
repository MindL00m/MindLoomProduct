"""Google Calendar knowledge ingestion using Calendar incremental sync tokens."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import httpx
from fastapi import HTTPException

from google_workspace import (
    PROVIDER_GOOGLE_WORKSPACE,
    SyncCursorRow,
    _get_cursor,
    _upsert_cursor,
    _workspace_access_token,
)
from models import Conversation, IncomingMessage, Participant
from pipeline import DocumentInput
from provider_http import request_with_backoff
from source_registry import ingest_external_source, mark_external_source_deleted

SYNC_PROVIDER_GOOGLE_CALENDAR = "google_calendar_sync"


def _event_time(value: dict[str, Any] | None) -> datetime:
    raw = (value or {}).get("dateTime") or (value or {}).get("date")
    if not raw:
        return datetime.now(timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.now(timezone.utc)


def _event_text(event: dict[str, Any]) -> str:
    attendees = ", ".join(
        str(item.get("email")) for item in event.get("attendees", []) if item.get("email")
    )
    lines = [
        f"Event: {event.get('summary') or '(untitled)'}",
        f"Start: {(event.get('start') or {}).get('dateTime') or (event.get('start') or {}).get('date') or ''}",
        f"End: {(event.get('end') or {}).get('dateTime') or (event.get('end') or {}).get('date') or ''}",
    ]
    if event.get("location"):
        lines.append(f"Location: {event['location']}")
    if attendees:
        lines.append(f"Attendees: {attendees}")
    if event.get("description"):
        lines.append(f"Notes:\n{event['description']}")
    return "\n".join(lines)


async def sync_google_calendar(
    org_id: str, user_id: str, *, max_results: int = 100
) -> int:
    """Import selected calendars and retain the provider's nextSyncToken."""

    from connection_setup import get_policy, visibility_for_policy

    token, account = await _workspace_access_token(org_id, user_id)
    if token.startswith("dev:"):
        await _upsert_cursor(
            org_id=org_id, user_id=user_id, provider=SYNC_PROVIDER_GOOGLE_CALENDAR,
            account_email=account.lower(), cursor_value="dev", mark_synced=True,
        )
        return 0
    policy = await get_policy(org_id, user_id, PROVIDER_GOOGLE_WORKSPACE)
    if not policy or policy.status == "paused":
        return 0
    selected = {
        item.removeprefix("calendar:")
        for item in json.loads(policy.included_resources)
        if item.startswith("calendar:")
    }
    if not selected:
        return 0
    visible_to = visibility_for_policy(policy, org_id=org_id, source_account=account)
    ingested = 0
    async with httpx.AsyncClient(timeout=30) as client:
        for calendar_id in selected:
            cursor = await _get_cursor(
                org_id, user_id, f"{SYNC_PROVIDER_GOOGLE_CALENDAR}:{calendar_id}"
            )
            params: dict[str, str] = {
                "singleEvents": "true", "showDeleted": "true",
                "maxResults": str(max_results),
            }
            if cursor and cursor.cursor_value and cursor.cursor_value != "dev":
                params["syncToken"] = cursor.cursor_value
            elif policy.include_history and policy.history_start_date:
                params["timeMin"] = f"{policy.history_start_date}T00:00:00Z"
            elif not policy.include_history:
                params["timeMin"] = datetime.now(timezone.utc).isoformat()
            response = await request_with_backoff(
                client, "GET",
                f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events",
                headers={"Authorization": f"Bearer {token}"}, params=params,
            )
            if response.status_code == 410:
                params.pop("syncToken", None)
                response = await request_with_backoff(
                    client, "GET",
                    f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events",
                    headers={"Authorization": f"Bearer {token}"}, params=params,
                )
            if response.status_code >= 400:
                raise HTTPException(status_code=502, detail="Could not synchronize Google Calendar.")
            payload = response.json()
            pages = [payload]
            while payload.get("nextPageToken"):
                page_params = {**params, "pageToken": str(payload["nextPageToken"])}
                response = await request_with_backoff(
                    client, "GET",
                    f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events",
                    headers={"Authorization": f"Bearer {token}"}, params=page_params,
                )
                if response.status_code >= 400:
                    raise HTTPException(
                        status_code=502, detail="Could not paginate Google Calendar."
                    )
                payload = response.json()
                pages.append(payload)
            for event in [
                item for page in pages for item in page.get("items", [])
            ]:
                event_id = str(event.get("id") or "")
                if not event_id:
                    continue
                external_id = f"{calendar_id}:{event_id}"
                if event.get("status") == "cancelled":
                    await mark_external_source_deleted(
                        org_id, SYNC_PROVIDER_GOOGLE_CALENDAR, external_id
                    )
                    continue
                organizer = str((event.get("organizer") or {}).get("email") or account)
                text = _event_text(event)
                timestamp = _event_time(event.get("start"))
                conversation = Conversation(
                    source="google_calendar",
                    conversation_id=f"gcal:{external_id}",
                    title=str(event.get("summary") or "Calendar event"),
                    participants=[Participant(id=organizer, name=organizer)],
                    messages=[IncomingMessage(
                        id=event_id, sender=organizer, timestamp=timestamp, text=text
                    )],
                )
                raw = json.dumps(event, ensure_ascii=False).encode()
                await ingest_external_source(
                    org_id=org_id, provider=SYNC_PROVIDER_GOOGLE_CALENDAR,
                    external_id=external_id,
                    version=str(event.get("updated") or event.get("sequence") or ""),
                    conversation=conversation,
                    document=DocumentInput(
                        data=raw, source="google_calendar", source_label=conversation.title or "Event",
                        original_filename=f"{event_id}.json", mime_type="application/json",
                        visible_to=visible_to, title=conversation.title, author=organizer,
                        owners=[organizer], source_created_at=_event_time({"dateTime": event.get("created")}),
                        source_updated_at=_event_time({"dateTime": event.get("updated")}),
                        source_application="Google Calendar",
                        source_location=f"Calendar {calendar_id}", folder_path=f"calendars/{calendar_id}",
                        version=str(event.get("sequence") or event.get("updated") or ""),
                        contributors=[
                            str(item.get("email")) for item in event.get("attendees", [])
                            if item.get("email")
                        ],
                        permissions=visible_to, source_url=event.get("htmlLink"),
                    ),
                )
                ingested += 1
            next_token = pages[-1].get("nextSyncToken")
            if next_token:
                await _upsert_cursor(
                    org_id=org_id, user_id=user_id,
                    provider=f"{SYNC_PROVIDER_GOOGLE_CALENDAR}:{calendar_id}",
                    account_email=account.lower(), cursor_value=str(next_token),
                    mark_synced=True,
                )
    return ingested
