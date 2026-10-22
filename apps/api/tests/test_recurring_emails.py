"""Recurring email workflows: schedule maths, intent, and Ask drafting."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from core.models import QueryResponse, RetrievalResult
from loombot import ask_agent
from shuttle import recurring_emails as rec

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)


def test_intervals_are_bounded() -> None:
    assert rec.interval_of(2, "hours") == timedelta(hours=2)
    assert rec.interval_of(15, "minutes") == timedelta(minutes=15)
    for value, unit in [(10, "minutes"), (0, "days"), (60, "weeks"), (1, "months")]:
        with pytest.raises(HTTPException):
            rec.interval_of(value, unit)
    assert rec.describe_interval(1, "weeks") == "every week"
    assert rec.describe_interval(3, "days") == "every 3 days"


def test_next_run_skips_missed_slots_instead_of_bursting() -> None:
    hour = timedelta(hours=1)
    assert rec.next_after(NOW - timedelta(minutes=1), hour, NOW) == NOW + timedelta(minutes=59)
    # Worker was down for 5.5 hours: the next slot is the first one after now.
    late = rec.next_after(NOW - timedelta(hours=5, minutes=30), hour, NOW)
    assert late == NOW + timedelta(minutes=30)
    assert rec.next_after(NOW, hour, NOW) == NOW + hour


@pytest.mark.parametrize("question", [
    "Send an email to priya@acme.com every Monday with the tiramisu status",
    "email the team a reminder every 2 hours to drink water",
    "every day at 9am send alex@acme.com the sales numbers",
    "Set up a weekly email to the board",
])
def test_recurring_email_requests_are_detected(question: str) -> None:
    assert ask_agent._wants_recurring(question)


@pytest.mark.parametrize("question", [
    "Send an email to priya@acme.com about the launch",
    "What is our weekly report process?",
    "Who owns daily standups?",
])
def test_one_off_and_plain_questions_are_not_recurring(question: str) -> None:
    assert not ask_agent._wants_recurring(question)


def test_start_times_without_offsets_use_the_users_timezone() -> None:
    start = ask_agent._parse_start("2026-10-26T09:00:00", "America/New_York")
    assert start == datetime(2026, 10, 26, 13, 0, tzinfo=timezone.utc)
    assert ask_agent._parse_start("2026-10-26T09:00:00+05:30", None) == datetime(
        2026, 10, 26, 3, 30, tzinfo=timezone.utc
    )
    assert ask_agent._parse_start("not a date", None) is None


async def test_ask_drafts_a_recurring_email_instead_of_a_one_off(monkeypatch) -> None:
    async def base_answer(*args, **kwargs):
        return QueryResponse(answer="base", sources=[], confidence="low", routed=False)

    async def connected(*args):
        return True

    async def no_files(org_id, user_id):
        return []

    async def people(org_id, query, exclude_user_id=None):
        return [{"user_id": "", "name": "Priya", "email": "priya@acme.com"}]

    def call(call_id, name, arguments):
        return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=json.dumps(arguments)))

    sent: list[dict] = []

    async def create(**kwargs):
        sent.append(kwargs)
        step = len(sent)
        if step == 1:
            message = SimpleNamespace(content=None, tool_calls=[call("c1", "lookup_person", {"query": "Priya"})])
        elif step == 2:
            # One person found: the agent must NOT force a one-off propose_email.
            assert "tool_choice" not in kwargs or kwargs["tool_choice"] in ("auto", None)
            message = SimpleNamespace(content=None, tool_calls=[call("c2", "propose_recurring_email", {
                "subject": "Tiramisu status", "body": "Hi Priya, here is this week's status.",
                "interval_value": 1, "interval_unit": "weeks",
                "start_at": "2026-10-26T09:00:00", "refresh_instructions": "This week's Tiramisu status",
            })])
        else:
            message = SimpleNamespace(content="I drafted a weekly email to Priya. Approve it below.", tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    client = SimpleNamespace(
        configuration=SimpleNamespace(supports_tools=True),
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
    )

    async def fake_client(org_id):
        return client

    monkeypatch.setattr(ask_agent, "generate_answer", base_answer)
    monkeypatch.setattr(ask_agent, "get_llm_client", fake_client)
    monkeypatch.setattr(ask_agent, "_user_files", no_files)
    monkeypatch.setattr(ask_agent, "lookup_messageable_people", people)
    monkeypatch.setattr("weaver.integrations.has_google_workspace_connection", connected)

    response = await ask_agent.run_ask_agent(
        question="Send Priya an email every Monday at 9am with the tiramisu status",
        retrieval=RetrievalResult(chunks=[], experts=[], entities_found=[]),
        history=[], org_id="o", user_id="u", user_timezone="America/New_York",
    )

    draft = response.proposed_recurring_email
    assert draft is not None
    assert (draft.recipient_email, draft.interval_value, draft.interval_unit) == ("priya@acme.com", 1, "weeks")
    assert draft.start_at == datetime(2026, 10, 26, 13, 0, tzinfo=timezone.utc)
    assert draft.refresh_instructions == "This week's Tiramisu status" and draft.google_connected
    assert response.proposed_email is None and response.proposed_message is None
    assert "America/New_York" in sent[0]["messages"][0]["content"]
    assert response.answer.startswith("I drafted a weekly email")


async def test_invalid_intervals_become_tool_errors() -> None:
    result, *_ = await ask_agent._safe_run_tool(
        name="propose_recurring_email",
        arguments={"subject": "x", "body": "y", "interval_value": 5, "interval_unit": "minutes"},
        org_id="o", user_id="u", people_cache={},
    )
    assert "15 minutes" in result["error"]
