"""Schedule meetings on the user's Google Calendar.

Ask and workspace Loombot draft meetings (``ProposedMeeting``); nothing reaches
Google Calendar until the user approves the card, which calls
``create_meeting``. Free/busy lookups use the Calendar ``freeBusy`` API, which
works with the read-only scope; creating events needs the
``calendar.events`` scope, so connections made before scheduling existed must
reconnect Google Workspace once.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from fastapi import HTTPException

logger = logging.getLogger(__name__)

CALENDAR_API = "https://www.googleapis.com/calendar/v3"
CALENDAR_EVENTS_SCOPE = "https://www.googleapis.com/auth/calendar.events"
_WRITE_SCOPES = {CALENDAR_EVENTS_SCOPE, "https://www.googleapis.com/auth/calendar"}
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_MAX_ATTENDEES = 50
_SLOT_STEP = timedelta(minutes=30)
_MAX_WINDOW_DAYS = 21

RECONNECT_HINT = (
    "MindLoom can read your calendar but is not yet allowed to add events. Reconnect "
    "Google Workspace in Import (Apps → Google Workspace → Reconnect) to allow scheduling."
)


class CalendarUnavailable(Exception):
    """The user's calendar cannot be used for this request; the message says why."""


def zone(name: str | None) -> Any:
    try:
        return ZoneInfo(name) if name else timezone.utc
    except (ZoneInfoNotFoundError, ValueError):
        return timezone.utc


async def _connection(org_id: str, user_id: str) -> Any:
    from weaver.google_workspace import PROVIDER_GOOGLE_WORKSPACE
    from weaver.integrations import _get_connection

    return await _get_connection(org_id, user_id, PROVIDER_GOOGLE_WORKSPACE)


def _can_write(row: Any) -> bool:
    scopes = str(getattr(row, "scopes", "") or "")
    return scopes == "dev" or bool(set(scopes.split()) & _WRITE_SCOPES)


async def calendar_status(org_id: str, user_id: str) -> dict[str, Any]:
    """Whether this user can schedule meetings, and if not, why."""

    row = await _connection(org_id, user_id)
    if row is None:
        return {
            "connected": False, "can_schedule": False, "account": None,
            "reason": "Connect Google Workspace in Import to schedule meetings on Google Calendar.",
        }
    if str(row.access_token or "").startswith("dev:"):
        return {
            "connected": True, "can_schedule": False, "account": row.account_email,
            "reason": "Google Workspace is connected in development mode, so there is no real calendar.",
        }
    if not _can_write(row):
        return {
            "connected": True, "can_schedule": False, "account": row.account_email,
            "reason": RECONNECT_HINT,
        }
    return {"connected": True, "can_schedule": True, "account": row.account_email, "reason": None}


async def _token(org_id: str, user_id: str, *, write: bool) -> tuple[str, str]:
    from weaver.google_workspace import _workspace_access_token

    row = await _connection(org_id, user_id)
    if row is None:
        raise CalendarUnavailable(
            "Google Workspace is not connected. Connect it in Import to use Google Calendar."
        )
    if write and not _can_write(row):
        raise CalendarUnavailable(RECONNECT_HINT)
    token, account = await _workspace_access_token(org_id, user_id)
    if token.startswith("dev:"):
        raise CalendarUnavailable(
            "Google Workspace is connected in development mode, so there is no real calendar."
        )
    return token, account


def clean_attendees(emails: list[str] | None) -> tuple[list[str], list[str]]:
    """(valid unique lower-cased addresses, rejected inputs)."""

    valid: list[str] = []
    rejected: list[str] = []
    for raw in emails or []:
        value = str(raw or "").strip().strip("<>").lower()
        if not value:
            continue
        if not _EMAIL.match(value):
            rejected.append(str(raw))
        elif value not in valid:
            valid.append(value)
    return valid[:_MAX_ATTENDEES], rejected


def _parse_time_of_day(value: str | None, default: time) -> time:
    match = re.match(r"^\s*(\d{1,2})(?::(\d{2}))?\s*$", str(value or ""))
    if not match:
        return default
    hour, minute = int(match.group(1)), int(match.group(2) or 0)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return default
    return time(hour, minute)


def _parse_day(value: str | None) -> date | None:
    try:
        return date.fromisoformat(str(value or "").strip()[:10]) if value else None
    except ValueError:
        return None


