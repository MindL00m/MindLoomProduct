"""Web search: Tavily calls, the low-confidence fallback, and explicit labelling."""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from core.models import QueryResponse, RetrievalResult, WebResult
from loombot import ask_agent, web_search


def _settings(**overrides):
    values = {"web_search_enabled": True, "tavily_api_key": "tvly-test"}
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr("core.config.get_settings", lambda: _settings())


def _client(script):
    sent: list[dict] = []

    async def create(**kwargs):
        sent.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=script(len(sent), kwargs))])

    return SimpleNamespace(
        configuration=SimpleNamespace(supports_tools=True),
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
    ), sent


def _mock_tavily(monkeypatch, payload=None, status=200) -> list[httpx.Request]:
    requests: list[httpx.Request] = []
    real_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status, json=payload or {"results": [
            {"title": "Tavily pricing", "url": "https://tavily.com/pricing",
             "content": "The free plan includes 1,000 API credits per month.", "score": 0.9},
            {"title": "Bad", "url": "javascript:alert(1)", "content": "nope"},
        ]})

    monkeypatch.setattr(
        web_search.httpx, "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    return requests


def test_web_search_is_off_without_a_key(monkeypatch) -> None:
    monkeypatch.setattr("core.config.get_settings", lambda: _settings(tavily_api_key=""))
    assert web_search.web_search_enabled() is False
    monkeypatch.setattr("core.config.get_settings", lambda: _settings(web_search_enabled=False))
    assert web_search.web_search_enabled() is False


async def test_search_web_calls_tavily_and_drops_unsafe_links(monkeypatch, enabled) -> None:
    requests = _mock_tavily(monkeypatch)
    found = await web_search.search_web("tavily free plan credits")
    assert requests[0].headers["Authorization"] == "Bearer tvly-test"
    body = json.loads(requests[0].content)
    assert body["query"] == "tavily free plan credits" and body["max_results"] == 5
    assert [r.url for r in found["results"]] == ["https://tavily.com/pricing"]


async def test_search_web_explains_a_bad_key(monkeypatch, enabled) -> None:
    _mock_tavily(monkeypatch, status=401)
    assert "TAVILY_API_KEY" in (await web_search.search_web("anything"))["error"]


async def test_fallback_skips_internal_questions(monkeypatch, enabled) -> None:
    requests = _mock_tavily(monkeypatch)
    client, sent = _client(lambda step, kw: SimpleNamespace(
        content=json.dumps({"search": False, "query": ""}), tool_calls=None
    ))
    assert await web_search.web_fallback(client, "Who owns Project Tiramisu?") is None
    assert requests == [], "internal questions must never be sent to the search provider"
    assert len(sent) == 1


def _low_confidence(monkeypatch):
    async def base_answer(*args, **kwargs):
        return QueryResponse(
            answer="I don't have enough information to answer this question.",
            sources=[], confidence="low", routed=True,
        )

    async def no_files(org_id, user_id):
        return []

    monkeypatch.setattr(ask_agent, "generate_answer", base_answer)
    monkeypatch.setattr(ask_agent, "_user_files", no_files)


async def test_low_confidence_answer_falls_back_to_labelled_web_answer(monkeypatch, enabled) -> None:
    _low_confidence(monkeypatch)
    _mock_tavily(monkeypatch)

    def script(step, kwargs):
        if step == 1:  # planner: a knowledge question, no tools
            return SimpleNamespace(content=json.dumps({"use_tools": False, "plan": []}), tool_calls=None)
        if step == 2:  # decide + rewrite query
            assert "public" in kwargs["messages"][0]["content"]
            return SimpleNamespace(content=json.dumps({"search": True, "query": "Tavily free plan credits"}), tool_calls=None)
        assert "Tavily pricing" in kwargs["messages"][0]["content"]
        return SimpleNamespace(content="The free plan has 1,000 credits a month [WEB: 1].", tool_calls=None)

    client, _sent = _client(script)

    async def fake_client(org_id):
        return client

    monkeypatch.setattr(ask_agent, "get_llm_client", fake_client)
    response = await ask_agent.run_ask_agent(
        question="How many credits does Tavily's free plan include?",
        retrieval=RetrievalResult(chunks=[], experts=[], entities_found=[]),
        history=[], org_id="o", user_id="u",
    )
    assert response.web_search_used is True
    assert response.answer.startswith("**Web search used.**")
    assert "[WEB: 1]" in response.answer
    assert response.web_results[0].url == "https://tavily.com/pricing"
    assert response.web_search_query == "Tavily free plan credits"
    assert response.routed is False and response.expert is None


async def test_no_fallback_when_web_search_is_not_configured(monkeypatch) -> None:
    monkeypatch.setattr("core.config.get_settings", lambda: _settings(tavily_api_key=""))
    _low_confidence(monkeypatch)
    client, sent = _client(lambda step, kw: SimpleNamespace(
        content=json.dumps({"use_tools": False, "plan": []}), tool_calls=None
    ))

    async def fake_client(org_id):
        return client

    monkeypatch.setattr(ask_agent, "get_llm_client", fake_client)
    response = await ask_agent.run_ask_agent(
        question="How many credits does Tavily's free plan include?",
        retrieval=RetrievalResult(chunks=[], experts=[], entities_found=[]),
        history=[], org_id="o", user_id="u",
    )
    assert response.web_search_used is False and response.confidence == "low"
    assert len(sent) == 1  # only the planner ran


async def test_explicit_web_search_tool_is_always_labelled(monkeypatch, enabled) -> None:
    async def base_answer(*args, **kwargs):
        return QueryResponse(answer="kb", sources=[], confidence="medium", routed=False)

    async def no_files(org_id, user_id):
        return []

    async def not_connected(*args):
        return False

    monkeypatch.setattr(ask_agent, "generate_answer", base_answer)
    monkeypatch.setattr(ask_agent, "_user_files", no_files)
    monkeypatch.setattr("weaver.integrations.has_google_workspace_connection", not_connected)
    _mock_tavily(monkeypatch)

    def call(name, arguments):
        return SimpleNamespace(id="c1", function=SimpleNamespace(name=name, arguments=json.dumps(arguments)))

    def script(step, kwargs):
        if step == 1:
            assert "web_search" in {t["function"]["name"] for t in kwargs["tools"]}
            return SimpleNamespace(content=None, tool_calls=[call("web_search", {"query": "tavily pricing"})])
        tool_result = json.loads(kwargs["messages"][-1]["content"])
        assert tool_result["results"][0]["n"] == 1
        return SimpleNamespace(content="1,000 free credits [WEB: 1].", tool_calls=None)

    client, _ = _client(script)

    async def fake_client(org_id):
        return client

    monkeypatch.setattr(ask_agent, "get_llm_client", fake_client)
    response = await ask_agent.run_ask_agent(
        question="search the web for tavily pricing",
        retrieval=RetrievalResult(chunks=[], experts=[], entities_found=[]),
        history=[], org_id="o", user_id="u",
    )
    assert response.web_search_used is True
    assert response.answer.startswith("**Web search used.**")
    assert response.web_results == [WebResult(
        title="Tavily pricing", url="https://tavily.com/pricing",
        snippet="The free plan includes 1,000 API credits per month.",
    )]


def test_action_answers_never_fall_back_to_the_web() -> None:
    from core.models import FileChange

    plain = QueryResponse(answer="?", sources=[], confidence="low", routed=True)
    assert ask_agent._web_fallback_candidate(plain)
    acted = plain.model_copy(update={"file_changes": [FileChange(file_id="f", name="a.csv", action="created")]})
    assert not ask_agent._web_fallback_candidate(acted)
    assert not ask_agent._web_fallback_candidate(plain.model_copy(update={"confidence": "medium"}))


async def test_explicit_web_request_says_when_web_search_is_off(monkeypatch) -> None:
    monkeypatch.setattr("core.config.get_settings", lambda: _settings(tavily_api_key=""))
    _low_confidence(monkeypatch)
    client, _ = _client(lambda step, kw: SimpleNamespace(
        content=json.dumps({"use_tools": False, "plan": []}), tool_calls=None
    ))

    async def fake_client(org_id):
        return client

    monkeypatch.setattr(ask_agent, "get_llm_client", fake_client)
    response = await ask_agent.run_ask_agent(
        question="search the web for the latest GDPR fines",
        retrieval=RetrievalResult(chunks=[], experts=[], entities_found=[]),
        history=[], org_id="o", user_id="u",
    )
    assert response.web_search_used is False
    assert "Web search is not turned on" in response.answer
