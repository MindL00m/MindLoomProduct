"""Ask → Expert Messages propose/confirm and thread ingest helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import ask_agent
import review_workflows
from models import ProposedExpertMessage


def test_wants_messaging_detects_notify_intent() -> None:
    assert ask_agent._wants_messaging("Tell Priya the tiramisu is delayed")
    assert ask_agent._wants_messaging("Please message Alex about the launch")
    assert not ask_agent._wants_messaging("What is our pricing model?")


def test_extract_recipient_and_draft_from_question() -> None:
    q = "send a message to the CTO saying the tiramisu is delayed"
    assert ask_agent._extract_recipient_query(q) == "CTO"
    assert "tiramisu is delayed" in ask_agent._draft_message_from_question(q).lower()


@pytest.mark.asyncio
async def test_ensure_proposal_builds_card_when_model_skips_tool(monkeypatch):
    async def fake_lookup(org_id, query, exclude_user_id=None, limit=8):
        assert query == "CTO"
        return [{
            "user_id": "u-cto",
            "name": "Alan",
            "email": "alan@gmail.com",
            "title": "CTO",
        }]

    monkeypatch.setattr(ask_agent, "lookup_messageable_people", fake_lookup)
    proposal = await ask_agent._ensure_proposal(
        question="send a message to the CTO saying the tiramisu is delayed",
        org_id="org-1",
        user_id="u-me",
        people_cache={},
        proposal=None,
    )
    assert proposal is not None
    assert proposal.recipient_email == "alan@gmail.com"
    assert "tiramisu" in proposal.message.lower()


@pytest.mark.asyncio
async def test_lookup_messageable_people_ranks_email_exact(monkeypatch):
    rows = [
        {
            "user_id": "u2",
            "name": "Priya Shah",
            "email": "priya@example.com",
            "role": "member",
            "rank_score": 0,
        },
        {
            "user_id": "u3",
            "name": "Other Priya",
            "email": "other@example.com",
            "role": "member",
            "rank_score": 3,
        },
    ]

    class FakeResult:
        def mappings(self):
            return self

        def all(self):
            return rows

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def execute(self, *_args, **_kwargs):
            return FakeResult()

    class FakeFactory:
        def __call__(self):
            return FakeSession()

    monkeypatch.setattr(review_workflows, "get_session_factory", FakeFactory)

    import database

    monkeypatch.setattr(
        database,
        "get_neo4j_driver",
        lambda: (_ for _ in ()).throw(RuntimeError("no neo")),
    )

    people = await review_workflows.lookup_messageable_people(
        "org-1", "priya@example.com", exclude_user_id="u1"
    )
    assert people[0]["user_id"] == "u2"
    assert people[0]["email"] == "priya@example.com"
    assert "rank_score" not in people[0]


@pytest.mark.asyncio
async def test_propose_tool_does_not_send(monkeypatch):
    started = {"called": False}

    async def boom(*_args, **_kwargs):
        started["called"] = True
        raise AssertionError("must not send from propose tool")

    monkeypatch.setattr(review_workflows, "start_expert_conversation", boom)

    people_cache = {
        "u-expert": {
            "user_id": "u-expert",
            "name": "Priya Shah",
            "email": "priya@example.com",
        }
    }
    result, proposal, pr, ws = await ask_agent._run_tool(
        name="propose_expert_message",
        arguments={
            "recipient_user_id": "u-expert",
            "message": "Tiramisu is delayed",
        },
        org_id="org-1",
        user_id="u-me",
        people_cache=people_cache,
    )
    assert started["called"] is False
    assert pr is None
    assert ws is None
    assert result["status"] == "proposed"
    assert isinstance(proposal, ProposedExpertMessage)
    assert proposal.recipient_user_id == "u-expert"
    assert proposal.message == "Tiramisu is delayed"


@pytest.mark.asyncio
async def test_send_proposed_calls_start_conversation(monkeypatch):
    async def fake_start(**kwargs):
        assert kwargs["expert_user_id"] == "u-expert"
        assert kwargs["requester_user_id"] == "u-me"
        assert kwargs["message"] == "Hello"
        return "review-123"

    monkeypatch.setattr(review_workflows, "start_expert_conversation", fake_start)
    out = await review_workflows.send_proposed_expert_message(
        org_id="org-1",
        requester_user_id="u-me",
        recipient_user_id="u-expert",
        message="Hello",
    )
    assert out == {"review_id": "review-123", "status": "sent"}


@pytest.mark.asyncio
async def test_send_proposed_rejects_self():
    with pytest.raises(Exception) as exc:
        await review_workflows.send_proposed_expert_message(
            org_id="org-1",
            requester_user_id="u-me",
            recipient_user_id="u-me",
            message="Hello",
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_ingest_expert_thread_builds_external_source(monkeypatch):
    captured: dict = {}

    async def fake_load(org_id, review_id):
        assert org_id == "org-1"
        assert review_id == "rev-1"
        ts = datetime(2026, 7, 20, tzinfo=timezone.utc)
        return {
            "thread": {
                "review_id": "rev-1",
                "title": "Conversation with Priya",
                "created_by": "u-me",
                "owner_user_id": "u-expert",
                "requester_email": "me@example.com",
                "requester_name": "Me",
                "expert_email": "priya@example.com",
                "expert_name": "Priya",
            },
            "messages": [
                {
                    "message_id": "m1",
                    "sender_user_id": "u-me",
                    "body": "Tiramisu delayed",
                    "created_at": ts,
                    "sender_name": "Me",
                    "sender_email": "me@example.com",
                }
            ],
        }

    async def fake_ingest(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(total_chunks=1)

    monkeypatch.setattr(review_workflows, "_load_thread_for_ingest", fake_load)
    monkeypatch.setattr(review_workflows, "ingest_external_source", fake_ingest)

    await review_workflows.ingest_expert_thread("org-1", "rev-1")

    assert captured["provider"] == "expert_messages"
    assert captured["external_id"] == "rev-1"
    assert captured["conversation"].source == "expert_messages"
    assert captured["conversation"].conversation_id == "expert_messages:rev-1"
    assert captured["document"].source == "expert_messages"
    assert "me@example.com" in captured["document"].visible_to
    assert "priya@example.com" in captured["document"].visible_to


@pytest.mark.asyncio
async def test_send_expert_message_enqueues_ingest(monkeypatch):
    enqueued: list[tuple] = []

    class FakeResult:
        def mappings(self):
            return self

        def one_or_none(self):
            return {"created_by": "u-me", "owner_user_id": "u-expert"}

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def execute(self, *_args, **_kwargs):
            return FakeResult()

        async def commit(self):
            return None

    class FakeFactory:
        def __call__(self):
            return FakeSession()

    async def fake_enqueue(kind, **kwargs):
        enqueued.append((kind, kwargs))
        return "job-1"

    monkeypatch.setattr(review_workflows, "get_session_factory", FakeFactory)
    monkeypatch.setattr("durable_jobs.enqueue", fake_enqueue)

    result = await review_workflows.send_expert_message(
        org_id="org-1",
        user_id="u-me",
        review_id="rev-9",
        body="Hello expert",
    )
    assert result["status"] == "sent"
    assert enqueued
    assert enqueued[0][0] == "expert_thread_ingest"
    assert enqueued[0][1]["payload"]["review_id"] == "rev-9"