def _busy_intervals(payload: dict[str, Any], account: str, attendees: list[str]) -> tuple[
    list[tuple[datetime, datetime]], list[str], list[dict[str, str]]
]:
    """Merged busy intervals, attendees whose calendars were checked, and the rest."""

    busy: list[tuple[datetime, datetime]] = []
    checked: list[str] = []
    unknown: list[dict[str, str]] = []
    calendars = payload.get("calendars") or {}
    for calendar_id, info in calendars.items():
        label = account if calendar_id == "primary" else calendar_id
        errors = info.get("errors") or []
        if errors:
            reason = str(errors[0].get("reason") or "unavailable")
            unknown.append({
                "email": label,
                "reason": (
                    "Their calendar is not shared with you (outside your organization or private)."
                    if reason in {"notFound", "groupTooBig", "tooManyCalendarsRequested"}
                    else f"Google Calendar could not share their availability ({reason})."
                ),
            })
            continue
        if calendar_id != "primary":
            checked.append(calendar_id)
        for item in info.get("busy") or []:
            try:
                start = datetime.fromisoformat(str(item["start"]).replace("Z", "+00:00"))
                end = datetime.fromisoformat(str(item["end"]).replace("Z", "+00:00"))
            except (KeyError, ValueError):
                continue
            busy.append((start, end))
    for email in attendees:
        if email not in calendars and email not in checked and not any(u["email"] == email for u in unknown):
            unknown.append({"email": email, "reason": "Google Calendar did not return their availability."})
    busy.sort()
    merged: list[tuple[datetime, datetime]] = []
    for start, end in busy:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged, checked, unknown


def slot_label(start: datetime, end: datetime, tz: Any) -> str:
    local_start, local_end = start.astimezone(tz), end.astimezone(tz)
    return (
        f"{local_start.strftime('%a %d %b')}, {local_start.strftime('%H:%M')}–"
        f"{local_end.strftime('%H:%M')} {local_start.strftime('%Z')}".strip()
    )


def free_slots(
    busy: list[tuple[datetime, datetime]],
    *,
    window_start: datetime,
    window_end: datetime,
    duration: timedelta,
    tz: Any,
    earliest: time,
    latest: time,
    include_weekends: bool,
    limit: int,
    per_day: int = 2,
) -> list[tuple[datetime, datetime]]:
    """Up to ``limit`` free slots inside working hours, at most ``per_day`` per day."""

    slots: list[tuple[datetime, datetime]] = []
    day = window_start.astimezone(tz).date()
    last_day = window_end.astimezone(tz).date()
    while day <= last_day and len(slots) < limit:
        if include_weekends or day.weekday() < 5:
            taken = 0
            cursor = datetime.combine(day, earliest, tzinfo=tz)
            day_end = datetime.combine(day, latest, tzinfo=tz)
            while cursor + duration <= day_end and taken < per_day and len(slots) < limit:
                start, end = cursor.astimezone(timezone.utc), (cursor + duration).astimezone(timezone.utc)
                if start >= window_start and end <= window_end and not any(
                    b_start < end and start < b_end for b_start, b_end in busy
                ):
                    slots.append((start, end))
                    taken += 1
                    cursor += max(duration, _SLOT_STEP) + timedelta(hours=1)
                    continue
                cursor += _SLOT_STEP
        day += timedelta(days=1)
    return slots


