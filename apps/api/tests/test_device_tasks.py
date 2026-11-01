"""Device tasks: the Mac app operating allowed apps, one checked action at a time."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from api.routes import captures as capture_routes
from keeper.auth import require_user_context
from shuttle import device_tasks as dt
from shuttle import skill_automation as sa

RUN = {"run_id": "run-1", "org_id": "org-a", "user_id": "user-a", "skill_id": "skill-1",
       "inputs": json.dumps({"invoice": "INV-42"})}
SCREEN = {
    "app": "Mail", "bundle_id": "com.apple.mail", "running_apps": ["Mail"],
    "text": 'Window "New Message"\n[e1] AXTextField "To"\n[e2] AXButton "Send"\n[e3] AXSecureTextField "Password" (secure)',
    "elements": [
        {"ref": "e1", "role": "AXTextField", "label": "To", "focused": True},
        {"ref": "e2", "role": "AXButton", "label": "Send"},
        {"ref": "e3", "role": "AXSecureTextField", "label": "Password", "secure": True},
    ],
}


def _call(name: str, args: dict, call_id: str = "c1") -> dict:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


class FakeModel:
    """Returns queued turns: each a list of tool calls (or a string for a text-only reply)."""

    def __init__(self, *turns):
        self.turns = list(turns)
        self.seen: list[list[dict]] = []

    async def __call__(self, task, messages):
        self.seen.append(json.loads(json.dumps(messages)))
        turn = self.turns.pop(0)
        return (turn, []) if isinstance(turn, str) else ("", turn)


@pytest.fixture(autouse=True)
def _memory(monkeypatch):
    dt.use_memory_store(True)
    handed: list = []

    async def fake_continue(org_id, user_id, run_id, *, task_id, result):
        handed.append({"run_id": run_id, "task_id": task_id, "result": result})

    monkeypatch.setattr(sa, "continue_after_device", fake_continue)
    yield handed
    dt.use_memory_store(False)


async def _task(auto_approve: bool = False, apps=("Mail",)) -> dict:
    task = await dt.create_task(RUN, title="Send the invoice", goal="Email INV-42 to a@x.com",
                                allowed_apps=list(apps), auto_approve=auto_approve, person="Ada")
    await dt.claim("org-a", "user-a", task["task_id"])
    return task


async def _step(task_id: str, result: dict | None = None, screen: dict = SCREEN) -> dict:
    return await dt.step("org-a", "user-a", task_id, observation=screen, result=result)


def _messages(task_id: str) -> list[dict]:
    return json.loads(dt._memory[task_id]["messages"])


# --- Policy -----------------------------------------------------------------------------


@pytest.mark.parametrize("label", [
    "Send", "Send Now", "Resend", "Sending…", "Move to Trash", "Delete…", "Pay $40", "Place Order",
    "Buy", "Submit", "Don't Save", "Replace", "Confirm Transfer", "Empty Trash", "Book Meeting",
])
def test_irreversible_labels(label) -> None:
    assert dt.looks_irreversible(label)


@pytest.mark.parametrize("label", ["Save", "Open", "New Message", "Sendai office", "Signature", "Cancel", "Edit", ""])
def test_ordinary_labels(label) -> None:
    assert not dt.looks_irreversible(label)


def test_key_combos_are_normalized() -> None:
    assert dt.normalize_combo("Command+Shift+D") == "cmd+shift+d"
    assert dt.normalize_combo("shift + cmd + d") == "cmd+shift+d"
    assert dt.normalize_combo("⌘-Enter") == "cmd+return"
    assert dt.normalize_combo("Return") == "return"


def test_irreversible_reason_uses_either_check() -> None:
    elements = dt._elements(SCREEN)
    assert dt.irreversible_reason("press", {"ref": "e2"}, elements)  # label
    assert dt.irreversible_reason("press", {"ref": "e1", "irreversible": True}, elements)  # model flag
    assert not dt.irreversible_reason("press", {"ref": "e1"}, elements)
    assert dt.irreversible_reason("key", {"combo": "Cmd+Shift+D"}, elements)
    assert not dt.irreversible_reason("key", {"combo": "cmd+n"}, elements)
    assert dt.irreversible_reason("select_menu", {"path": ["Message", "Send Again"]}, elements)
    assert not dt.irreversible_reason("select_menu", {"path": ["Send", "Show Mailbox"]}, elements)
    assert not dt.irreversible_reason("set_value", {"ref": "e2", "irreversible": True}, elements)


def test_validate_action() -> None:
    elements = dt._elements(SCREEN)
    allowed = ["Mail"]
    assert dt.validate_action("press", {"ref": "e2"}, elements, allowed) is None
    assert "No element" in dt.validate_action("press", {"ref": "e99"}, elements, allowed)
    assert "password" in dt.validate_action("set_value", {"ref": "e3", "text": "x"}, elements, allowed)
    assert "password" in dt.validate_action("type_text", {"ref": "e3", "text": "x"}, elements, allowed)
    secure_focus = {"e3": {**elements["e3"], "focused": True}}
    assert "password" in dt.validate_action("type_text", {"text": "x"}, secure_focus, allowed)
    assert dt.validate_action("open_app", {"app": "mail.app"}, elements, allowed) is None
    assert "not one of the allowed" in dt.validate_action("open_app", {"app": "Numbers"}, elements, allowed)
    assert "not one of the allowed" in dt.validate_action("open_app", {"app": "Terminal"}, elements, ["Terminal"])
    assert dt.validate_action("select_menu", {"path": [" "]}, elements, allowed)
    assert dt.validate_action("key", {"combo": ""}, elements, allowed)
    assert dt.validate_action("rm_rf", {}, elements, allowed)


def test_allowed_apps_never_include_denied_ones() -> None:
    assert dt.clean_apps(["Mail", "mail.app", " Numbers ", "Terminal", "System Settings", "1Password", 3, ""]) == [
        "Mail", "Numbers", "3"]
    assert dt.is_denied_app("Keychain Access.app")
    assert len(dt.clean_apps([f"App {i}" for i in range(30)])) == dt.MAX_APPS


def test_driver_tools_are_well_formed() -> None:
    names = {t["function"]["name"] for t in dt.DRIVER_TOOLS}
    assert names == dt.ACTION_TOOLS | {"ask_person", "done"}
    for tool in dt.DRIVER_TOOLS:
        params = tool["function"]["parameters"]
        assert set(params["required"]) <= set(params["properties"])


# --- Lifecycle ----------------------------------------------------------------------------


async def test_claim_once_and_only_fresh_tasks() -> None:
    task = await dt.create_task(RUN, title="t", goal="g", allowed_apps=["Mail"], auto_approve=False)
    assert (await dt.list_tasks("org-a", "user-a", status="pending"))[0]["task_id"] == task["task_id"]
    with pytest.raises(HTTPException) as exc:
        await _step(task["task_id"])
    assert exc.value.status_code == 409  # not claimed yet
    await dt.claim("org-a", "user-a", task["task_id"])
    with pytest.raises(HTTPException) as exc:
        await dt.claim("org-a", "user-a", task["task_id"])
    assert exc.value.status_code == 409

    old = await dt.create_task(RUN, title="t", goal="g", allowed_apps=["Mail"], auto_approve=False)
    dt._memory[old["task_id"]]["created_at"] -= dt.CLAIM_WINDOW + timedelta(minutes=1)
    with pytest.raises(HTTPException):
        await dt.claim("org-a", "user-a", old["task_id"])


async def test_other_people_cannot_see_or_drive_a_task() -> None:
    task = await _task()
    for org, user in [("org-a", "user-b"), ("org-b", "user-a")]:
        with pytest.raises(HTTPException) as exc:
            await dt.get_task(org, user, task["task_id"])
        assert exc.value.status_code == 404
        with pytest.raises(HTTPException):
            await dt.step(org, user, task["task_id"], observation=SCREEN, result=None)
    assert await dt.list_tasks("org-a", "user-b") == []


async def test_action_result_and_screen_go_back_to_the_model(monkeypatch) -> None:
    model = FakeModel([_call("set_value", {"ref": "e1", "text": "a@x.com"}, "c1")],
                      [_call("done", {"summary": "Filled in the address."}, "c2")])
    monkeypatch.setattr(dt, "_model_call", model)
    task = await _task()
    first = await _step(task["task_id"])
    assert first["status"] == "running"
    assert first["action"]["tool"] == "set_value" and first["action"]["id"] == "c1"
    assert first["action"]["approved"] is False and first["action"]["expect_label"] == "To"
    assert "Current screen" in model.seen[0][-1]["content"] and '[e2] AXButton "Send"' in model.seen[0][-1]["content"]
    assert "Allowed apps: Mail" in model.seen[0][0]["content"]

    second = await _step(task["task_id"], {"action_id": "c1", "ok": True, "detail": "Value set", "changed": True})
    tool_msg = model.seen[1][-1]
    assert tool_msg["role"] == "tool" and tool_msg["tool_call_id"] == "c1"
    assert json.loads(tool_msg["content"].split(dt._SCREEN_MARK)[0]) == {
        "ok": True, "detail": "Value set", "screen_changed": True}
    assert second["status"] == "completed" and second["task"]["summary"] == "Filled in the address."
    log = second["task"]["log"]
    assert [e["kind"] for e in log] == ["start", "claim", "action", "finish"]
    assert log[2]["summary"] == "Enter “a@x.com” in “To”"


async def test_finishing_hands_the_result_back_to_the_run(monkeypatch, _memory) -> None:
    monkeypatch.setattr(dt, "_model_call", FakeModel([_call("done", {"summary": "Sent.", "outcome": "done"})]))
    task = await _task()
    await _step(task["task_id"])
    assert _memory == [{"run_id": "run-1", "task_id": task["task_id"],
                        "result": {"status": "completed", "outcome": "done", "summary": "Sent.", "actions": []}}]


async def test_irreversible_press_waits_for_approval(monkeypatch, _memory) -> None:
    model = FakeModel([_call("press", {"ref": "e2"}, "c1")], [_call("done", {"summary": "Sent"}, "c2")])
    monkeypatch.setattr(dt, "_model_call", model)
    task = await _task()
    paused = await _step(task["task_id"])
    assert paused["status"] == "needs_approval" and paused["action"] is None
    assert paused["task"]["pending"]["description"] == "Press “Send”"
    assert "can't be undone" in paused["task"]["pending"]["reason"]
    # While it waits, the Mac's polls change nothing and call no model.
    assert (await _step(task["task_id"]))["status"] == "needs_approval" and len(model.seen) == 1

    await dt.reply("org-a", "user-a", task["task_id"], action="approve")
    delivered = await _step(task["task_id"])
    assert delivered["action"]["tool"] == "press" and delivered["action"]["approved"] is True
    assert len(model.seen) == 1  # the approved action goes out as approved, no new model turn
    done = await _step(task["task_id"], {"action_id": "c1", "ok": True, "detail": "Pressed"})
    assert done["status"] == "completed" and model.seen[1][-1]["tool_call_id"] == "c1"
    assert _memory and _memory[0]["result"]["actions"] == ["Press “Send”"]


async def test_rejected_action_is_reported_to_the_model(monkeypatch) -> None:
    model = FakeModel([_call("press", {"ref": "e1", "irreversible": True}, "c1")],
                      [_call("done", {"summary": "Left as draft", "outcome": "not_done"}, "c2")])
    monkeypatch.setattr(dt, "_model_call", model)
    task = await _task()
    assert (await _step(task["task_id"]))["status"] == "needs_approval"
    await dt.reply("org-a", "user-a", task["task_id"], action="reject", note="Not yet")
    done = await _step(task["task_id"])
    rejected = next(m for m in model.seen[1] if m.get("tool_call_id") == "c1")
    assert json.loads(rejected["content"]) == {"ok": False, "rejected": True, "detail": "Not yet"}
    assert done["status"] == "completed"


async def test_auto_approve_sends_irreversible_actions_marked_approved(monkeypatch) -> None:
    monkeypatch.setattr(dt, "_model_call", FakeModel([_call("press", {"ref": "e2"})]))
    task = await _task(auto_approve=True)
    response = await _step(task["task_id"])
    assert response["status"] == "running" and response["action"]["approved"] is True


async def test_mac_side_check_turns_into_an_approval(monkeypatch) -> None:
    """The Mac flags a control the server didn't (e.g. a default button behind Return)."""

    model = FakeModel([_call("key", {"combo": "return"}, "c1")], [_call("done", {"summary": "ok"}, "c2")])
    monkeypatch.setattr(dt, "_model_call", model)
    task = await _task()
    sent = await _step(task["task_id"])
    assert sent["action"]["approved"] is False
    blocked = await _step(task["task_id"], {"action_id": "c1", "ok": False, "blocked": "needs_approval",
                                            "detail": "Return presses “Send”"})
    assert blocked["status"] == "needs_approval"
    assert blocked["task"]["pending"]["reason"] == "Return presses “Send”"
    await dt.reply("org-a", "user-a", task["task_id"], action="approve")
    again = await _step(task["task_id"])
    assert again["action"]["tool"] == "key" and again["action"]["approved"] is True and len(model.seen) == 1


