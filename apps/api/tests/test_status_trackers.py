"""Status trackers: matching new knowledge, and starting trackers from Ask."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

from core.models import QueryResponse, RetrievalResult
from lens import status_trackers as st
from loombot import ask_agent


def _chunk(chunk_id: str, text: str, embedding=None, visible_to=None) -> st.NewChunk:
    return st.NewChunk(
        chunk_id=chunk_id, text=text, summary=text[:60], embedding=embedding,
        occurred_at=datetime(2026, 10, 21, tzinfo=timezone.utc), visible_to=visible_to or [],
    )


def test_keywords_drop_filler_words() -> None:
    assert st.extract_keywords("Status report on the Tiramisu website launch") == [
        "tiramisu", "website", "launch",
    ]


def test_candidates_need_similarity_or_two_keyword_hits() -> None:
    tracker = {"embedding": [1.0, 0.0], "keywords": ["tiramisu", "website", "launch"]}
    close = _chunk("close", "Unrelated words", embedding=[0.9, 0.1])
    keywords = _chunk("kw", "The Tiramisu launch moved to Friday", embedding=[0.0, 1.0])
    single = _chunk("single", "Website maintenance tonight", embedding=[0.0, 1.0])
    far = _chunk("far", "Lunch menu", embedding=[0.0, 1.0])
    ids = [c.chunk_id for c in st._candidates(tracker, [far, single, keywords, close])]
    assert ids == ["close", "kw"]


def test_visibility_respects_chunk_acl() -> None:
    assert st._visible([], {"user:u1"})
    assert st._visible(["user:u1", "group:x"], {"user:u1"})
    assert not st._visible(["user:u2"], {"user:u1"})


def test_status_values_are_normalised() -> None:
    assert st._clean_status("At risk") == "at_risk"
    assert st._clean_status("whatever", "on_track") == "on_track"


async def test_ask_starts_a_tracker_and_returns_a_card(monkeypatch) -> None:
    created_with: list[tuple] = []

    async def fake_create(org_id, user_id, subject, title=None):
        created_with.append((org_id, user_id, subject, title))
        return {
            "tracker_id": "t1", "title": "Website launch", "status": "at_risk",
            "summary": "Launch slipped a week.", "update_count": 1,
            "updates": [
                {"kind": "created", "occurred_at": "2026-10-21", "summary": "Started tracking",
                 "source_label": None, "source_chunk_ids": []},
                {"kind": "history", "occurred_at": "2026-10-20", "summary": "Launch moved to Oct 28.",
                 "source_label": "#launch", "source_chunk_ids": ["c-1"]},
            ],
        }, True

    monkeypatch.setattr(st, "create_tracker", fake_create)

    async def base_answer(*args, **kwargs):
        return QueryResponse(answer="base", sources=[], confidence="low", routed=False)

    async def not_connected(*args):
        return False

    async def no_files(org_id, user_id):
        return []

    def call(call_id, name, arguments):
        return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=json.dumps(arguments)))

    sent: list[dict] = []

    async def create_completion(**kwargs):
        sent.append(kwargs)
        if len(sent) == 1:
            message = SimpleNamespace(content=None, tool_calls=[
                call("c1", "track_status", {"subject": "website launch"}),
            ])
        else:
            message = SimpleNamespace(content="The launch is **at risk**: it moved to Oct 28.", tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    client = SimpleNamespace(
        configuration=SimpleNamespace(supports_tools=True),
        chat=SimpleNamespace(completions=SimpleNamespace(create=create_completion)),
    )

    async def fake_client(org_id):
        return client

    monkeypatch.setattr(ask_agent, "generate_answer", base_answer)
    monkeypatch.setattr(ask_agent, "get_llm_client", fake_client)
    monkeypatch.setattr(ask_agent, "_user_files", no_files)
    monkeypatch.setattr("weaver.integrations.has_google_workspace_connection", not_connected)

    response = await ask_agent.run_ask_agent(
        question="Give me a status report on the website launch",
        retrieval=RetrievalResult(chunks=[], experts=[], entities_found=[]),
        history=[], org_id="org-1", user_id="u-1",
    )

    assert created_with == [("org-1", "u-1", "website launch", None)]
    tool_result = json.loads(sent[1]["messages"][-1]["content"])
    assert tool_result["status"] == "at_risk"
    assert [u["update"] for u in tool_result["recent_updates"]] == ["Launch moved to Oct 28."]
    assert response.status_tracker is not None
    assert (response.status_tracker.tracker_id, response.status_tracker.created) == ("t1", True)
    assert response.answer.startswith("The launch is **at risk**")
