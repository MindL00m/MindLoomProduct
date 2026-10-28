"""Files shared into a workspace are visible to, and editable by, every member."""

from __future__ import annotations

import pytest

from loombot import ask_agent, workspace_files, workspaces


async def test_files_loombot_creates_or_members_attach_are_shared(monkeypatch) -> None:
    async def find(org_id, user_id, name):
        return {"file_id": f"id-{name}"} if name == "brief.docx" else None

    linked: list = []

    async def link(org_id, user_id, workspace_id, file_ids):
        linked.append((workspace_id, file_ids))
        return []

    monkeypatch.setattr("loombot.files.find_file_by_name", find)
    monkeypatch.setattr(workspace_files, "link_files_quietly", link)
    payload = {"file_changes": [
        {"file_id": "f-new", "action": "created"},
        {"file_id": "f-mon", "action": "monitoring"},
        {"file_id": "f-upd", "action": "updated"},
    ]}
    await workspaces._share_turn_files("o", "u", "w", ["brief.docx", "missing.csv"], payload)
    assert linked == [("w", ["id-brief.docx", "f-new", "f-upd"])]


async def test_list_files_tool_includes_workspace_files(monkeypatch) -> None:
    async def mine(org_id, user_id):
        return [{
            "name": "prices.csv", "kind": "csv", "row_count": 2, "column_count": 3,
            "updated_at": None, "monitor_enabled": False, "monitor_instructions": None,
        }]

    monkeypatch.setattr("loombot.files.list_files", mine)
    room = ask_agent.WorkspaceContext(
        workspace_id="w", name="Dubai shipment", kind="group", mode="org_knowledge",
        shared_files=[
            {"name": "Invoice.docx", "kind": "docx", "owner_name": "Priya"},
            {"name": "prices.csv", "kind": "csv", "owner_name": "Me"},
        ],
    )
    out = await ask_agent._run_file_tool(
        "list_files", {}, org_id="o", user_id="u", state=ask_agent._FileToolState(),
        workspace=room,
    )
    names = [f["name"] for f in out["files"]]
    assert names == ["prices.csv", "Invoice.docx"]
    assert out["files"][1]["shared_in_workspace_by"] == "Priya"


async def test_read_file_falls_back_to_workspace_files(monkeypatch) -> None:
    async def not_mine(*args):
        return None

    async def shared(org_id, workspace_id, name):
        return {"name": "Invoice.docx", "kind": "docx", "owner_user_id": "priya",
                "row_count": 4, "column_count": 0}

    async def read(org_id, owner, file):
        assert owner == "priya"
        return "INVOICE 001"

    monkeypatch.setattr("loombot.files.find_file_by_name", not_mine)
    monkeypatch.setattr(workspace_files, "find_workspace_file", shared)
    monkeypatch.setattr("loombot.files.read_text_for_ask", read)
    room = ask_agent.WorkspaceContext(workspace_id="w", name="R", kind="group", mode="org_knowledge")
    out = await ask_agent._run_file_tool(
        "read_file", {"name": "Invoice"}, org_id="o", user_id="u",
        state=ask_agent._FileToolState(), workspace=room,
    )
    assert out["content"] == "INVOICE 001"


def test_workspace_hint_lists_shared_files() -> None:
    room = ask_agent.WorkspaceContext(
        workspace_id="w", name="R", kind="group", mode="org_knowledge",
        shared_files=[{"name": "Invoice.docx", "kind": "docx", "owner_name": "Priya"}],
    )
    assert "Invoice.docx (shared by Priya)" in ask_agent._workspace_hint(room)


def test_other_members_see_files_as_workspace_files() -> None:
    summaries = workspaces.card_summaries({"file_changes": [{"file_id": "f"}]})
    assert summaries == ["1 file added or updated in this workspace's Files"]


async def test_file_owner_requires_link(monkeypatch) -> None:
    from fastapi import HTTPException

    async def member(*args):
        return {"kind": "group", "name": "R"}

    class _Result:
        def scalar_one_or_none(self):
            return None

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def execute(self, *args, **kwargs):
            return _Result()

    monkeypatch.setattr(workspace_files, "_require_member", member)
    monkeypatch.setattr(workspace_files, "get_session_factory", lambda: (lambda: _Session()))
    with pytest.raises(HTTPException) as exc:
        await workspace_files.file_owner("o", "u", "w", "f")
    assert exc.value.status_code == 404