async def test_ask_person_then_continue(monkeypatch) -> None:
    model = FakeModel([_call("ask_person", {"instruction": "Sign in to Mail."}, "c1")],
                      [_call("ask_person", {"instruction": "Enter the 2FA code."}, "c2")],
                      [_call("done", {"summary": "x"}, "c3")])
    monkeypatch.setattr(dt, "_model_call", model)
    task = await _task()
    asked = await _step(task["task_id"])
    assert asked["status"] == "needs_person" and asked["task"]["pending"]["instruction"] == "Sign in to Mail."
    with pytest.raises(HTTPException):
        await dt.reply("org-a", "user-a", task["task_id"], action="approve")  # nothing to approve
    await dt.reply("org-a", "user-a", task["task_id"], action="done")
    assert (await _step(task["task_id"]))["status"] == "needs_person"
    assert json.loads(model.seen[1][-2]["content"]) == {"ok": True, "person": "done"}
    await dt.reply("org-a", "user-a", task["task_id"], action="cant", note="No code")
    await _step(task["task_id"])
    assert json.loads(model.seen[2][-2]["content"]) == {"ok": False, "person": "could_not_do_it", "note": "No code"}


async def test_invalid_actions_are_explained_and_retried(monkeypatch) -> None:
    model = FakeModel([_call("set_value", {"ref": "e3", "text": "hunter2"}, "c1")],
                      [_call("open_app", {"app": "Terminal"}, "c2")],
                      [_call("ask_person", {"instruction": "Type your password."}, "c3")])
    monkeypatch.setattr(dt, "_model_call", model)
    task = await _task()
    response = await _step(task["task_id"])
    assert response["status"] == "needs_person" and response["action"] is None
    assert "password field" in model.seen[1][-1]["content"]
    assert "not one of the allowed apps" in model.seen[2][-1]["content"]


