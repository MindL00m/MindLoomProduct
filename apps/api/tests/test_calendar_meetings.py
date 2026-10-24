"""Scheduling meetings on Google Calendar from Ask and workspaces."""

from __future__ import annotations

import json
from datetime import datetime, time, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi import HTTPException

from core.models import QueryResponse, RetrievalResult
from loombot import ask_agent
from shuttle import calendar_meetings

NY = ZoneInfo("America/New_York")
WRITE = "openid https://www.googleapis.com/auth/calendar.readonly https://www.googleapis.com/auth/calendar.events"


def _connect(monkeypatch, scopes: str = WRITE) -> None:
    async def fake_connection(org_id, user_id):
        return SimpleNamespace(scopes=scopes, access_token="real", account_email="me@acme.com")

    async def fake_token(org_id, user_id):
        return "cal-token", "me@acme.com"

    monkeypatch.setattr(calendar_meetings, "_connection", fake_connection)
    monkeypatch.setattr("weaver.google_workspace._workspace_access_token", fake_token)


def _mock_http(monkeypatch, handler) -> list[httpx.Request]:
    requests: list[httpx.Request] = []
    real_client = httpx.AsyncClient

    def recording(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    monkeypatch.setattr(
        calendar_meetings.httpx, "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(recording), **kwargs),
    )
    return requests


def test_free_slots_skip_busy_time_weekends_and_spread_across_days() -> None:
    monday = datetime(2026, 10, 26, tzinfo=NY)
    busy = [(
        datetime.combine(monday.date(), time(9, 0), tzinfo=NY).astimezone(timezone.utc),
        datetime.combine(monday.date(), time(11, 0), tzinfo=NY).astimezone(timezone.utc),
    )]
    slots = calendar_meetings.free_slots(
        busy,
        window_start=datetime.combine(monday.date(), time(0, 0), tzinfo=NY).astimezone(timezone.utc),
        window_end=(monday + timedelta(days=7)).astimezone(timezone.utc),
        duration=timedelta(minutes=30), tz=NY,
        earliest=time(9, 0), latest=time(17, 0), include_weekends=False, limit=6,
    )
    local = [s.astimezone(NY) for s, _ in slots]
    assert local[0].hour == 11 and local[0].minute == 0  # first free time after the busy block
    assert all(t.weekday() < 5 for t in local)
    assert max(sum(1 for t in local if t.date() == d.date()) for d in local) <= 2
    assert all(s.astimezone(NY).time() >= time(9) and e.astimezone(NY).time() <= time(17) for s, e in slots)


async def test_find_free_times_reports_unreadable_calendars(monkeypatch) -> None:
    _connect(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert [item["id"] for item in body["items"]] == ["primary", "priya@acme.com", "vendor@other.com"]
        return httpx.Response(200, json={"calendars": {
            "primary": {"busy": []},
            "priya@acme.com": {"busy": []},
            "vendor@other.com": {"errors": [{"reason": "notFound"}], "busy": []},
        }})

    requests = _mock_http(monkeypatch, handler)
    result = await calendar_meetings.find_free_times(
        "o", "u", attendee_emails=["Priya@Acme.com", "vendor@other.com", "me@acme.com", "not-an-email"],
        duration_minutes=45, user_timezone="America/New_York",
    )
    assert requests[0].headers["Authorization"] == "Bearer cal-token"
    assert result["suggestions"] and result["duration_minutes"] == 45
    assert result["availability_checked_for"] == ["me@acme.com", "priya@acme.com"]
    assert result["unknown_availability"][0]["email"] == "vendor@other.com"
    assert result["invalid_attendees"] == ["not-an-email"]


async def test_create_meeting_sends_invites_with_a_meet_link(monkeypatch) -> None:
    _connect(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "id": "evt1", "htmlLink": "https://calendar.google.com/event?eid=1",
            "hangoutLink": "https://meet.google.com/abc-defg-hij",
        })

    requests = _mock_http(monkeypatch, handler)
    start = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=1)
    result = await calendar_meetings.create_meeting(
        "o", "u", title="Launch sync", start=start, end=start + timedelta(minutes=30),
        attendees=["priya@acme.com", "me@acme.com"], description="Agenda: launch",
        user_timezone="America/New_York",
    )
    sent = json.loads(requests[0].content)
    assert requests[0].url.params["sendUpdates"] == "all"
    assert requests[0].url.params["conferenceDataVersion"] == "1"
    assert sent["attendees"] == [{"email": "priya@acme.com"}]  # the organizer is not re-invited
    assert sent["start"]["timeZone"] == "America/New_York"
    assert sent["conferenceData"]["createRequest"]["conferenceSolutionKey"]["type"] == "hangoutsMeet"
    assert result["meet_link"] == "https://meet.google.com/abc-defg-hij"
    assert result["status"] == "scheduled"


