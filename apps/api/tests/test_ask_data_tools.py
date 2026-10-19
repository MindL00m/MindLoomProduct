"""Ask data lookups: live mailbox counts, indexed inventory, and project status."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest

from core.models import (
    OpenStatusResponse,
    StatusActionItem,
    StatusEvidence,
    StatusIssueItem,
    StatusProjectItem,
)
from loombot import ask_agent, data_tools


@pytest.mark.parametrize("question", [
    "How many emails did I get today?",
    "how many unread emails do I have",
    "What emails did I receive this week?",
    "What is the status of project tiramisu?",
    "How is the tiramisu launch going?",
    "Any blockers on the migration?",
    "What are the open action items?",
    "How many meetings did I have last week?",
])
def test_wants_data_detects_counts_and_status(question: str) -> None:
    assert ask_agent._wants_data(question)


@pytest.mark.parametrize("question", [
    "What is our pricing model?",
    "What is our email policy?",
    "Who owns the launch?",
])
def test_wants_data_ignores_plain_knowledge_questions(question: str) -> None:
    assert not ask_agent._wants_data(question)


@pytest.mark.parametrize("question", [
    "Tell me about project tiramisu",
    "Can you tell me how many emails I got today?",
    "What is the text of the contract?",
    "Ask whether the launch is on track",
    "Message history from the vendor?",
])
def test_questions_are_not_treated_as_messaging(question: str) -> None:
    assert not ask_agent._wants_messaging(question)


@pytest.mark.parametrize("question", [
    "Can you ask the CTO about pricing",
    "I need you to tell Priya the launch moved",
    "Let Priya know the demo moved to Friday",
    "Summarize the notes then email them to Alex",
    "Send the Q3 report to Priya",
    "Draft an email to the vendor about the delay",
])
def test_messaging_requests_are_detected(question: str) -> None:
    assert ask_agent._wants_messaging(question)


def _connect_mailboxes(monkeypatch, *, resources: list[str], connected: bool = True) -> None:
    from weaver import connection_setup, google_workspace, integrations, microsoft_teams

    async def fake_connection(org_id, user_id, provider):
        return SimpleNamespace(provider=provider) if connected else None

    async def fake_policy(org_id, user_id, provider):
        return SimpleNamespace(included_resources=json.dumps(resources))

    async def fake_google_token(org_id, user_id):
        return "google-token", "me@acme.com"

    async def fake_teams_token(org_id, user_id):
        return "graph-token", "me@acme.onmicrosoft.com"

    monkeypatch.setattr(integrations, "_get_connection", fake_connection)
    monkeypatch.setattr(connection_setup, "get_policy", fake_policy)
    monkeypatch.setattr(google_workspace, "_workspace_access_token", fake_google_token)
    monkeypatch.setattr(microsoft_teams, "_teams_token", fake_teams_token)


def _mock_http(monkeypatch, handler) -> list[httpx.Request]:
    requests: list[httpx.Request] = []
    real_client = httpx.AsyncClient

    def recording(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    monkeypatch.setattr(
        data_tools.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(recording), **kwargs),
    )
    return requests


def _gmail_metadata() -> dict:
    return {"payload": {"headers": [
        {"name": "Subject", "value": "Invoice 42"},
        {"name": "From", "value": "billing@vendor.com"},
        {"name": "Date", "value": "Sat, 17 Oct 2026 09:00:00 +0000"},
    ]}}


@pytest.mark.asyncio
async def test_count_emails_reads_gmail_inbox_totals(monkeypatch) -> None:
    _connect_mailboxes(monkeypatch, resources=["mailbox:gmail"])

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer google-token"
        if request.url.path.endswith("/labels/INBOX"):
            return httpx.Response(200, json={"messagesTotal": 1234, "messagesUnread": 12})
        if request.url.path.endswith("/messages"):
            return httpx.Response(200, json={"messages": [{"id": "m1"}]})
        return httpx.Response(200, json=_gmail_metadata())

    _mock_http(monkeypatch, handler)
    result = await data_tools.count_emails("org-1", "u-1", provider="gmail")
    mailbox = result["mailboxes"][0]
    assert (mailbox["count"], mailbox["unread"], mailbox["exact"]) == (1234, 12, True)
    assert mailbox["account"] == "me@acme.com"
    assert mailbox["recent"][0]["subject"] == "Invoice 42"


@pytest.mark.asyncio
async def test_count_emails_pages_through_gmail_for_a_date_range(monkeypatch) -> None:
    _connect_mailboxes(monkeypatch, resources=["mailbox:gmail"])

    def handler(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        if request.url.path.endswith("/messages") and params.get("maxResults") == "500":
            if params.get("pageToken") == "page-2":
                return httpx.Response(200, json={"messages": [{"id": f"b{i}"} for i in range(3)]})
            return httpx.Response(200, json={
                "messages": [{"id": f"a{i}"} for i in range(500)],
                "nextPageToken": "page-2",
            })
        if request.url.path.endswith("/messages"):
            return httpx.Response(200, json={"messages": []})
        raise AssertionError(f"unexpected request {request.url}")

    requests = _mock_http(monkeypatch, handler)
    result = await data_tools.count_emails(
        "org-1", "u-1", provider="gmail", start_date="2026-10-18", end_date="2026-10-18"
    )
    mailbox = result["mailboxes"][0]
    assert (mailbox["count"], mailbox["exact"]) == (503, True)
    start = int(datetime(2026, 10, 18, tzinfo=timezone.utc).timestamp())
    end = int(datetime(2026, 10, 19, tzinfo=timezone.utc).timestamp())
    assert requests[0].url.params["q"] == f"in:inbox after:{start} before:{end}"


@pytest.mark.asyncio
async def test_count_emails_uses_graph_count_for_outlook(monkeypatch) -> None:
    _connect_mailboxes(monkeypatch, resources=["mailbox:outlook"])

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["ConsistencyLevel"] == "eventual"
        assert "receivedDateTime ge 2026-10-12T00:00:00Z" in request.url.params["$filter"]
        assert "isRead eq false" in request.url.params["$filter"]
        return httpx.Response(200, json={
            "@odata.count": 41,
            "value": [{
                "subject": "Standup notes",
                "from": {"emailAddress": {"address": "lead@acme.com"}},
                "receivedDateTime": "2026-10-17T15:00:00Z",
            }],
        })

    _mock_http(monkeypatch, handler)
    result = await data_tools.count_emails(
        "org-1", "u-1", provider="outlook", start_date="2026-10-12", unread_only=True
    )
    mailbox = result["mailboxes"][0]
    assert mailbox["count"] == 41
    assert mailbox["recent"][0]["from"] == "lead@acme.com"


@pytest.mark.asyncio
async def test_count_emails_explains_missing_or_unapproved_mailboxes(monkeypatch) -> None:
    _connect_mailboxes(monkeypatch, resources=["drive:my-drive"])

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not read a mailbox the user did not approve")

    _mock_http(monkeypatch, handler)
    result = await data_tools.count_emails("org-1", "u-1", provider="gmail")
    assert "not an approved source" in result["error"]

    _connect_mailboxes(monkeypatch, resources=[], connected=False)
    result = await data_tools.count_emails("org-1", "u-1")
    assert "Google Workspace is not connected" in result["error"]
    assert "Microsoft 365 is not connected" in result["error"]


class _Record:
    def __init__(self, row: dict) -> None:
        self._row = row

    def data(self) -> dict:
        return self._row


class _Result:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def __aiter__(self):
        async def rows():
            for row in self._rows:
                yield _Record(row)
        return rows()


class _Neo4jDateTime:
    def __init__(self, value: datetime) -> None:
        self._value = value

    def to_native(self) -> datetime:
        return self._value


def _fake_driver(rows_for: callable, calls: list[dict]):
    class Tx:
        async def run(self, query: str, **params):
            calls.append(params)
            return _Result(rows_for(query))

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def execute_read(self, fn):
            return await fn(Tx())

    return SimpleNamespace(session=lambda: Session())


@pytest.mark.asyncio
async def test_project_status_combines_board_and_graph(monkeypatch) -> None:
    from lens import status_board

    update = StatusEvidence(
        chunk_id="c-1",
        summary="Tiramisu beta slipped to November",
        source_label="Weekly sync",
        knowledge_type="status_update",
        end_time=datetime(2026, 10, 16, tzinfo=timezone.utc),
    )

    async def fake_board(org_id, user_id):
        return OpenStatusResponse(
            projects=[
                StatusProjectItem(
                    entity_id="e-1", name="Tiramisu", current_status="Beta slipped",
                    recent_updates=[update],
                ),
                StatusProjectItem(entity_id="e-2", name="Pavlova"),
            ],
            issues=[StatusIssueItem(
                issue_id="i-1", title="Payments API blocked", kind="problem_report",
                project="Tiramisu", evidence=[update],
            )],
            action_items=[StatusActionItem(
                action_item_id="a-1", text="Book QA", assignee="Priya", project="Pavlova",
            )],
        )

    async def fake_tokens(org_id, user_id):
        return ["org:org-1", "user:u-1"]

    calls: list[dict] = []
    graph_row = {
        "entity_id": "e-1",
        "name": "Tiramisu",
        "work_status": "open",
        "closed_at": None,
        "last_signal_at": _Neo4jDateTime(datetime(2026, 10, 16, tzinfo=timezone.utc)),
        "recent_mentions": [{"chunk_id": "c-1", "summary": "Beta slipped", "date": None}],
    }
    monkeypatch.setattr(status_board, "get_open_status", fake_board)
    monkeypatch.setattr(data_tools, "get_user_access_tokens", fake_tokens)
    monkeypatch.setattr(data_tools, "get_neo4j_driver", lambda: _fake_driver(lambda q: [graph_row], calls))

    result = await data_tools.project_status("org-1", "u-1", project="Project Tiramisu")
    assert result["found"] is True
    assert calls[0]["needle"] == "tiramisu"
    assert calls[0]["access_tokens"] == ["org:org-1", "user:u-1"]
    board = result["status_board"]
    assert [p["name"] for p in board["projects"]] == ["Tiramisu"]
    assert board["projects"][0]["recent_updates"][0]["chunk_id"] == "c-1"
    assert [i["title"] for i in board["open_issues"]] == ["Payments API blocked"]
    assert board["open_action_items"] == []
    assert result["knowledge_graph_projects"][0]["last_signal_at"].startswith("2026-10-16")
    json.dumps(result)


@pytest.mark.asyncio
async def test_project_status_reports_unknown_project(monkeypatch) -> None:
    from lens import status_board

    async def empty_board(org_id, user_id):
        return OpenStatusResponse()

    async def fake_tokens(org_id, user_id):
        return []

    monkeypatch.setattr(status_board, "get_open_status", empty_board)
    monkeypatch.setattr(data_tools, "get_user_access_tokens", fake_tokens)
    monkeypatch.setattr(data_tools, "get_neo4j_driver", lambda: _fake_driver(lambda q: [], []))

    result = await data_tools.project_status("org-1", "u-1", project="the tiramisu project")
    assert result == {
        "found": False,
        "project": "tiramisu",
        "note": (
            "No project matching 'tiramisu' is on the Status board or in the "
            "knowledge graph you can see."
        ),
    }


@pytest.mark.asyncio
async def test_knowledge_inventory_filters_by_source_alias(monkeypatch) -> None:
    async def fake_tokens(org_id, user_id):
        return ["org:org-1"]

    def rows_for(query: str) -> list[dict]:
        if "count(d)" in query:
            return [{"source": "gmail", "application": "", "documents": 25, "latest": None}]
        return [{"title": "Invoice 42", "source": "gmail", "date": None, "url": None}]

    calls: list[dict] = []
    monkeypatch.setattr(data_tools, "get_user_access_tokens", fake_tokens)
    monkeypatch.setattr(data_tools, "get_neo4j_driver", lambda: _fake_driver(rows_for, calls))

    result = await data_tools.knowledge_inventory(
        "org-1", "u-1", source="Email", start_date="2026-10-01", end_date="2026-10-18"
    )
    assert calls[0]["sources"] == ["gmail", "outlook_email"]
    assert calls[0]["start"] == "2026-10-01T00:00:00+00:00"
    assert calls[0]["end"] == "2026-10-19T00:00:00+00:00"
    assert result["total_documents"] == 25
    assert result["most_recent"][0]["title"] == "Invoice 42"


@pytest.mark.asyncio
async def test_tool_failures_become_tool_results(monkeypatch) -> None:
    async def broken(*_args, **_kwargs):
        raise RuntimeError("neo4j is down")

    monkeypatch.setattr(data_tools, "project_status", broken)
    result, *_ = await ask_agent._safe_run_tool(
        name="project_status",
        arguments={"project": "tiramisu"},
        org_id="org-1",
        user_id="u-1",
        people_cache={},
    )
    assert "project_status failed" in result["error"]


@pytest.mark.asyncio
async def test_status_question_runs_tool_and_hydrates_cited_sources(monkeypatch) -> None:
    from core.models import ChunkResult, QueryResponse, RetrievalResult

    status_args: list[dict] = []

    async def fake_status(org_id, user_id, *, project=None):
        status_args.append({"project": project})
        return {"found": True, "status_board": {"projects": [{
            "name": "Tiramisu",
            "recent_updates": [{"chunk_id": "c-9", "summary": "Beta slipped"}],
        }]}}

    async def base_answer(*_args, **_kwargs):
        return QueryResponse(answer="From context.", sources=[], confidence="low", routed=False)

    async def not_connected(*_args, **_kwargs):
        return False

    async def no_citations(*_args, **_kwargs):
        return None

    async def fake_tokens(*_args, **_kwargs):
        return ["org:org-1"]

    loaded: list[list[str]] = []

    async def fake_load(chunk_ids, org_id, access_tokens=None):
        loaded.append(list(chunk_ids))
        moment = datetime(2026, 10, 16, tzinfo=timezone.utc)
        return [ChunkResult(
            chunk_id="c-9", raw_text="Beta slipped", summary="Beta slipped", speakers=[],
            start_time=moment, end_time=moment, knowledge_type="status_update",
            confidence="high", similarity_score=1.0,
        )]

    def tool_call(name: str, arguments: dict):
        return SimpleNamespace(
            id="call-1",
            function=SimpleNamespace(name=name, arguments=json.dumps(arguments)),
        )

    replies = [
        SimpleNamespace(content=None, tool_calls=[tool_call("project_status", {"project": "tiramisu"})]),
        SimpleNamespace(content="Tiramisu's beta slipped. [SOURCE: c-9]", tool_calls=None),
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
    monkeypatch.setattr(data_tools, "project_status", fake_status)
    monkeypatch.setattr("weaver.integrations.has_google_workspace_connection", not_connected)
    monkeypatch.setattr("loombot.answerer._attach_citations", no_citations)
    monkeypatch.setattr("keeper.auth.get_user_access_tokens", fake_tokens)
    monkeypatch.setattr("brain.retrieval._load_chunks_by_ids", fake_load)

    response = await ask_agent.run_ask_agent(
        question="What is the status of project tiramisu?",
        retrieval=RetrievalResult(chunks=[], experts=[], entities_found=[]),
        history=[],
        org_id="org-1",
        user_id="u-1",
    )
    assert status_args == [{"project": "tiramisu"}]
    assert sent[0]["tool_choice"] == "required"
    assert "Today is" in sent[0]["messages"][0]["content"]
    assert json.loads(sent[1]["messages"][-1]["content"])["found"] is True
    assert response.answer == "Tiramisu's beta slipped. [SOURCE: c-9]"
    assert [source.chunk_id for source in response.sources] == ["c-9"]
    assert loaded == [["c-9"]]
    assert response.confidence == "high"


def test_status_board_reads_neo4j_datetimes() -> None:
    from lens.status_board import _as_datetime

    moment = datetime(2026, 10, 16, tzinfo=timezone.utc)
    assert _as_datetime(_Neo4jDateTime(moment)) == moment
    assert _as_datetime(moment) == moment
    assert _as_datetime(None) is None