async def test_gives_up_after_repeated_bad_turns(monkeypatch) -> None:
    monkeypatch.setattr(dt, "_model_call", FakeModel("I think it's done", [_call("press", {"ref": "zz"})],
                                                    [_call("press", {"ref": "zz"})]))
    task = await _task()
    response = await _step(task["task_id"])
    assert response["status"] == "failed" and "valid next action" in response["task"]["error"]


async def test_only_one_action_per_turn(monkeypatch) -> None:
    model = FakeModel([_call("press", {"ref": "e1"}, "c1"), _call("press", {"ref": "e2"}, "c2")])
    monkeypatch.setattr(dt, "_model_call", model)
    task = await _task()
    response = await _step(task["task_id"])
    assert response["action"]["id"] == "c1"
    skipped = [m for m in _messages(task["task_id"]) if m.get("tool_call_id") == "c2"]
    assert skipped and "one action at a time" in skipped[0]["content"]


async def test_step_budget(monkeypatch) -> None:
    monkeypatch.setattr(dt, "_model_call", FakeModel([_call("wait", {"seconds": 1})]))
    task = await _task()
    dt._memory[task["task_id"]]["steps"] = dt.MAX_STEPS
    response = await _step(task["task_id"])
    assert response["status"] == "failed" and str(dt.MAX_STEPS) in response["task"]["error"]


