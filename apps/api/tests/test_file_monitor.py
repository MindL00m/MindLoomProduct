"""File monitoring: applying model changes safely, and Ask's monitoring choices."""

from __future__ import annotations

import pytest

from loombot import ask_agent
from loombot import files as files_service
from loombot.file_monitor import apply_changes

BASE = "date,sender,code,status\n2026-10-20,Squarespace,111111,unused\n"


def test_append_fits_rows_to_header_and_skips_duplicates_and_blanks() -> None:
    content, added, updated = apply_changes(
        BASE,
        [
            ["2026-10-21", "Squarespace", "222222"],                 # padded
            ["2026-10-20", "Squarespace", "111111", "unused"],       # duplicate
            ["", "", "", ""],                                        # blank
            ["2026-10-22", "Squarespace", "333333", "unused", "x"],  # trimmed
            "not a row",
        ],
        [],
    )
    assert (added, updated) == (2, 0)
    assert content.splitlines()[1:] == [
        "2026-10-20,Squarespace,111111,unused",
        "2026-10-21,Squarespace,222222,",
        "2026-10-22,Squarespace,333333,unused",
    ]


def test_cell_updates_need_a_real_row_and_column() -> None:
    content, added, updated = apply_changes(
        BASE,
        [],
        [
            {"row": 1, "column": "Status", "value": "used"},
            {"row": 9, "column": "status", "value": "x"},
            {"row": 1, "column": "nope", "value": "x"},
            {"row": 0, "column": "status", "value": "header?"},
        ],
    )
    assert (added, updated) == (0, 1)
    assert content.splitlines() == ["date,sender,code,status", "2026-10-20,Squarespace,111111,used"]


def test_no_changes_returns_original_content() -> None:
    assert apply_changes(BASE, [], [{"row": 1, "column": "code", "value": "111111"}]) == (BASE, 0, 0)


class _FakeFiles:
    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self.monitoring_calls: list[dict] = []

    async def list_files(self, org_id, user_id):
        return list(self.rows.values())

    async def find_file_by_name(self, org_id, user_id, name):
        wanted = name.lower() if name.lower().endswith(".csv") else f"{name.lower()}.csv"
        return next((dict(r) for r in self.rows.values() if r["name"].lower() == wanted), None)

    async def create_file(self, org_id, user_id, name, content="", *, edited_by="user"):
        body, rows, columns = files_service.normalize_csv(content)
        record = {"file_id": f"f{len(self.rows) + 1}", "name": files_service.normalize_name(name),
                  "content": body, "row_count": rows, "column_count": columns,
                  "monitor_enabled": False, "monitor_instructions": None}
        self.rows[record["file_id"]] = record
        return dict(record)

    async def set_monitoring(self, org_id, user_id, file_id, *, enabled, instructions=None):
        self.monitoring_calls.append({"file_id": file_id, "enabled": enabled, "instructions": instructions})
        record = self.rows[file_id]
        record["monitor_enabled"] = enabled
        record["monitor_instructions"] = instructions or record["monitor_instructions"]
        return dict(record)


@pytest.fixture
def fake_files(monkeypatch) -> _FakeFiles:
    fake = _FakeFiles()
    for attr in ("list_files", "find_file_by_name", "create_file", "set_monitoring"):
        monkeypatch.setattr(files_service, attr, getattr(fake, attr))
    return fake


async def _write(args: dict, state: ask_agent._FileToolState) -> dict:
    result, *_ = await ask_agent._safe_run_tool(
        name="write_csv_file", arguments=args, org_id="o", user_id="u",
        people_cache={}, file_state=state,
    )
    return result


