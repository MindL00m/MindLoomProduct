"""Slack Loombot: signatures, events, answers, and thread ingestion."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.models import ChatMessage, RetrievalResult
from keeper.auth import require_user_context
from weaver import slack


def _sign(secret: str, body: bytes, timestamp: str) -> str:
    digest = hmac.new(
        secret.encode("utf-8"),
        b"v0:" + timestamp.encode("utf-8") + b":" + body,
        hashlib.sha256,
    ).hexdigest()
    return f"v0={digest}"


def _install(**overrides: object) -> dict:
    base: dict = {
        "install_id": "inst-1",
        "org_id": "org-1",
        "team_id": "T1",
        "team_name": "Acme",
        "bot_user_id": "UBOT",
        "bot_token_enc": "xoxb-test",
        "signing_secret_enc": "signing-secret",
        "scopes": "app_mentions:read,chat:write",
        "ingest_channel_ids": '["C1"]',
        "ingest_mention_threads": True,
        "answer_mentions": True,
        "unlinked_user_policy": "ignore",
        "status": "active",
        "created_at": datetime.now(timezone.utc),
        "last_error": None,
        "last_event_at": None,
    }
    base.update(overrides)
    return base


def test_signature_accepts_a_fresh_request_and_rejects_tampering() -> None:
    body = b'{"type":"event_callback"}'
    timestamp = str(int(time.time()))
    secret = "signing-secret"
    signature = _sign(secret, body, timestamp)
    assert slack.verify_slack_signature(body, timestamp, signature, secret) is True
    assert slack.verify_slack_signature(body + b" ", timestamp, signature, secret) is False
    assert slack.verify_slack_signature(
        body, timestamp, signature, secret, now=int(timestamp) + 600
    ) is False


def test_strip_bot_mention_leaves_other_people() -> None:
    assert slack.strip_bot_mention("Hey <@UBOT|Loombot> ship it", "UBOT") == "Hey ship it"
    assert "<@U2>" in slack.strip_bot_mention("<@UBOT> ask <@U2>", "UBOT")


@pytest.mark.asyncio
async def test_format_slack_answer_numbers_citations_and_uses_mrkdwn() -> None:
    source = SimpleNamespace(
        chunk_id="c-1",
        citation=SimpleNamespace(source_label="#general", source_url="https://slack.example/t"),
    )
    rendered = await slack.format_slack_answer(
        "Beta **slipped**. [SOURCE: c-1]", [source], "org-1"
    )
    assert "Beta *slipped*." in rendered
    assert "[1] <https://slack.example/t|#general>" in rendered
    assert "[SOURCE:" not in rendered


def _client(monkeypatch: pytest.MonkeyPatch, install: dict) -> TestClient:
    async def fetch(install_id: str) -> dict | None:
        return install if install_id == install["install_id"] else None

    monkeypatch.setattr(slack, "fetch_install", fetch)
    app = FastAPI()
    from api.routes.slack import router

    app.include_router(router)
    return TestClient(app)


def test_draft_answers_url_verification_without_a_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    install = _install(status="draft", signing_secret_enc=None, created_at=datetime.now(timezone.utc))
    client = _client(monkeypatch, install)
    response = client.post(
        "/webhooks/slack/events/inst-1",
        json={"type": "url_verification", "challenge": "challenge-token"},
    )
    assert response.status_code == 200
    assert response.json() == {"challenge": "challenge-token"}

    expired = _install(
        status="draft",
        signing_secret_enc=None,
        created_at=datetime.now(timezone.utc) - timedelta(hours=2),
    )
    client = _client(monkeypatch, expired)
    response = client.post(
        "/webhooks/slack/events/inst-1",
        json={"type": "url_verification", "challenge": "challenge-token"},
    )
    assert response.status_code == 401


def test_retried_and_duplicate_events_are_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    scheduled: list[str] = []

    async def claim(event_id: str) -> bool:
        scheduled.append(f"claim:{event_id}")
        return False

    async def process(*_args: object) -> None:
        scheduled.append("process")

    monkeypatch.setattr(slack, "claim_event", claim)
    monkeypatch.setattr(slack, "process_slack_event", process)
    client = _client(monkeypatch, _install())
    body = json.dumps({
        "type": "event_callback",
        "event_id": "Ev1",
        "team_id": "T1",
        "event": {"type": "message", "channel": "C1", "ts": "1.0"},
    }).encode()
    timestamp = str(int(time.time()))
    headers = {
        "Content-Type": "application/json",
        "X-Slack-Request-Timestamp": timestamp,
        "X-Slack-Signature": _sign("signing-secret", body, timestamp),
    }
    retry = client.post(
        "/webhooks/slack/events/inst-1",
        content=body,
        headers={**headers, "X-Slack-Retry-Num": "1"},
    )
    duplicate = client.post("/webhooks/slack/events/inst-1", content=body, headers=headers)
    assert retry.status_code == 200
    assert duplicate.status_code == 200
    assert scheduled == ["claim:Ev1"]


@pytest.mark.asyncio
async def test_bot_messages_are_not_answered_or_ingested(monkeypatch: pytest.MonkeyPatch) -> None:
    called = {"n": 0}

    async def boom(*_args: object, **_kwargs: object) -> None:
        called["n"] += 1

    monkeypatch.setattr(slack, "answer_mention", boom)
    monkeypatch.setattr(slack, "mark_dirty", boom)
    install = _install()
    await slack.handle_event(install, {"event": {
        "type": "app_mention", "bot_id": "B1", "user": "U1",
        "text": "hi", "channel": "C1", "ts": "1.0",
    }})
    await slack.handle_event(install, {"event": {
        "type": "message", "user": "UBOT", "text": "Thinking…",
        "channel": "C1", "ts": "2.0",
    }})
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_mention_answers_as_the_matching_mindloom_user(monkeypatch: pytest.MonkeyPatch) -> None:
    posted: list[str] = []
    captured: dict = {}

    async def post(token: str, *, channel: str, thread_ts: str, message_ts: str | None, text_value: str) -> str:
        posted.append(text_value)
        return message_ts or "9.0"

    async def sender(_install: dict, slack_user_id: str) -> dict:
        assert slack_user_id == "U1"
        return {"user_id": "u-ada", "email": "ada@acme.com"}

    async def history(*_args: object) -> list[ChatMessage]:
        return [ChatMessage(role="user", content="Ada: earlier note")]

    async def tokens(org_id: str, user_id: str) -> list[str]:
        return [f"org:{org_id}", f"user:{user_id}"]

    async def retrieve(question: str, history: list, org_id: str, access_tokens: list[str] | None = None):
        captured["question"] = question
        captured["tokens"] = access_tokens
        captured["history"] = history
        return RetrievalResult(chunks=[], experts=[], entities_found=[])

    async def agent(**kwargs: object):
        captured["user_id"] = kwargs["user_id"]
        return SimpleNamespace(
            answer="The beta slipped. [SOURCE: c-1]",
            sources=[SimpleNamespace(
                chunk_id="c-1",
                citation=SimpleNamespace(source_label="#general", source_url=None),
            )],
            proposed_email=None,
            proposed_message=None,
            proposed_workspace=SimpleNamespace(name="Tiramisu"),
            proposed_pull_request=None,
        )

    async def cleared(_install_id: str) -> None:
        captured["cleared"] = True

    monkeypatch.setattr(slack, "get_settings", lambda: SimpleNamespace(frontend_url="http://localhost:5500"))
    monkeypatch.setattr(slack, "_post_or_update", post)
    monkeypatch.setattr(slack, "_slack_user", sender)
    monkeypatch.setattr(slack, "_history", history)
    monkeypatch.setattr(slack, "clear_error", cleared)
    monkeypatch.setattr("keeper.auth.get_user_access_tokens", tokens)
    monkeypatch.setattr("brain.retrieval.retrieve", retrieve)
    monkeypatch.setattr("loombot.ask_agent.run_ask_agent", agent)

    await slack.answer_mention(_install(), {
        "type": "app_mention",
        "user": "U1",
        "channel": "C1",
        "ts": "2.0",
        "text": "<@UBOT> what is the status of tiramisu?",
    })
    assert captured["question"] == "what is the status of tiramisu?"
    assert captured["tokens"] == ["org:org-1", "user:u-ada"]
    assert captured["user_id"] == "u-ada"
    assert captured["history"][0].content == "Ada: earlier note"
    assert posted[0] == "Thinking…"
    assert "[1] #general" in posted[1]
    assert "/dashboard?tab=ask" in posted[1]
    assert "a workspace" in posted[1]


@pytest.mark.asyncio
async def test_unlinked_sender_is_ignored_or_limited_to_org_knowledge(monkeypatch: pytest.MonkeyPatch) -> None:
    posted: list[str] = []
    captured: dict = {"retrieve": 0, "agent": 0, "plain": 0}

    async def post(token: str, *, channel: str, thread_ts: str, message_ts: str | None, text_value: str) -> str:
        posted.append(text_value)
        return "9.0"

    async def nobody(*_args: object) -> None:
        return None

    async def retrieve(question: str, history: list, org_id: str, access_tokens: list[str] | None = None):
        captured["retrieve"] += 1
        captured["tokens"] = access_tokens
        return RetrievalResult(chunks=[], experts=[], entities_found=[])

    async def agent(**_kwargs: object) -> None:
        captured["agent"] += 1
        raise AssertionError("unlinked senders must not use another user's tools")

    async def plain(*_args: object, **_kwargs: object):
        captured["plain"] += 1
        return SimpleNamespace(
            answer="Org overview.", sources=[], proposed_email=None,
            proposed_message=None, proposed_workspace=None, proposed_pull_request=None,
        )

    async def cleared(_install_id: str) -> None:
        return None

    monkeypatch.setattr(slack, "_post_or_update", post)
    monkeypatch.setattr(slack, "_slack_user", nobody)
    monkeypatch.setattr(slack, "_history", lambda *_a, **_k: _empty_history())
    monkeypatch.setattr(slack, "clear_error", cleared)
    monkeypatch.setattr("brain.retrieval.retrieve", retrieve)
    monkeypatch.setattr("loombot.ask_agent.run_ask_agent", agent)
    monkeypatch.setattr("loombot.answerer.generate_answer", plain)

    event = {"user": "U9", "channel": "C1", "ts": "3.0", "text": "<@UBOT> hello"}
    await slack.answer_mention(_install(unlinked_user_policy="ignore"), event)
    assert captured["retrieve"] == 0
    assert "MindLoom account" in posted[-1]

    await slack.answer_mention(_install(unlinked_user_policy="org"), event)
    assert captured["tokens"] == ["org:org-1"]
    assert captured["agent"] == 0
    assert captured["plain"] == 1
    assert posted[-1] == "Org overview."


async def _empty_history() -> list[ChatMessage]:
    return []


@pytest.mark.asyncio
async def test_thread_ingest_keeps_channel_members_and_drops_the_bot(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    async def replies(*_args: object) -> list[dict]:
        return [
            {"user": "UBOT", "text": "Thinking…", "ts": "100.0"},
            {"user": "U1", "text": "<@UBOT> launch slipped", "ts": "100.1"},
            {"user": "U2", "bot_id": "B9", "text": "ignore me", "ts": "100.2"},
            {"user": "U2", "text": "confirmed", "ts": "100.3"},
        ]

    async def emails(*_args: object) -> list[str]:
        return ["ada@acme.com", "bea@acme.com"]

    async def user(_token: str, _team: str, user_id: str) -> dict:
        name = "Ada" if user_id == "U1" else "Bea"
        return {"id": user_id, "real_name": name, "profile": {"email": f"{name.lower()}@acme.com"}}

    async def channel_name(*_args: object) -> str:
        return "general"

    async def permalink(*_args: object) -> str:
        return "https://acme.slack.com/archives/C1/p1001"

    async def ingest(**kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(slack, "_thread_messages", replies)
    monkeypatch.setattr(slack, "_member_emails", emails)
    monkeypatch.setattr(slack, "cached_user", user)
    monkeypatch.setattr(slack, "_channel_name", channel_name)
    monkeypatch.setattr(slack, "_permalink", permalink)
    monkeypatch.setattr("brain.source_registry.ingest_external_source", ingest)

    assert await slack.ingest_thread(_install(), "C1", "100.1") is True
    assert captured["provider"] == "slack"
    assert captured["external_id"] == "T1:C1:100.1"
    assert captured["version"] == "100.3"
    document = captured["document"]
    conversation = captured["conversation"]
    assert document.visible_to == ["ada@acme.com", "bea@acme.com"]
    assert document.source == "slack"
    assert document.source_label == "#general"
    assert document.source_url == "https://acme.slack.com/archives/C1/p1001"
    assert [message.text for message in conversation.messages] == ["launch slipped", "confirmed"]
    assert "UBOT" not in {message.sender for message in conversation.messages}


@pytest.mark.asyncio
async def test_slack_pagination_sends_integer_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict]] = []

    async def api(_token: str, method: str, args: dict | None = None) -> dict:
        calls.append((method, dict(args or {})))
        return {"messages": [], "members": [], "response_metadata": {}}

    monkeypatch.setattr(slack, "slack_api", api)
    monkeypatch.setattr(slack, "_cache_get", lambda *_a, **_k: _none())
    monkeypatch.setattr(slack, "_cache_set", lambda *_a, **_k: _none())

    await slack._thread_messages("xoxb", "C1", "1.0")
    await slack._member_emails("xoxb", "T1", "C1")

    async def fetch(_install_id: str) -> dict:
        return _install()

    monkeypatch.setattr(slack, "fetch_install", fetch)
    monkeypatch.setattr(slack, "_token", lambda _install: "xoxb")
    await slack.backfill_install("inst-1", 7)

    limits = {method: args["limit"] for method, args in calls if "limit" in args}
    assert limits["conversations.replies"] == 200
    assert limits["conversations.members"] == 200
    assert limits["conversations.history"] == 100
    assert all(isinstance(limit, int) for limit in limits.values())


async def _none(*_args: object, **_kwargs: object):
    return None


@pytest.mark.asyncio
async def test_history_survives_slack_argument_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(*_args: object, **_kwargs: object) -> list:
        raise slack.SlackApiError("invalid_arguments")

    monkeypatch.setattr(slack, "_thread_messages", boom)
    history = await slack._history(_install(), "xoxb", "C1", "1.0", "1.1")
    assert history == []


@pytest.mark.asyncio
async def test_cached_user_skips_blank_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    async def api(*_args: object, **_kwargs: object) -> dict:
        raise AssertionError("users.info must not be called for a blank id")

    monkeypatch.setattr(slack, "slack_api", api)
    assert await slack.cached_user("xoxb", "T1", "") == {}
    assert await slack.cached_user("xoxb", "T1", "   ") == {}


@pytest.mark.asyncio
async def test_unresolved_slack_user_does_not_abort_the_mention(monkeypatch: pytest.MonkeyPatch) -> None:
    async def missing(*_args: object, **_kwargs: object) -> dict:
        raise slack.SlackApiError("user_not_found")

    monkeypatch.setattr(slack, "cached_user", missing)
    monkeypatch.setattr(slack, "_token", lambda _install: "xoxb")
    assert await slack._slack_user(_install(), "U1") is None


@pytest.mark.asyncio
async def test_credentials_reject_a_workspace_linked_to_another_org(monkeypatch: pytest.MonkeyPatch) -> None:
    async def existing(_org_id: str) -> dict:
        return _install()

    async def other_team(_team_id: str) -> dict:
        return {"org_id": "org-other", "team_id": "T9"}

    async def auth_test(_token: str, method: str, args: dict | None = None) -> dict:
        assert method == "auth.test"
        return {"ok": True, "team_id": "T9", "team": "Other", "user_id": "UB", "_scopes": "chat:write"}

    monkeypatch.setattr(slack, "fetch_install_for_org", existing)
    monkeypatch.setattr(slack, "fetch_install_by_team", other_team)
    monkeypatch.setattr(slack, "slack_api", auth_test)
    with pytest.raises(Exception) as exc:
        await slack.save_credentials("org-1", "xoxb-real-token", "signing-secret")
    assert getattr(exc.value, "status_code", None) == 409


def test_settings_route_rejects_non_admins(monkeypatch: pytest.MonkeyPatch) -> None:
    from api.routes import slack as slack_routes

    async def member() -> tuple[str, str]:
        return "org-1", "u-member"

    async def lookup(_org_id: str, _user_id: str) -> SimpleNamespace:
        return SimpleNamespace(role="member")

    monkeypatch.setattr("keeper.auth.get_user_by_id", lookup)
    app = FastAPI()
    app.include_router(slack_routes.router)
    app.dependency_overrides[require_user_context] = member
    client = TestClient(app)
    response = client.put("/integrations/slack/settings", json={
        "ingest_channel_ids": ["C1"],
        "ingest_mention_threads": True,
        "answer_mentions": True,
        "unlinked_user_policy": "ignore",
    })
    assert response.status_code == 403


async def test_slack_api_sends_form_encoded_arguments(monkeypatch) -> None:
    """Read methods like users.info ignore JSON bodies, so every call is form-encoded."""

    import httpx

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True, "user": {"id": "U1"}})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        slack.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )

    await slack.slack_api("xoxb-test", "users.info", {"user": "U1", "include_locale": True, "skip": None})

    request = seen[0]
    assert request.url.path == "/api/users.info"
    assert request.headers["content-type"].startswith("application/x-www-form-urlencoded")
    assert request.content.decode() == "user=U1&include_locale=true"
    assert request.headers["authorization"] == "Bearer xoxb-test"