async def test_old_screens_are_dropped_from_the_conversation(monkeypatch) -> None:
    turns = [[_call("wait", {"seconds": 1}, f"c{i}")] for i in range(5)]
    model = FakeModel(*turns)
    monkeypatch.setattr(dt, "_model_call", model)
    task = await _task()
    await _step(task["task_id"])
    for i in range(4):
        await _step(task["task_id"], {"action_id": f"c{i}", "ok": True})
    screens = [m["content"] for m in model.seen[-1] if dt._SCREEN_MARK in str(m.get("content"))]
    assert len(screens) == 5
    assert sum("(older screen omitted)" in s for s in screens) == 3
    assert '[e2] AXButton "Send"' in screens[-1]


async def test_huge_screens_are_cut(monkeypatch) -> None:
    model = FakeModel([_call("wait", {"seconds": 1})])
    monkeypatch.setattr(dt, "_model_call", model)
    task = await _task()
    await _step(task["task_id"], screen={**SCREEN, "text": "x" * 100_000})
    assert len(model.seen[0][-1]["content"]) < dt.MAX_SCREEN_CHARS + 500


async def test_takeover_and_stop(monkeypatch, _memory) -> None:
    task = await _task()
    taken = await dt.reply("org-a", "user-a", task["task_id"], action="takeover")
    assert taken["status"] == "completed" and _memory[0]["result"]["summary"].startswith("The person did")

    stopped_runs: list = []

    async def fake_stop(org_id, user_id, run_id):
        stopped_runs.append(run_id)

    monkeypatch.setattr(sa, "stop_run", fake_stop)
    other = await _task()
    assert (await dt.stop("org-a", "user-a", other["task_id"]))["status"] == "stopped"
    assert stopped_runs == ["run-1"]
    assert (await _step(other["task_id"]))["status"] == "stopped"