async def test_monitor_yes_turns_monitoring_on(fake_files) -> None:
    state = ask_agent._FileToolState()
    result = await _write({
        "name": "leads", "content": "name,email\n", "monitor": "yes",
        "monitor_instructions": "Add a row for every new inbound lead email.",
    }, state)
    assert result["monitoring"] == "on" and "ask_user" not in result
    assert fake_files.monitoring_calls == [
        {"file_id": "f1", "enabled": True, "instructions": "Add a row for every new inbound lead email."}
    ]
    assert (state.changes[0].monitoring, state.changes[0].monitor_instructions) == (
        "on", "Add a row for every new inbound lead email.",
    )


async def test_monitor_no_leaves_snapshot_files_alone(fake_files) -> None:
    state = ask_agent._FileToolState()
    result = await _write({"name": "codes", "content": "code\n1\n", "monitor": "no"}, state)
    assert result["monitoring"] == "off" and fake_files.monitoring_calls == []


async def test_unclear_stores_a_suggestion_and_asks_the_user(fake_files) -> None:
    state = ask_agent._FileToolState()
    result = await _write({
        "name": "vendors", "content": "vendor\nAcme\n", "monitor": "unclear",
        "monitor_instructions": "Add new vendors that appear in contracts.",
    }, state)
    assert result["monitoring"] == "suggested"
    assert "keep vendors.csv updated" in result["ask_user"]
    assert fake_files.monitoring_calls[0]["enabled"] is False
    assert state.changes[0].monitoring == "suggested"

    # The user says yes: the stored suggestion is enabled without new instructions.
    toggled, *_ = await ask_agent._safe_run_tool(
        name="set_file_monitoring", arguments={"name": "vendors.csv", "enabled": True},
        org_id="o", user_id="u", people_cache={}, file_state=state,
    )
    assert toggled["monitoring"] == "on"
    assert toggled["instructions"] == "Add new vendors that appear in contracts."
    assert state.changes[0].monitoring == "on" and len(state.changes) == 1


async def test_updating_from_a_recurring_source_turns_monitoring_on(fake_files, monkeypatch) -> None:
    fake_files.rows["f1"] = {
        "file_id": "f1", "name": "supplier prices.csv", "content": "item,price\nflour,4.00\n",
        "row_count": 1, "column_count": 2, "monitor_enabled": False, "monitor_instructions": None,
    }

    async def update(org_id, user_id, file_id, *, content=None, name=None, edited_by="user"):
        body, rows, columns = files_service.normalize_csv(content)
        fake_files.rows[file_id].update(content=body, row_count=rows, column_count=columns)
        return dict(fake_files.rows[file_id])

    monkeypatch.setattr(files_service, "update_file", update)
    state = ask_agent._FileToolState(read_ids={"f1"})
    result = await _write({
        "name": "supplier prices.csv", "content": "item,price\nflour,4.50\n",
        "monitor": "yes",
        "monitor_instructions": "Update item prices from new emails sent by amey05agarwal@gmail.com.",
    }, state)
    assert result["action"] == "updated" and result["monitoring"] == "on"
    assert fake_files.monitoring_calls[-1]["enabled"] is True


async def test_access_tokens_include_connected_account_addresses(monkeypatch) -> None:
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from keeper import auth

    async def fake_user(org_id, user_id):
        return SimpleNamespace(user_id=user_id, email="Soham@MindLoomAI.org")

    def no_graph():
        raise RuntimeError("graph offline")

    class _Session:
        async def execute(self, statement, params):
            assert params == {"org": "o1", "user": "u1"}
            return [("soham.personal@gmail.com",), ("soham@mindloomai.org",)]

    @asynccontextmanager
    async def session():
        yield _Session()

    monkeypatch.setattr(auth, "get_user_by_id", fake_user)
    monkeypatch.setattr(auth, "get_neo4j_driver", no_graph)
    monkeypatch.setattr(auth, "get_session_factory", lambda: session)

    tokens = await auth.get_user_access_tokens("o1", "u1")
    assert tokens == [
        "org:o1", "user:u1", "soham@mindloomai.org", "domain:mindloomai.org",
        "soham.personal@gmail.com",
    ]
