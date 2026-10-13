"""Ask → Expert Messages propose/confirm and thread ingest helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from loombot import ask_agent
from keeper import review_workflows
from core.models import ProposedExpertMessage


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
    proposal, email = await ask_agent._ensure_proposals(
        question="send a message to the CTO saying the tiramisu is delayed",
        org_id="org-1",
        user_id="u-me",
        people_cache={},
        proposal=None,
        email_proposal=None,
        google_connected=True,
    )
    assert proposal is not None
    assert proposal.recipient_email == "alan@gmail.com"
    assert "tiramisu" in proposal.message.lower()
    assert email is not None
    assert email.recipient_email == "alan@gmail.com"
    assert email.google_connected is True
    assert "tiramisu" in email.body.lower()


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

    from core import database

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
    result, proposal, pr, ws, email = await ask_agent._run_tool(
        name="propose_expert_message",
        arguments={
            "recipient_user_id": "u-expert",
            "message": "Tiramisu is delayed",
        },
        org_id="org-1",
        user_id="u-me",
        people_cache=people_cache,
        google_connected=True,
    )
    assert started["called"] is False
    assert pr is None
    assert ws is None
    assert result["status"] == "proposed"
    assert isinstance(proposal, ProposedExpertMessage)
    assert proposal.recipient_user_id == "u-expert"
    assert proposal.message == "Tiramisu is delayed"
    assert email is not None
    assert email.recipient_email == "priya@example.com"
    assert email.body == "Tiramisu is delayed"


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
    monkeypatch.setattr("core.durable_jobs.enqueue", fake_enqueue)

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


def test_wants_messaging_detects_email_intent() -> None:
    assert ask_agent._wants_messaging("Email jane@vendor.com about the delay")
    assert ask_agent._wants_messaging("Send an email to the vendor")
    assert not ask_agent._wants_messaging("What is our email policy?")


@pytest.mark.asyncio
async def test_ensure_proposals_allows_empty_email_when_no_match(monkeypatch):
    async def fake_lookup(*_args, **_kwargs):
        return []

    monkeypatch.setattr(ask_agent, "lookup_messageable_people", fake_lookup)
    proposal, email = await ask_agent._ensure_proposals(
        question="email someone about the launch delay",
        org_id="org-1",
        user_id="u-me",
        people_cache={},
        proposal=None,
        email_proposal=None,
        google_connected=False,
    )
    assert proposal is None
    assert email is not None
    assert email.recipient_email == ""
    assert email.google_connected is False
    assert "launch delay" in email.body.lower()


@pytest.mark.asyncio
async def test_lookup_includes_directory_only_email(monkeypatch):
    class FakeResult:
        def mappings(self):
            return self

        def all(self):
            return []

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

    async def fake_directory(org_id, query):
        assert org_id == "org-1"
        assert query == "vendor"
        return [{
            "email": "vendor@example.com",
            "name": "Vendor Ops",
            "title": "Vendor",
            "department": None,
            "rank_score": 3,
        }]

    monkeypatch.setattr(review_workflows, "get_session_factory", FakeFactory)
    monkeypatch.setattr(
        review_workflows, "_directory_people_matching_query", fake_directory
    )

    people = await review_workflows.lookup_messageable_people(
        "org-1", "vendor", exclude_user_id="u1"
    )
    assert people[0]["email"] == "vendor@example.com"
    assert people[0]["user_id"] == ""
    assert people[0]["name"] == "Vendor Ops"


@pytest.mark.asyncio
async def test_lookup_includes_typed_email_when_unknown(monkeypatch):
    class FakeResult:
        def mappings(self):
            return self

        def all(self):
            return []

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

    from core import database

    monkeypatch.setattr(
        database,
        "get_neo4j_driver",
        lambda: (_ for _ in ()).throw(RuntimeError("no neo")),
    )

    people = await review_workflows.lookup_messageable_people(
        "org-1", "jane@vendor.com", exclude_user_id="u1"
    )
    assert people[0]["email"] == "jane@vendor.com"
    assert people[0]["user_id"] == ""


@pytest.mark.asyncio
async def test_propose_email_does_not_send(monkeypatch):
    sent = {"called": False}

    async def boom(*_args, **_kwargs):
        sent["called"] = True
        raise AssertionError("must not send from propose tool")

    monkeypatch.setattr(review_workflows, "send_proposed_email", boom)
    result, proposal, pr, ws, email = await ask_agent._run_tool(
        name="propose_email",
        arguments={
            "recipient_email": "jane@vendor.com",
            "recipient_name": "Jane",
            "subject": "Launch delay",
            "body": "The launch is delayed one week.",
        },
        org_id="org-1",
        user_id="u-me",
        people_cache={},
        google_connected=True,
    )
    assert sent["called"] is False
    assert proposal is None
    assert pr is None
    assert ws is None
    assert result["status"] == "proposed"
    assert email is not None
    assert email.recipient_email == "jane@vendor.com"
    assert email.subject == "Launch delay"
    assert email.google_connected is True


@pytest.mark.asyncio
async def test_send_proposed_email_requires_valid_address():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await review_workflows.send_proposed_email(
            org_id="org-1",
            requester_user_id="u-me",
            recipient_email="not-an-email",
            subject="Hi",
            body="Hello",
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_send_proposed_email_requires_google(monkeypatch):
    from fastapi import HTTPException

    async def not_connected(*_args, **_kwargs):
        return False

    monkeypatch.setattr(
        "weaver.integrations.has_google_workspace_connection", not_connected
    )
    with pytest.raises(HTTPException) as exc:
        await review_workflows.send_proposed_email(
            org_id="org-1",
            requester_user_id="u-me",
            recipient_email="jane@vendor.com",
            subject="Hi",
            body="Hello",
        )
    assert exc.value.status_code == 400
    assert "Connect Google Workspace" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_send_proposed_email_uses_caller_gmail(monkeypatch):
    captured: dict = {}

    async def connected(*_args, **_kwargs):
        return True

    async def fake_send(**kwargs):
        captured.update(kwargs)
        return "gmail-99"

    monkeypatch.setattr(
        "weaver.integrations.has_google_workspace_connection", connected
    )
    monkeypatch.setattr("shuttle.notification_delivery.send_user_gmail", fake_send)
    out = await review_workflows.send_proposed_email(
        org_id="org-1",
        requester_user_id="u-me",
        recipient_email="jane@vendor.com",
        subject="Launch delay",
        body="The launch is delayed.",
    )
    assert out == {"status": "sent", "provider_message_id": "gmail-99"}
    assert captured["user_id"] == "u-me"
    assert captured["recipient"] == "jane@vendor.com"
    assert captured["subject"] == "Launch delay"


@pytest.mark.asyncio
async def test_create_expert_request_without_email_uses_name_only_query(monkeypatch):
    captured: dict = {}

    class FakeResult:
        def mappings(self):
            return self

        def one_or_none(self):
            return None

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def execute(self, statement, params):
            captured["sql"] = str(statement)
            captured["params"] = params
            return FakeResult()

    class FakeFactory:
        def __call__(self):
            return FakeSession()

    monkeypatch.setattr(review_workflows, "get_session_factory", FakeFactory)
    result = await review_workflows.create_expert_request(
        org_id="org-1",
        requester_user_id="u-me",
        question="Email Amey about the launch",
        expert_name="Amey Agarwal",
        expert_email=None,
        source_ids=[],
    )

    assert result is None
    assert ":email" not in captured["sql"]
    assert captured["params"] == {"org": "org-1", "name": "Amey Agarwal"}