async def test_stop_for_run_ends_active_tasks() -> None:
    task = await _task()
    await dt.stop_for_run("run-1", "Done by the person")
    assert (await dt.get_task("org-a", "user-a", task["task_id"]))["status"] == "stopped"


async def test_concurrent_writes_conflict() -> None:
    task = await _task()
    stale = await dt._load("org-a", "user-a", task["task_id"])
    fresh = await dt._load("org-a", "user-a", task["task_id"])
    await dt._save(fresh)
    with pytest.raises(HTTPException) as exc:
        await dt._save(stale)
    assert exc.value.status_code == 409


async def test_public_view_hides_the_conversation(monkeypatch) -> None:
    task = await _task()
    view = await dt.get_task("org-a", "user-a", task["task_id"])
    assert "messages" not in view and view["allowed_apps"] == ["Mail"]


# --- The automation hand-off --------------------------------------------------------------


def _run() -> dict:
    return {**RUN, "status": "running", "messages": json.dumps([{"role": "system", "content": "x"}]),
            "log": "[]", "pending": None}


async def test_hand_off_creates_a_device_task_for_allowed_apps() -> None:
    run = _run()
    calls = [_call("hand_off_to_device", {"steps": "Enter the invoice in Numbers", "details": "Total 1,040.50"})]
    assert await sa._drain(run, calls, False, device_apps=["Numbers", "Terminal"],
                           skill_text="Skill: Invoices", person="Ada") is False
    pending = json.loads(run["pending"])
    assert run["status"] == "waiting_device" and pending["device_task_id"]
    task = dt._memory[pending["device_task_id"]]
    assert json.loads(task["allowed_apps"]) == ["Numbers"]
    goal = json.loads(task["messages"])[1]["content"]
    assert "Skill: Invoices" in goal and "INV-42" in goal and "Total 1,040.50" in goal


async def test_hand_off_without_allowed_apps_waits_for_the_person() -> None:
    run = _run()
    assert await sa._drain(run, [_call("hand_off_to_device", {"steps": "Click Post"})], False) is False
    assert run["status"] == "waiting_device" and json.loads(run["pending"])["device_task_id"] is None
    assert dt._memory == {}


def test_hand_off_tool_takes_details() -> None:
    spec = next(t for t in sa.tool_specs() if t["function"]["name"] == "hand_off_to_device")
    assert "details" in spec["function"]["parameters"]["properties"]


# --- Routes --------------------------------------------------------------------------------


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(capture_routes.router)
    app.dependency_overrides[require_user_context] = lambda: ("org-a", "user-a")
    return TestClient(app)


async def test_routes_drive_a_task(client, monkeypatch) -> None:
    monkeypatch.setattr(dt, "_model_call", FakeModel([_call("press", {"ref": "e2"}, "c1")]))
    task = await dt.create_task(RUN, title="t", goal="g", allowed_apps=["Mail"], auto_approve=False)
    pending = client.get("/captures/device-tasks", params={"status": "pending"}).json()
    assert [t["task_id"] for t in pending] == [task["task_id"]]
    assert client.post(f"/captures/device-tasks/{task['task_id']}/claim").status_code == 200
    step = client.post(f"/captures/device-tasks/{task['task_id']}/step", json={"observation": SCREEN})
    assert step.status_code == 200 and step.json()["status"] == "needs_approval"
    reply = client.post(f"/captures/device-tasks/{task['task_id']}/reply", json={"action": "approve"})
    assert reply.status_code == 200 and reply.json()["status"] == "running"
    bad = client.post(f"/captures/device-tasks/{task['task_id']}/step",
                      json={"observation": SCREEN, "result": {"ok": True, "blocked": "whatever"}})
    assert bad.status_code == 422
    assert client.post(f"/captures/device-tasks/{task['task_id']}/reply", json={"action": "nuke"}).status_code == 422
    assert client.get("/captures/device-tasks/nope").status_code == 404


def test_mac_app_uses_the_same_safety_lists() -> None:
    """The Mac checks every action again with its own copy of these lists (DriverCore)."""

    import re
    from pathlib import Path

    swift = (Path(__file__).resolve().parents[2] / "desktop-agent/Sources/DriverCore/ActionPolicy.swift").read_text()

    def swift_list(name: str) -> set[str]:
        body = re.search(rf"{name}[^=]*= \[(.*?)\]", swift, re.S).group(1)
        return set(re.findall(r'"([^"]+)"', body))

    assert swift_list("irreversibleWords") == set(dt._IRREVERSIBLE_WORDS)
    assert swift_list("deniedNames") == set(dt.DENIED_APPS)
    assert swift_list("irreversibleKeys") == {dt.normalize_combo(k) for k in dt._IRREVERSIBLE_KEYS}
