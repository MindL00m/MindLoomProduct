"""Creating and editing TXT and Word (DOCX) files from the Files tab and Ask."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from loombot import ask_agent
from loombot import files as files_service
from loombot.docx_builder import blocks_to_markdown, markdown_to_docx

MARKDOWN = """# Launch plan

Owner: **Priya**, reviewer *Alex*.

## Milestones
- Beta in October
- GA in November

1. Price list
2. Docs

| Task | Owner |
| --- | --- |
| Pricing | Amey |
"""


def test_markdown_round_trips_through_word() -> None:
    data = markdown_to_docx(MARKDOWN, title="Launch plan")
    files_service.validate_binary("docx", data)
    blocks = files_service.docx_blocks(data)
    assert blocks[0] == {"type": "heading", "level": 1, "text": "Launch plan"}
    assert {"type": "list_item", "text": "Beta in October"} in blocks
    assert {"type": "table", "rows": [["Task", "Owner"], ["Pricing", "Amey"]]} in blocks
    markdown = blocks_to_markdown(blocks)
    assert "# Launch plan" in markdown and "## Milestones" in markdown
    assert "- Beta in October\n- GA in November" in markdown
    assert "1. Price list\n2. Docs" in markdown
    assert "| Task | Owner |\n| --- | --- |\n| Pricing | Amey |" in markdown
    assert "Owner: **Priya**, reviewer *Alex*." in markdown


def test_bold_and_italic_become_run_formatting() -> None:
    import io

    import docx

    document = docx.Document(io.BytesIO(markdown_to_docx("Owner: **Priya** and *Alex*")))
    runs = document.paragraphs[0].runs
    assert [(r.text, bool(r.bold), bool(r.italic)) for r in runs] == [
        ("Owner: ", False, False), ("Priya", True, False), (" and ", False, False), ("Alex", False, True),
    ]


async def test_create_document_builds_a_docx(monkeypatch) -> None:
    captured: dict = {}

    async def fake_binary(org_id, user_id, name, kind, data, *, edited_by="user", based_on=None):
        captured.update(name=name, kind=kind, data=data, edited_by=edited_by)
        return {"file_id": "f1", "name": name, "kind": kind, "row_count": 3, "column_count": 1}

    monkeypatch.setattr(files_service, "create_binary_file", fake_binary)
    saved = await files_service.create_document("o", "u", "Launch plan", MARKDOWN, kind="docx")
    assert saved["name"] == "Launch plan.docx" and captured["kind"] == "docx"
    assert files_service.docx_blocks(captured["data"])[0]["text"] == "Launch plan"

    with pytest.raises(HTTPException):
        await files_service.create_document("o", "u", "x.pdf", "text", kind="pdf")


async def test_editing_docx_text_rebuilds_it_and_stays_undoable(monkeypatch) -> None:
    replaced: dict = {}

    async def current(org_id, user_id, file_id):
        return {"file_id": file_id, "name": "Plan.docx", "kind": "docx", "content": ""}

    async def fake_replace(org_id, user_id, file_id, kind, data, *, edited_by):
        replaced.update(kind=kind, data=data, edited_by=edited_by)
        return {"file_id": file_id, "name": "Plan.docx", "kind": kind, "can_undo": True}

    async def current_bytes(org_id, user_id, file_id):
        return markdown_to_docx("# Old title\n\nSame **bold** text"), {}

    monkeypatch.setattr(files_service, "get_file", current)
    monkeypatch.setattr(files_service, "get_file_bytes", current_bytes)
    monkeypatch.setattr(files_service, "_replace_binary", fake_replace)
    saved = await files_service.update_file("o", "u", "f1", content="# New title", edited_by="loombot")
    assert saved["can_undo"] is True and replaced["edited_by"] == "loombot"
    assert files_service.docx_blocks(replaced["data"]) == [{"type": "heading", "level": 1, "text": "New title"}]

    # Saving the same text again must not replace the file (that would lose the undo point).
    replaced.clear()
    await files_service.update_file("o", "u", "f1", content="# Old title\n\nSame **bold** text\n")
    assert replaced == {}


def test_editing_as_text_keeps_bold_italic_and_links() -> None:
    data = markdown_to_docx("Owner: **Priya** and *Alex*, see `run.sh` and the plan.\n\n- **Due** Friday")
    markdown = blocks_to_markdown(files_service.docx_blocks(data))
    assert "Owner: **Priya** and *Alex*, see `run.sh` and the plan." in markdown
    assert "- **Due** Friday" in markdown
    # The round trip is stable, so re-saving unchanged text is a no-op.
    again = blocks_to_markdown(files_service.docx_blocks(markdown_to_docx(markdown)))
    assert again == markdown


def _fake_files(monkeypatch, existing: dict | None = None) -> dict:
    calls: dict = {"created": [], "updated": []}

    async def list_files(org_id, user_id):
        return [existing] if existing else []

    async def find(org_id, user_id, name):
        if existing and name.lower() == existing["name"].lower():
            return existing
        return None

    async def create(org_id, user_id, name, content="", *, kind="csv", edited_by="user", based_on=None):
        calls["created"].append((name, kind, content, edited_by))
        final = name if files_service.kind_from_name(name) else f"{name}.{kind}"
        return {"file_id": f"new-{len(calls['created'])}", "name": final, "kind": kind,
                "row_count": 1, "column_count": 0}

    async def update(org_id, user_id, file_id, *, content=None, name=None, edited_by="user"):
        calls["updated"].append((file_id, content, edited_by))
        return {**existing, "row_count": 2, "column_count": 0}

    async def read_text(org_id, user_id, file):
        return "# Old plan"

    monkeypatch.setattr(files_service, "list_files", list_files)
    monkeypatch.setattr(files_service, "find_file_by_name", find)
    monkeypatch.setattr(files_service, "create_document", create)
    monkeypatch.setattr(files_service, "update_file", update)
    monkeypatch.setattr(files_service, "read_text_for_ask", read_text)
    return calls


async def test_write_document_creates_word_and_text_files(monkeypatch) -> None:
    calls = _fake_files(monkeypatch)
    state = ask_agent._FileToolState()

    result, *_ = await ask_agent._safe_run_tool(
        name="write_document",
        arguments={"name": "Launch memo", "content": MARKDOWN, "summary": "Launch memo for the team."},
        org_id="o", user_id="u", people_cache={}, file_state=state,
    )
    assert result["saved"] is True and result["type"] == "docx"
    assert calls["created"][0][:2] == ("Launch memo.docx", "docx")
    assert calls["created"][0][3] == "loombot"

    await ask_agent._safe_run_tool(
        name="write_document", arguments={"name": "todo.txt", "content": "- call Acme"},
        org_id="o", user_id="u", people_cache={}, file_state=state,
    )
    assert calls["created"][1][:2] == ("todo.txt", "txt")
    assert [(c.name, c.kind, c.action) for c in state.changes] == [
        ("Launch memo.docx", "docx", "created"), ("todo.txt", "txt", "created"),
    ]

    refused, *_ = await ask_agent._safe_run_tool(
        name="write_document", arguments={"name": "prices.csv", "content": "a,b"},
        org_id="o", user_id="u", people_cache={}, file_state=state,
    )
    assert "write_csv_file" in refused["error"]


async def test_write_document_requires_reading_before_replacing(monkeypatch) -> None:
    existing = {
        "file_id": "d1", "name": "Plan.docx", "kind": "docx", "content": "", "row_count": 1,
        "column_count": 0, "monitor_enabled": False,
    }
    calls = _fake_files(monkeypatch, existing)
    state = ask_agent._FileToolState()
    args = {"name": "Plan.docx", "content": "# New plan"}

    blocked, *_ = await ask_agent._safe_run_tool(
        name="write_document", arguments=args, org_id="o", user_id="u", people_cache={}, file_state=state,
    )
    assert "read_file" in blocked["error"] and calls["updated"] == []

    read, *_ = await ask_agent._safe_run_tool(
        name="read_file", arguments={"name": "Plan.docx"}, org_id="o", user_id="u",
        people_cache={}, file_state=state,
    )
    assert read["content"] == "# Old plan" and "write_document" in read["note"]
    saved, *_ = await ask_agent._safe_run_tool(
        name="write_document", arguments=args, org_id="o", user_id="u", people_cache={}, file_state=state,
    )
    assert saved["action"] == "updated"
    assert calls["updated"] == [("d1", "# New plan", "loombot")]
    assert state.changes[0].action == "updated" and state.changes[0].kind == "docx"


@pytest.mark.parametrize("question", [
    "write a memo about the launch as a word doc",
    "create notes.txt with my todo list",
    "draft a project proposal document for Acme",
])
def test_document_requests_use_file_tools(question: str) -> None:
    assert ask_agent._wants_files(question, [])
