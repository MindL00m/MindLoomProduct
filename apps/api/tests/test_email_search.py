"""Live Gmail search for Ask, and pulling email details into a Files CSV."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest

from core.models import QueryResponse, RetrievalResult
from loombot import ask_agent, data_tools
from loombot import files as files_service


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def _connect_gmail(monkeypatch, *, resources=("mailbox:gmail",)) -> None:
    from weaver import connection_setup, google_workspace, integrations

    async def fake_connection(org_id, user_id, provider):
        return SimpleNamespace(provider=provider)

    async def fake_policy(org_id, user_id, provider):
        return SimpleNamespace(included_resources=json.dumps(list(resources)))

    async def fake_token(org_id, user_id):
        return "google-token", "me@acme.com"

    monkeypatch.setattr(integrations, "_get_connection", fake_connection)
    monkeypatch.setattr(connection_setup, "get_policy", fake_policy)
    monkeypatch.setattr(google_workspace, "_workspace_access_token", fake_token)


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


_MESSAGES = {
    "older": {
        "id": "older", "internalDate": str(int(datetime(2026, 10, 19, tzinfo=timezone.utc).timestamp() * 1000)),
        "snippet": "Your code is 111111", "labelIds": ["INBOX"],
        "payload": {
            "mimeType": "text/html",
            "headers": [
                {"name": "Subject", "value": "Your Squarespace verification code"},
                {"name": "From", "value": "Squarespace <no-reply@squarespace.com>"},
            ],
            "body": {"data": _b64(
                "<html><head><style>.x{color:red}</style></head><body>"
                "<p>Your verification code is</p><p><b>111111</b></p>"
                "<script>alert(1)</script></body></html>"
            )},
        },
    },
    "newer": {
        "id": "newer", "internalDate": str(int(datetime(2026, 10, 20, tzinfo=timezone.utc).timestamp() * 1000)),
        "snippet": "Your code is 222222", "labelIds": ["INBOX", "UNREAD"],
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": [
                {"name": "Subject", "value": "Your Squarespace verification code"},
                {"name": "From", "value": "Squarespace <no-reply@squarespace.com>"},
            ],
            "parts": [
                {"mimeType": "text/plain", "body": {"data": _b64("Your verification code is 222222")}},
                {"mimeType": "text/html", "body": {"data": _b64("<p>ignored html</p>")}},
            ],
        },
    },
}


def _gmail_handler(request: httpx.Request) -> httpx.Response:
    assert request.headers["Authorization"] == "Bearer google-token"
    if request.url.path.endswith("/messages"):
        # Deliberately oldest first: the tool must sort newest first itself.
        return httpx.Response(200, json={
            "messages": [{"id": "older"}, {"id": "newer"}], "resultSizeEstimate": 2,
        })
    message_id = request.url.path.rsplit("/", 1)[-1]
    return httpx.Response(200, json=_MESSAGES[message_id])


async def test_search_emails_builds_query_and_reads_bodies_newest_first(monkeypatch) -> None:
    _connect_gmail(monkeypatch)
    requests = _mock_http(monkeypatch, _gmail_handler)

    result = await data_tools.search_emails(
        "org-1", "u-1", sender="squarespace", query="verification code",
        newer_than_days=3, include_body=True,
    )

    query = requests[0].url.params["q"]
    assert query.startswith("-in:sent -in:drafts -in:chats from:(squarespace) after:")
    assert query.endswith("verification code")
    assert [m["id"] for m in result["messages"]] == ["newer", "older"]
    newer, older = result["messages"]
    assert newer["body"] == "Your verification code is 222222"
    assert newer["unread"] is True
    # HTML-only mail is converted to visible text; scripts and styles are dropped.
    assert older["body"] == "Your verification code is\n\n111111"
    assert "alert" not in older["body"] and "color" not in older["body"]
    assert result["account"] == "me@acme.com"
    assert "untrusted" in result["note"]
    assert all(r.url.params.get("format") == "full" for r in requests[1:])


async def test_search_emails_can_skip_bodies(monkeypatch) -> None:
    _connect_gmail(monkeypatch)
    requests = _mock_http(monkeypatch, _gmail_handler)

    result = await data_tools.search_emails("org-1", "u-1", max_results=1, include_body=False)

    assert requests[0].url.params["maxResults"] == "1"
    assert "body" not in result["messages"][0]
    assert requests[1].url.params["format"] == "metadata"


async def test_search_emails_explains_unapproved_mailbox(monkeypatch) -> None:
    _connect_gmail(monkeypatch, resources=())
    result = await data_tools.search_emails("org-1", "u-1", sender="squarespace")
    assert "not an approved source" in result["error"]


async def test_search_emails_rejects_bad_ranges() -> None:
    assert "error" in await data_tools.search_emails("o", "u", newer_than_days=0)
    assert "error" in await data_tools.search_emails(
        "o", "u", start_date="2026-10-10", end_date="2026-10-01"
    )


@pytest.mark.parametrize("question", [
    "What's my most recent email?",
    "Who emailed me last?",
    "Find emails from Priya about the launch",
    "make a file using all the verification codes i've received from squarespace in the last three days",
    "What did Amazon send me?",
    "Pull the tracking numbers from my email",
])
def test_email_lookups_use_the_data_tools(question: str) -> None:
    assert ask_agent._wants_data(question)


@pytest.mark.parametrize("question", [
    "What is our email policy?",
    "Is there a new email policy?",
    "What's my email address?",
    "I got an error when deploying, what does it mean?",
])
def test_email_lookup_detection_ignores_other_questions(question: str) -> None:
    assert not ask_agent._wants_data(question)


async def test_ask_turns_squarespace_codes_into_a_file(monkeypatch) -> None:
    _connect_gmail(monkeypatch)
    _mock_http(monkeypatch, _gmail_handler)
    saved: dict[str, str] = {}

    async def no_files(org_id, user_id):
        return []

    async def find_none(org_id, user_id, name):
        return None

    async def create(org_id, user_id, name, content="", *, edited_by="user"):
        body, rows, columns = files_service.normalize_csv(content)
        saved.update(name=files_service.normalize_name(name), content=body, edited_by=edited_by)
        return {"file_id": "f1", "name": saved["name"], "content": body,
                "row_count": rows, "column_count": columns}

    monkeypatch.setattr(files_service, "list_files", no_files)
    monkeypatch.setattr(files_service, "find_file_by_name", find_none)
    monkeypatch.setattr(files_service, "create_file", create)

    async def base_answer(*args, **kwargs):
        return QueryResponse(answer="base", sources=[], confidence="low", routed=False)

    async def not_connected(*args):
        return False

    def call(call_id, name, arguments):
        return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=json.dumps(arguments)))

    sent: list[dict] = []

    async def create_completion(**kwargs):
        sent.append(kwargs)
        step = len(sent)
        if step == 1:
            message = SimpleNamespace(content=None, tool_calls=[call("c1", "search_emails", {
                "sender": "squarespace", "query": "verification code",
                "newer_than_days": 3, "include_body": True, "max_results": 50,
            })])
        elif step == 2:
            found = json.loads(kwargs["messages"][-1]["content"])
            rows = ["date,sender,subject,code"] + [
                f"{m['date'][:10]},Squarespace,{m['subject']},{m['body'].split()[-1]}"
                for m in found["messages"]
            ]
            message = SimpleNamespace(content=None, tool_calls=[call("c2", "write_csv_file", {
                "name": "squarespace-codes.csv", "content": "\n".join(rows) + "\n",
                "summary": "Saved 2 Squarespace verification codes.",
            })])
        else:
            message = SimpleNamespace(content="Saved **2 codes** to squarespace-codes.csv.", tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    client = SimpleNamespace(
        configuration=SimpleNamespace(supports_tools=True),
        chat=SimpleNamespace(completions=SimpleNamespace(create=create_completion)),
    )

    async def fake_client(org_id):
        return client

    monkeypatch.setattr(ask_agent, "generate_answer", base_answer)
    monkeypatch.setattr(ask_agent, "get_llm_client", fake_client)
    monkeypatch.setattr("weaver.integrations.has_google_workspace_connection", not_connected)

    response = await ask_agent.run_ask_agent(
        question="make a file using all the verification codes i've received from squarespace in the last three days",
        retrieval=RetrievalResult(chunks=[], experts=[], entities_found=[]),
        history=[],
        org_id="org-1",
        user_id="u-1",
    )

    tool_names = {t["function"]["name"] for t in sent[0]["tools"]}
    assert {"search_emails", "write_csv_file"} <= tool_names
    assert "search_emails" in sent[0]["messages"][0]["content"]
    assert saved["content"].splitlines() == [
        "date,sender,subject,code",
        "2026-10-20,Squarespace,Your Squarespace verification code,222222",
        "2026-10-19,Squarespace,Your Squarespace verification code,111111",
    ]
    assert saved["edited_by"] == "loombot"
    assert [(c.name, c.action) for c in response.file_changes] == [("squarespace-codes.csv", "created")]
    assert response.answer == "Saved **2 codes** to squarespace-codes.csv."


async def test_search_emails_returns_bodies_by_default(monkeypatch) -> None:
    _connect_gmail(monkeypatch)
    requests = _mock_http(monkeypatch, _gmail_handler)

    result = await data_tools.search_emails("org-1", "u-1", sender="slack", max_results=1)

    assert requests[1].url.params["format"] == "full"
    assert result["messages"][0]["body"]


async def test_ask_reads_bodies_when_the_model_omits_include_body(monkeypatch) -> None:
    seen: list[dict] = []

    async def fake_search(org_id, user_id, **kwargs):
        seen.append(kwargs)
        return {"messages": []}

    monkeypatch.setattr(data_tools, "search_emails", fake_search)
    await ask_agent._safe_run_tool(
        name="search_emails", arguments={"sender": "slack", "max_results": 1},
        org_id="o", user_id="u", people_cache={},
    )
    await ask_agent._safe_run_tool(
        name="search_emails", arguments={"sender": "slack", "include_body": False},
        org_id="o", user_id="u", people_cache={},
    )
    assert [call["include_body"] for call in seen] == [True, False]