async def test_old_connections_are_asked_to_reconnect(monkeypatch) -> None:
    _connect(monkeypatch, scopes="openid https://www.googleapis.com/auth/calendar.readonly")
    status = await calendar_meetings.calendar_status("o", "u")
    assert status["connected"] is True and status["can_schedule"] is False
    assert "Reconnect" in status["reason"]
    start = datetime.now(timezone.utc) + timedelta(days=1)
    with pytest.raises(HTTPException) as raised:
        await calendar_meetings.create_meeting(
            "o", "u", title="x", start=start, end=start + timedelta(minutes=30), attendees=[],
        )
    assert "Reconnect" in raised.value.detail


async def test_create_meeting_rejects_bad_times(monkeypatch) -> None:
    _connect(monkeypatch)
    start = datetime.now(timezone.utc) + timedelta(days=1)
    with pytest.raises(HTTPException):
        await calendar_meetings.create_meeting("o", "u", title="x", start=start, end=start, attendees=[])
    with pytest.raises(HTTPException):
        past = datetime.now(timezone.utc) - timedelta(days=1)
        await calendar_meetings.create_meeting(
            "o", "u", title="x", start=past, end=past + timedelta(minutes=30), attendees=[],
        )


@pytest.mark.parametrize("question", [
    "schedule a meeting with Priya tomorrow at 3pm",
    "set up a 30 minute sync with the team next week",
    "find a time for me and Amey to meet",
    "book a call with acme on Friday",
    "send a calendar invite to alex for the demo",
])
def test_meeting_requests_are_detected(question: str) -> None:
    assert ask_agent._wants_meeting(question)


@pytest.mark.parametrize("question", [
    "what is our meeting policy?",
    "who attended the launch meeting?",
    "summarize yesterday's call notes",
    "Send Priya a note asking her to review the contract",
    "send the demo recording to Alex",
])
def test_meeting_questions_are_not_scheduling(question: str) -> None:
    assert not ask_agent._wants_meeting(question)


async def test_ask_drafts_a_meeting_card_without_scheduling(monkeypatch) -> None:
    async def base_answer(*args, **kwargs):
        return QueryResponse(answer="kb", sources=[], confidence="medium", routed=False)

    async def no_files(org_id, user_id):
        return []

    async def connected(*args):
        return True

    async def people(org_id, query, **kwargs):
        return [{"user_id": "u-priya", "name": "Priya Shah", "email": "priya@acme.com"}]

    async def status(org_id, user_id):
        return {"connected": True, "can_schedule": True, "account": "me@acme.com", "reason": None}

    created: list = []

    async def never_create(*args, **kwargs):
        created.append(kwargs)

    monkeypatch.setattr(ask_agent, "generate_answer", base_answer)
    monkeypatch.setattr(ask_agent, "_user_files", no_files)
    monkeypatch.setattr(ask_agent, "lookup_messageable_people", people)
    monkeypatch.setattr("weaver.integrations.has_google_workspace_connection", connected)
    monkeypatch.setattr(calendar_meetings, "calendar_status", status)
    monkeypatch.setattr(calendar_meetings, "create_meeting", never_create)

    def call(call_id, name, arguments):
        return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=json.dumps(arguments)))

    def script(step, kwargs):
        if step == 1:
            assert kwargs["tool_choice"] == "required"
            assert "Meeting rules" in kwargs["messages"][0]["content"]
            return SimpleNamespace(content=None, tool_calls=[call("c1", "lookup_person", {"query": "Priya"})])
        if step == 2:
            return SimpleNamespace(content=None, tool_calls=[call("c2", "propose_meeting", {
                "title": "Launch sync", "attendee_emails": ["priya@acme.com"],
                "start": "2026-10-27T15:00:00-04:00", "duration_minutes": 30,
                "alternatives": ["2026-10-28T10:00:00-04:00"],
            })])
        return SimpleNamespace(content="Drafted a 30-minute launch sync with Priya for Tue 3:00 PM.", tool_calls=None)

    sent: list = []

    async def create(**kwargs):
        sent.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=script(len(sent), kwargs))])

    client = SimpleNamespace(
        configuration=SimpleNamespace(supports_tools=True),
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
    )

    async def fake_client(org_id):
        return client

    monkeypatch.setattr(ask_agent, "get_llm_client", fake_client)
    response = await ask_agent.run_ask_agent(
        question="schedule a meeting with Priya tomorrow at 3pm about the launch",
        retrieval=RetrievalResult(chunks=[], experts=[], entities_found=[]),
        history=[], org_id="o", user_id="u", user_timezone="America/New_York",
    )
    meeting = response.proposed_meeting
    assert meeting is not None and meeting.title == "Launch sync"
    assert meeting.start == datetime(2026, 10, 27, 19, 0, tzinfo=timezone.utc)
    assert meeting.end - meeting.start == timedelta(minutes=30)
    assert [(a.email, a.name) for a in meeting.attendees] == [("priya@acme.com", "Priya Shah")]
    assert meeting.can_schedule is True and meeting.add_video_call is True
    assert len(meeting.alternatives) == 1
    # A meeting invite is the message: no email card is forced, and nothing is scheduled.
    assert response.proposed_email is None and created == []
    assert response.answer.startswith("Drafted a 30-minute launch sync")
