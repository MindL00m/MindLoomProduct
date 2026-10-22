"""Ask's file tools across file types, and document-update routing."""

from __future__ import annotations

import pytest

from loombot import ask_agent
from loombot import document_update
from loombot import files as files_service

DOCX = {
    "file_id": "d1", "name": "Q3 report.docx", "kind": "docx", "content": "",
    "row_count": 3, "column_count": 1, "monitor_enabled": False,
}


@pytest.fixture
def docs(monkeypatch):
    created: list[dict] = []

    async def find(org_id, user_id, name):
        lowered = name.lower()
        if lowered in {"q3 report", "q3 report.docx"}:
            return dict(DOCX)
        return None

    async def read_text(org_id, user_id, file):
        return "# Q3 Status Report\n\nRevenue was $1.2M."

    async def create(org_id, user_id, name, content="", *, edited_by="user", kind="csv", based_on=None):
        created.append({"name": name, "kind": kind})
        body, rows, columns = files_service.normalize_csv(content)
        return {"file_id": "c1", "name": files_service.normalize_name(name), "content": body,
                "row_count": rows, "column_count": columns, "monitor_enabled": False}

    monkeypatch.setattr(files_service, "find_file_by_name", find)
    monkeypatch.setattr(files_service, "read_text_for_ask", read_text)
    monkeypatch.setattr(files_service, "create_file", create)
    return created


async def _tool(name: str, args: dict, state=None):
    result, *_ = await ask_agent._safe_run_tool(
        name=name, arguments=args, org_id="o", user_id="u", people_cache={},
        file_state=state or ask_agent._FileToolState(),
    )
    return result


async def test_read_file_reads_word_documents(docs) -> None:
    result = await _tool("read_file", {"name": "Q3 report"})
    assert result["type"] == "docx" and "Revenue was $1.2M" in result["content"]
    assert "create_updated_document" in result["note"]
    # The old tool name still works.
    assert (await _tool("read_csv_file", {"name": "Q3 report.docx"}))["type"] == "docx"


async def test_write_csv_never_overwrites_other_file_types(docs) -> None:
    blocked = await _tool("write_csv_file", {"name": "Q3 report.docx", "content": "a\n1\n"})
    assert "create_updated_document" in blocked["error"]
    # A bare name that matched the .docx creates a separate CSV instead.
    saved = await _tool("write_csv_file", {"name": "Q3 report", "content": "a\n1\n", "monitor": "no"})
    assert saved["saved"] is True and docs == [{"name": "Q3 report", "kind": "csv"}]


async def test_create_updated_document_reports_a_new_file(docs, monkeypatch) -> None:
    calls: list[tuple] = []

    async def fake_update(org_id, user_id, file_id, instructions=None):
        calls.append((file_id, instructions))
        return {
            "changed": True,
            "file": {"file_id": "n1", "name": "Q3 report (updated 2026-10-22).docx", "kind": "docx",
                     "row_count": 3, "column_count": 1},
            "based_on": "Q3 report.docx", "summary": "Revenue refreshed.",
            "changes": [{"before": "Revenue was $1.2M.", "after": "Revenue was $1.5M.",
                         "reason": "Finance update", "sources": ["Finance update (2026-10-01)"]}],
            "warnings": [],
        }

    monkeypatch.setattr(document_update, "create_updated_version", fake_update)
    state = ask_agent._FileToolState()
    result = await _tool("create_updated_document", {"name": "Q3 report", "instructions": "refresh revenue"}, state)

    assert calls == [("d1", "refresh revenue")]
    assert result["changed"] and result["new_file"] == "Q3 report (updated 2026-10-22).docx"
    change = state.changes[0]
    assert (change.name, change.kind, change.based_on, change.action) == (
        "Q3 report (updated 2026-10-22).docx", "docx", "Q3 report.docx", "created",
    )
    assert change.changes[0].after == "Revenue was $1.5M."


@pytest.mark.parametrize("question,attached,expected", [
    ("Generate an updated version of this document with the latest data", [], True),
    ("refresh the Q3 report with current numbers", [], True),
    ("update this", ["Q3 report.docx"], True),
    ("what does the attached pdf say?", ["handbook.pdf"], True),
    ("Who owns the data pipeline?", [], False),
])
def test_document_requests_route_to_file_tools(question, attached, expected) -> None:
    assert ask_agent._wants_files(question, [], [], attached) is expected
