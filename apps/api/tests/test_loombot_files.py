"""LoomBot Files: CSV validation and Ask's file tools."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from core.models import ChatMessage, RetrievalResult
from loombot import ask_agent
from loombot import files as files_service


def test_normalize_name_appends_csv_and_rejects_paths() -> None:
    assert files_service.normalize_name("  Q3   budget ") == "Q3 budget.csv"
    assert files_service.normalize_name("sales.CSV") == "sales.CSV"
    with pytest.raises(HTTPException):
        files_service.normalize_name("../etc/passwd")
    with pytest.raises(HTTPException):
        files_service.normalize_name("   ")


def test_normalize_csv_canonicalises_and_counts() -> None:
    body, rows, columns = files_service.normalize_csv(
        'name,notes\r\nAda,"likes, commas"\r\n\r\nBob\r\n'
    )
    assert body == 'name,notes\nAda,"likes, commas"\nBob,\n'
    assert (rows, columns) == (2, 2)
    assert files_service.normalize_csv("") == ("", 0, 0)


def test_normalize_csv_rejects_malformed_and_oversized() -> None:
    with pytest.raises(HTTPException):
        files_service.normalize_csv('a,b\n"unterminated,1\n')
    with pytest.raises(HTTPException) as too_big:
        files_service.normalize_csv("a\n" + "x" * (files_service.MAX_FILE_BYTES + 1))
    assert too_big.value.status_code == 413


@pytest.mark.parametrize(
    "question",
    [
        "Create a CSV of our vendors",
        "Add a row to budget.csv for travel",
        "Show me the budget file",
        "What's in my files?",
    ],
)
def test_wants_files_detects_file_requests(question: str) -> None:
    assert ask_agent._wants_files(question, ["budget.csv"])


@pytest.mark.parametrize(
    "question",
    ["What is our budget policy?", "Who owns the data pipeline?"],
)
def test_wants_files_ignores_knowledge_questions(question: str) -> None:
    assert not ask_agent._wants_files(question, ["budget.csv"])


def test_wants_files_follows_up_on_a_file_just_edited() -> None:
    history = [
        ChatMessage(role="user", content="Add travel to budget.csv"),
        ChatMessage(role="assistant", content="I added a travel row to budget.csv."),
    ]
    assert ask_agent._wants_files("now sort it by amount", ["budget.csv"], history)
    assert not ask_agent._wants_files("now sort it by amount", ["budget.csv"], [])


class _FakeFiles:
    """In-memory stand-in for the files service used by Ask's tools."""

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}

    def _record(self, file_id: str, name: str, content: str) -> dict:
        body, rows, columns = files_service.normalize_csv(content)
        record = {
            "file_id": file_id, "name": files_service.normalize_name(name),
            "content": body, "row_count": rows, "column_count": columns,
            "updated_at": "2026-10-20T00:00:00Z",
        }
        self.rows[file_id] = record
        return record

    async def list_files(self, org_id, user_id):
        return [dict(r) for r in self.rows.values()]

    async def find_file_by_name(self, org_id, user_id, name):
        wanted = name.lower() if name.lower().endswith(".csv") else f"{name.lower()}.csv"
        return next((dict(r) for r in self.rows.values() if r["name"].lower() == wanted), None)

    async def create_file(self, org_id, user_id, name, content="", *, edited_by="user"):
        return dict(self._record(f"f{len(self.rows) + 1}", name, content))

    async def update_file(self, org_id, user_id, file_id, *, content=None, name=None, edited_by="user"):
        current = self.rows[file_id]
        return dict(self._record(file_id, name or current["name"], content or current["content"]))


@pytest.fixture
def fake_files(monkeypatch) -> _FakeFiles:
    fake = _FakeFiles()
    for attr in ("list_files", "find_file_by_name", "create_file", "update_file"):
        monkeypatch.setattr(files_service, attr, getattr(fake, attr))
    return fake


async def _tool(name: str, args: dict, state: ask_agent._FileToolState):
    result, *_ = await ask_agent._safe_run_tool(
        name=name, arguments=args, org_id="org", user_id="u",
        people_cache={}, file_state=state,
    )
    return result


