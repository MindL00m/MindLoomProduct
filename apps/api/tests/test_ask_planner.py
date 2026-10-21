"""The planning layer routes keyword-less requests to tools, and plans multi-step work."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from core.models import QueryResponse, RetrievalResult
from loombot import ask_agent, data_tools
from loombot import files as files_service

QUESTION = "update the prices that come from amey05agarwal@gmail.com"


def _call(call_id: str, name: str, arguments: dict) -> SimpleNamespace:
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=json.dumps(arguments)))


def _client(script):
    sent: list[dict] = []

    async def create(**kwargs):
        sent.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=script(len(sent), kwargs))])

    client = SimpleNamespace(
        configuration=SimpleNamespace(supports_tools=True),
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
    )
    return client, sent


@pytest.fixture
def base(monkeypatch):
    async def base_answer(*args, **kwargs):
        return QueryResponse(answer="knowledge answer", sources=[], confidence="medium", routed=False)

    async def not_connected(*args):
        return False

    monkeypatch.setattr(ask_agent, "generate_answer", base_answer)
    monkeypatch.setattr("weaver.integrations.has_google_workspace_connection", not_connected)


async def test_planner_routes_price_update_through_files_and_email(monkeypatch, base) -> None:
    assert not any((
        ask_agent._wants_messaging(QUESTION), ask_agent._wants_data(QUESTION),
        ask_agent._wants_files(QUESTION, ["supplier price list.csv"]),
    )), "this test covers requests no keyword rule catches"

    store = {"content": "item,price\nflour,4.00\nsugar,3.00\n"}

    async def list_files(org_id, user_id):
        return [{"file_id": "f1", "name": "supplier price list.csv", "row_count": 2,
                 "column_count": 2, "updated_at": "2026-10-20", "monitor_enabled": False}]

    async def find(org_id, user_id, name):
        if name.lower().startswith("supplier price list"):
            return {"file_id": "f1", "name": "supplier price list.csv", "content": store["content"],
                    "row_count": 2, "column_count": 2, "monitor_enabled": False}
        return None

    async def update(org_id, user_id, file_id, *, content=None, name=None, edited_by="user"):
        body, rows, columns = files_service.normalize_csv(content)
        store["content"] = body
        return {"file_id": file_id, "name": "supplier price list.csv", "content": body,
                "row_count": rows, "column_count": columns, "monitor_enabled": False}

    searched: list[dict] = []

    async def search(org_id, user_id, **kwargs):
        searched.append(kwargs)
        return {"messages": [{"id": "m1", "date": "2026-10-21", "from": "Amey <amey05agarwal@gmail.com>",
                              "subject": "New prices", "body": "Flour is now 4.50 and sugar 3.20."}]}

    monkeypatch.setattr(files_service, "list_files", list_files)
    monkeypatch.setattr(files_service, "find_file_by_name", find)
    monkeypatch.setattr(files_service, "update_file", update)
    monkeypatch.setattr(data_tools, "search_emails", search)

    def script(step, kwargs):
        if step == 1:  # planner
            assert "supplier price list.csv" in kwargs["messages"][1]["content"]
            return SimpleNamespace(content=json.dumps({"use_tools": True, "plan": [
                "read_csv_file supplier price list.csv",
                "search_emails sender amey05agarwal@gmail.com",
                "write_csv_file with the new prices",
            ]}), tool_calls=None)
        if step == 2:
            system = kwargs["messages"][0]["content"]
            assert "Suggested plan" in system and "search_emails sender amey05agarwal" in system
            assert kwargs["tool_choice"] == "required"
            return SimpleNamespace(content=None, tool_calls=[
                _call("c1", "read_csv_file", {"name": "supplier price list"})])
        if step == 3:
            return SimpleNamespace(content=None, tool_calls=[
                _call("c2", "search_emails", {"sender": "amey05agarwal@gmail.com", "max_results": 5})])
        if step == 4:
            return SimpleNamespace(content=None, tool_calls=[_call("c3", "write_csv_file", {
                "name": "supplier price list.csv",
                "content": "item,price\nflour,4.50\nsugar,3.20\n",
                "summary": "Updated flour and sugar prices from Amey's email.",
            })])
        return SimpleNamespace(content="Updated flour to 4.50 and sugar to 3.20.", tool_calls=None)

    client, sent = _client(script)

    async def fake_client(org_id):
        return client

    monkeypatch.setattr(ask_agent, "get_llm_client", fake_client)

    response = await ask_agent.run_ask_agent(
        question=QUESTION, retrieval=RetrievalResult(chunks=[], experts=[], entities_found=[]),
        history=[], org_id="o", user_id="u",
    )

    assert searched[0]["sender"] == "amey05agarwal@gmail.com" and searched[0]["include_body"] is True
    assert store["content"] == "item,price\nflour,4.50\nsugar,3.20\n"
    assert [(c.name, c.action) for c in response.file_changes] == [("supplier price list.csv", "updated")]
    assert response.answer == "Updated flour to 4.50 and sugar to 3.20."
    assert len(sent) == 5


async def test_planner_saying_no_tools_keeps_the_knowledge_answer(monkeypatch, base) -> None:
    def script(step, kwargs):
        return SimpleNamespace(content='{"use_tools": false, "plan": []}', tool_calls=None)

    client, sent = _client(script)

    async def fake_client(org_id):
        return client

    async def no_files(org_id, user_id):
        return []

    monkeypatch.setattr(ask_agent, "get_llm_client", fake_client)
    monkeypatch.setattr(ask_agent, "_user_files", no_files)
    response = await ask_agent.run_ask_agent(
        question="Who owns the data pipeline?",
        retrieval=RetrievalResult(chunks=[], experts=[], entities_found=[]),
        history=[], org_id="o", user_id="u",
    )
    assert response.answer == "knowledge answer" and len(sent) == 1


async def test_planner_failure_falls_back_to_the_knowledge_answer(monkeypatch, base) -> None:
    async def broken_client(org_id):
        raise RuntimeError("provider down")

    async def no_files(org_id, user_id):
        return []

    monkeypatch.setattr(ask_agent, "get_llm_client", broken_client)
    monkeypatch.setattr(ask_agent, "_user_files", no_files)
    response = await ask_agent.run_ask_agent(
        question="Who owns the data pipeline?",
        retrieval=RetrievalResult(chunks=[], experts=[], entities_found=[]),
        history=[], org_id="o", user_id="u",
    )
    assert response.answer == "knowledge answer"


async def test_plan_parsing_is_defensive() -> None:
    def script(step, kwargs):
        return SimpleNamespace(content='Sure! {"use_tools": "yes", "plan": ["x"]}', tool_calls=None)

    client, _ = _client(script)
    decision = await ask_agent._plan_request(client, "q", [], [])
    assert decision == {"use_tools": False, "plan": []}  # only a real true counts