async def find_free_times(
    org_id: str,
    user_id: str,
    *,
    attendee_emails: list[str],
    duration_minutes: int = 30,
    start_date: str | None = None,
    end_date: str | None = None,
    earliest_time: str | None = None,
    latest_time: str | None = None,
    include_weekends: bool = False,
    user_timezone: str | None = None,
    max_suggestions: int = 5,
) -> dict[str, Any]:
    """Suggest meeting times when the user and attendees are all free."""

    attendees, rejected = clean_attendees(attendee_emails)
    duration = timedelta(minutes=max(5, min(int(duration_minutes or 30), 8 * 60)))
    tz = zone(user_timezone)
    now = datetime.now(timezone.utc)
    first = _parse_day(start_date)
    last = _parse_day(end_date)
    window_start = max(
        now + timedelta(minutes=15),
        datetime.combine(first, time.min, tzinfo=tz).astimezone(timezone.utc) if first else now,
    )
    # Round up to the next half hour so suggestions look like real meeting times.
    window_start = window_start.replace(second=0, microsecond=0)
    if window_start.minute % 30:
        window_start += timedelta(minutes=30 - window_start.minute % 30)
    window_end = (
        datetime.combine(last + timedelta(days=1), time.min, tzinfo=tz).astimezone(timezone.utc)
        if last else window_start + timedelta(days=7)
    )
    window_end = min(window_end, window_start + timedelta(days=_MAX_WINDOW_DAYS))
    if window_end <= window_start:
        return {"error": "The date range is in the past or empty."}

    try:
        token, account = await _token(org_id, user_id, write=False)
    except CalendarUnavailable as exc:
        return {"error": str(exc)}
    body = {
        "timeMin": window_start.isoformat(),
        "timeMax": window_end.isoformat(),
        "timeZone": str(getattr(tz, "key", "UTC")),
        "items": [{"id": "primary"}, *({"id": email} for email in attendees if email != account.lower())],
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{CALENDAR_API}/freeBusy", json=body, headers={"Authorization": f"Bearer {token}"}
            )
    except httpx.HTTPError as exc:
        return {"error": f"Google Calendar could not be reached ({exc.__class__.__name__})."}
    if response.status_code in (401, 403):
        return {"error": "Google Calendar refused the request. Reconnect Google Workspace in Import."}
    if response.status_code >= 400:
        logger.warning("freeBusy failed %s: %s", response.status_code, response.text[:300])
        return {"error": f"Google Calendar returned {response.status_code}."}

    others = [email for email in attendees if email != account.lower()]
    busy, checked, unknown = _busy_intervals(response.json(), account, others)
    slots = free_slots(
        busy,
        window_start=window_start,
        window_end=window_end,
        duration=duration,
        tz=tz,
        earliest=_parse_time_of_day(earliest_time, time(9, 0)),
        latest=_parse_time_of_day(latest_time, time(17, 0)),
        include_weekends=include_weekends,
        limit=max(1, min(int(max_suggestions or 5), 10)),
    )
    return {
        "organizer": account,
        "timezone": str(getattr(tz, "key", "UTC")),
        "duration_minutes": int(duration.total_seconds() // 60),
        "window": {"start": window_start.isoformat(), "end": window_end.isoformat()},
        "suggestions": [
            {"start": start.isoformat(), "end": end.isoformat(), "label": slot_label(start, end, tz)}
            for start, end in slots
        ],
        "availability_checked_for": [account, *checked],
        "unknown_availability": unknown,
        "invalid_attendees": rejected,
        "note": (
            "Suggestions avoid busy times for everyone whose calendar was readable. "
            "People listed under unknown_availability may still be busy."
        ) if slots else "No common free time was found in that window; try a wider range or shorter meeting.",
    }


def _event_time(value: datetime, tz: Any) -> dict[str, str]:
    return {"dateTime": value.astimezone(tz).isoformat(), "timeZone": str(getattr(tz, "key", "UTC"))}


async def create_meeting(
    org_id: str,
    user_id: str,
    *,
    title: str,
    start: datetime,
    end: datetime,
    attendees: list[str],
    description: str = "",
    location: str = "",
    add_video_call: bool = True,
    user_timezone: str | None = None,
) -> dict[str, Any]:
    """Create the event on the user's primary calendar and email invitations."""

    cleaned_title = " ".join(str(title or "").split())[:300]
    if not cleaned_title:
        raise HTTPException(status_code=400, detail="Give the meeting a title.")
    if start.tzinfo is None or end.tzinfo is None:
        raise HTTPException(status_code=400, detail="Start and end need a timezone.")
    if end <= start:
        raise HTTPException(status_code=400, detail="The meeting must end after it starts.")
    if end - start > timedelta(hours=12):
        raise HTTPException(status_code=400, detail="Meetings can be at most 12 hours long.")
    if end < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="That time has already passed.")
    valid, rejected = clean_attendees(attendees)
    if rejected:
        raise HTTPException(status_code=400, detail=f"Not a valid email address: {', '.join(rejected)}.")
    try:
        token, account = await _token(org_id, user_id, write=True)
    except CalendarUnavailable as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    tz = zone(user_timezone)
    event: dict[str, Any] = {
        "summary": cleaned_title,
        "description": str(description or "").strip()[:8000],
        "start": _event_time(start, tz),
        "end": _event_time(end, tz),
        "attendees": [{"email": email} for email in valid if email != account.lower()],
        "reminders": {"useDefault": True},
    }
    if str(location or "").strip():
        event["location"] = str(location).strip()[:500]
    params = {"sendUpdates": "all"}
    if add_video_call:
        params["conferenceDataVersion"] = "1"
        event["conferenceData"] = {
            "createRequest": {
                "requestId": uuid4().hex,
                "conferenceSolutionKey": {"type": "hangoutsMeet"},
            }
        }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{CALENDAR_API}/calendars/primary/events",
                params=params,
                json=event,
                headers={"Authorization": f"Bearer {token}"},
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail=f"Google Calendar could not be reached ({exc.__class__.__name__})."
        ) from exc
    if response.status_code == 403 and "insufficient" in response.text.lower():
        raise HTTPException(status_code=400, detail=RECONNECT_HINT)
    if response.status_code in (401, 403):
        raise HTTPException(
            status_code=400, detail="Google Calendar refused the request. Reconnect Google Workspace in Import."
        )
    if response.status_code >= 400:
        logger.warning("Calendar insert failed %s: %s", response.status_code, response.text[:300])
        raise HTTPException(status_code=502, detail=f"Google Calendar returned {response.status_code}.")
    created = response.json()
    meet_link = created.get("hangoutLink") or next(
        (
            entry.get("uri")
            for entry in (created.get("conferenceData") or {}).get("entryPoints") or []
            if entry.get("entryPointType") == "video"
        ),
        None,
    )
    return {
        "status": "scheduled",
        "event_id": created.get("id"),
        "html_link": created.get("htmlLink"),
        "meet_link": meet_link,
        "organizer": account,
        "title": cleaned_title,
        "start": start.astimezone(timezone.utc).isoformat(),
        "end": end.astimezone(timezone.utc).isoformat(),
        "attendees": [item["email"] for item in event["attendees"]],
        "label": slot_label(start, end, tz),
    }