async def test_write_requires_reading_an_existing_file_first(fake_files) -> None:
    fake_files._record("f1", "budget.csv", "item,amount\nrent,100\n")
    state = ask_agent._FileToolState()

    blocked = await _tool("write_csv_file", {"name": "budget", "content": "item\n"}, state)
    assert "read_csv_file" in blocked["error"]
    assert fake_files.rows["f1"]["content"] == "item,amount\nrent,100\n"

    read = await _tool("read_csv_file", {"name": "budget.csv"}, state)
    assert read["content"] == "item,amount\nrent,100\n"
    saved = await _tool(
        "write_csv_file",
        {"name": "budget.csv", "content": "item,amount\nrent,100\ntravel,40\n", "summary": "Added travel."},
        state,
    )
    assert saved["saved"] is True and saved["rows"] == 2
    assert [(c.name, c.action, c.summary) for c in state.changes] == [
        ("budget.csv", "updated", "Added travel.")
    ]


async def test_write_creates_new_file_and_collapses_repeat_writes(fake_files) -> None:
    state = ask_agent._FileToolState()
    await _tool("write_csv_file", {"name": "vendors", "content": "vendor\nAcme\n"}, state)
    await _tool("write_csv_file", {"name": "vendors.csv", "content": "vendor\nAcme\nGlobex\n"}, state)
    assert len(state.changes) == 1
    assert (state.changes[0].name, state.changes[0].action, state.changes[0].row_count) == (
        "vendors.csv", "created", 2,
    )


async def test_large_files_are_read_only_for_ask(fake_files, monkeypatch) -> None:
    monkeypatch.setattr(files_service, "ASK_MAX_FILE_CHARS", 20)
    fake_files._record("f1", "big.csv", "col\n" + "\n".join(str(i) for i in range(50)))
    state = ask_agent._FileToolState()
    read = await _tool("read_csv_file", {"name": "big.csv"}, state)
    assert read["truncated"] is True
    write = await _tool("write_csv_file", {"name": "big.csv", "content": "col\n1\n"}, state)
    assert "too large" in write["error"]


async def test_ask_agent_edits_a_file_and_reports_the_change(fake_files, monkeypatch) -> None:
    fake_files._record("f1", "budget.csv", "item,amount\nrent,100\n")

    async def base_answer(*args, **kwargs):
        from core.models import QueryResponse
        return QueryResponse(answer="base", sources=[], confidence="low", routed=False)

    async def not_connected(*args):
        return False

    def call(call_id: str, name: str, arguments: dict):
        return SimpleNamespace(
            id=call_id, function=SimpleNamespace(name=name, arguments=json.dumps(arguments))
        )

    replies = [
        SimpleNamespace(content=None, tool_calls=[call("c1", "read_csv_file", {"name": "budget.csv"})]),
        SimpleNamespace(content=None, tool_calls=[call("c2", "write_csv_file", {
            "name": "budget.csv",
            "content": "item,amount\nrent,100\ntravel,40\n",
            "summary": "Added a travel row.",
        })]),
        SimpleNamespace(content="I added a **travel** row to budget.csv.", tool_calls=None),
    ]
    sent: list[dict] = []

    async def create(**kwargs):
        sent.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=replies.pop(0))])

    client = SimpleNamespace(
        configuration=SimpleNamespace(supports_tools=True),
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
    )

    async def fake_client(org_id):
        return client

    monkeypatch.setattr(ask_agent, "generate_answer", base_answer)
    monkeypatch.setattr(ask_agent, "get_llm_client", fake_client)
    monkeypatch.setattr("weaver.integrations.has_google_workspace_connection", not_connected)

    response = await ask_agent.run_ask_agent(
        question="Add a travel row with 40 to budget.csv",
        retrieval=RetrievalResult(chunks=[], experts=[], entities_found=[]),
        history=[],
        org_id="org",
        user_id="u",
    )

    assert "budget.csv (1 rows x 2 columns)" in sent[0]["messages"][0]["content"]
    assert {t["function"]["name"] for t in sent[0]["tools"]} >= ask_agent._FILE_TOOLS
    assert fake_files.rows["f1"]["content"].endswith("travel,40\n")
    assert response.answer == "I added a **travel** row to budget.csv."
    assert [(c.name, c.action) for c in response.file_changes] == [("budget.csv", "updated")]
    assert response.confidence == "high"
