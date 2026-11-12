"""Windows tasks: MindLoom for Windows operating allowed native apps, one checked action at a time."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes import captures as capture_routes
from keeper.auth import require_user_context
from shuttle import device_tasks as dt
from shuttle import skill_automation as sa
from shuttle import windows_tasks as wt

RUN = {"run_id": "run-1", "org_id": "org-a", "user_id": "user-a", "skill_id": "skill-1",
       "inputs": json.dumps({"party": "Azure Interior"})}
APPS = ["TallyPrime", "Excel"]
SCREEN = {
    "app": "TallyPrime", "bundle_id": "tally.exe", "running_apps": ["TallyPrime"],
    "text": ('Window "Accounting Voucher Creation"\n[e1] Edit "Party A/c name" (focused)\n[e2] Button "Save"\n'
             '[e3] Button "Delete Voucher"\n[e4] Edit "Password" (secure)\n[e5] ComboBox "Voucher type"'),
    "elements": [
        {"ref": "e1", "role": "Edit", "label": "Party A/c name", "focused": True},
        {"ref": "e2", "role": "Button", "label": "Save"},
        {"ref": "e3", "role": "Button", "label": "Delete Voucher"},
        {"ref": "e4", "role": "Edit", "label": "Password", "secure": True},
        {"ref": "e5", "role": "ComboBox", "label": "Voucher type"},
    ],
}


def _call(name: str, args: dict, call_id: str = "c1") -> dict:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


class FakeModel:
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


async def _task(auto_approve: bool = False, apps=APPS, **kwargs) -> dict:
    task = await dt.create_task(RUN, title="Enter the voucher", goal="Enter a sales voucher for Azure Interior",
                                allowed_apps=list(apps), auto_approve=auto_approve, person="Ada",
                                surface="windows", **kwargs)
    await dt.claim("org-a", "user-a", task["task_id"], surface="windows")
    return task


async def _step(task_id: str, result: dict | None = None, screen: dict = SCREEN) -> dict:
    return await dt.step("org-a", "user-a", task_id, observation=screen, result=result)


# --- Apps ---------------------------------------------------------------------------------


@pytest.mark.parametrize(("raw", "name"), [
    ("EXCEL.EXE", "excel"), ("  Tally   Prime ", "tally prime"), ("saplogon.exe", "saplogon"),
    ("Excel.lnk", "excel"), ("", ""), (None, ""),
])
def test_app_names_are_normalized(raw, name) -> None:
    assert wt.normalize_app(raw) == name


@pytest.mark.parametrize("app", [
    "Credential Manager", "Windows Security", "Registry Editor", "regedit.exe", "PowerShell",
    "pwsh", "Windows PowerShell", "cmd", "CMD.EXE", "Command Prompt", "Task Manager", "taskmgr.exe",
    "Windows Terminal", "wt", "1Password", "Bitwarden", "KeePassXC", "Settings", "Control Panel",
    "MindLoom", "MindLoom for Windows",
])
def test_apps_that_are_never_operated(app) -> None:
    assert wt.is_denied_app(app)


def test_allowed_apps_never_include_denied_ones() -> None:
    assert wt.clean_apps(["TallyPrime", "tallyprime.exe", "PowerShell", "Excel.exe", " ", "regedit",
                          "SAP Logon"]) == ["TallyPrime", "Excel", "SAP Logon"]
    assert wt.clean_apps("not a list") == [] and wt.clean_apps(None) == []
    assert len(wt.clean_apps([f"App {n}" for n in range(40)])) == wt.MAX_APPS
    for ordinary in ("Excel", "TallyPrime", "saplogon", "Notepad", "Outlook"):
        assert not wt.is_denied_app(ordinary)


def test_windows_tools_are_well_formed() -> None:
    names = [t["function"]["name"] for t in wt.TOOLS]
    assert len(names) == len(set(names))
    assert set(names) == wt.ACTION_TOOLS | {"ask_person", "done"}
    for tool in wt.TOOLS:
        params = tool["function"]["parameters"]
        assert set(params["required"]) <= set(params["properties"])
    # Typing always comes forward on Windows, so there is no foreground switch to set.
    assert "foreground" not in json.dumps(wt.TOOLS)


# --- Keys ---------------------------------------------------------------------------------


@pytest.mark.parametrize(("raw", "combo"), [
    ("Ctrl + Shift + S", "ctrl+shift+s"), ("shift+ctrl+s", "ctrl+shift+s"), ("Control-S", "ctrl+s"),
    ("Enter", "return"), ("ALT+F4", "alt+f4"), ("Esc", "escape"), ("del", "delete"),
    ("Backspace", "backspace"), ("opt+s", "alt+s"), ("Windows+R", "win+r"), ("cmd+s", "cmd+s"),
    ("ctrl++", "ctrl++"), ("-", "-"), ("ctrl+-", "ctrl+-"), ("PgDn", "pagedown"), ("", ""),
])
def test_key_combos_are_normalized(raw, combo) -> None:
    assert wt.normalize_combo(raw) == combo


def test_backspace_and_delete_stay_different_keys() -> None:
    # On the Mac "backspace" is the delete key; on Windows they are two keys.
    assert wt.normalize_combo("shift+backspace") != wt.normalize_combo("shift+delete")
    assert dt.normalize_combo("cmd+backspace") == dt.normalize_combo("cmd+delete")


@pytest.mark.parametrize("combo", ["enter", "tab", "shift+tab", "ctrl+s", "alt+f4", "f2", "ctrl+shift+end",
                                   "ctrl+a", "a", "7", "ctrl++", "alt+down"])
def test_keys_that_may_be_pressed(combo) -> None:
    assert wt.blocked_key_reason(combo) is None


@pytest.mark.parametrize(("combo", "why"), [
    ("win+r", "Windows key"), ("win", "Windows key"), ("win+x", "Windows key"), ("cmd+s", "no cmd key"),
    ("alt+tab", "outside the app"), ("ctrl+shift+esc", "outside the app"), ("ctrl+alt+del", "outside the app"),
    ("ctrl+esc", "outside the app"), ("ctrl", "one key"), ("ctrl+a+b", "one key"), ("ctrl+banana", "Unknown key"),
])
def test_keys_that_are_never_pressed(combo, why) -> None:
    assert why in wt.blocked_key_reason(combo)


def test_blocked_and_irreversible_keys_are_in_normal_form() -> None:
    for combo in wt.IRREVERSIBLE_KEYS | wt.BLOCKED_KEYS:
        assert wt.normalize_combo(combo) == combo
    # Something that needs approval must be pressable once approved.
    for combo in wt.IRREVERSIBLE_KEYS:
        assert wt.blocked_key_reason(combo) is None


# --- Policy -------------------------------------------------------------------------------


def test_validate_action() -> None:
    elements = dt._elements(SCREEN)
    check = lambda name, args: wt.validate_action(name, args, elements, APPS, SCREEN)  # noqa: E731
    assert check("press", {"ref": "e2"}) is None
    assert "No element" in check("press", {"ref": "e99"})
    assert "Never fill" in check("set_value", {"ref": "e4", "text": "x"})
    assert "Never fill" in check("type_text", {"ref": "e4", "text": "x"})
    assert check("set_value", {"ref": "e1", "text": "Azure Interior"}) is None
    assert check("open_app", {"app": "excel.exe"}) is None and check("open_app", {"app": "TALLYPRIME"}) is None
    assert "not one of the allowed apps" in check("open_app", {"app": "Notepad"})
    assert "not one of the allowed apps" in check("open_app", {"app": ""})
    assert check("select_menu", {"path": ["File", "Save As"]}) is None
    assert "menu path" in check("select_menu", {"path": []})
    assert check("key", {"combo": "ctrl+s"}) is None
    assert "Give a key" in check("key", {"combo": " "})
    assert "Windows key" in check("key", {"combo": "win+r"})
    assert "no cmd key" in check("key", {"combo": "cmd+s"})
    assert "too long" in check("set_value", {"ref": "e1", "text": "x" * 20_001})
    assert "Unknown tool" in check("open_url", {"url": "https://example.com"})
    secure_focus = {"e4": {**elements["e4"], "focused": True}}
    assert "Never type" in wt.validate_action("type_text", {"text": "x"}, secure_focus, APPS, SCREEN)


def test_a_denied_app_is_refused_even_when_the_task_lists_it() -> None:
    elements = dt._elements(SCREEN)
    for app in ("PowerShell", "cmd.exe", "Registry Editor", "Credential Manager", "Task Manager"):
        assert "not one of the allowed apps" in wt.validate_action(
            "open_app", {"app": app}, elements, [*APPS, app], SCREEN)


def test_irreversible_reason_uses_either_check() -> None:
    elements = dt._elements(SCREEN)
    reason = lambda name, args, els=elements: wt.irreversible_reason(name, args, els, SCREEN)  # noqa: E731
    assert reason("press", {"ref": "e3"})  # label
    assert reason("press", {"ref": "e2", "irreversible": True})  # model flag
    assert not reason("press", {"ref": "e2"})
    assert not reason("set_value", {"ref": "e1", "text": "x", "irreversible": True})
    assert reason("select_menu", {"path": ["Edit", "Delete Row"]})
    assert not reason("select_menu", {"path": ["File", "Save As"]})
    for combo in ("shift+delete", "Shift+Del", "ctrl+enter", "alt+s", "alt+d", "ctrl+d"):
        assert "usually sends or deletes" in reason("key", {"combo": combo})
    assert not reason("key", {"combo": "ctrl+s"}) and not reason("key", {"combo": "delete"})
    # Mac combos mean nothing here, and Windows ones mean nothing on the Mac.
    assert not reason("key", {"combo": "ctrl+backspace"})
    assert not dt.irreversible_reason("key", {"combo": "shift+delete"}, elements)
    assert reason("key", {"combo": "ctrl+a", "irreversible": True})  # Tally's accept, flagged by the model
    assert not reason("key", {"combo": "enter"})  # focus is a text field
    on_button = {**elements, "e1": {**elements["e1"], "focused": False}, "e3": {**elements["e3"], "focused": True}}
    assert "Delete Voucher" in reason("key", {"combo": "enter"}, on_button)
    assert "Delete Voucher" in reason("key", {"combo": "space"}, on_button)
    assert not reason("key", {"combo": "tab"}, on_button)


def test_describe() -> None:
    elements = dt._elements(SCREEN)
    assert wt.describe("key", {"combo": "Ctrl + S"}, elements, SCREEN) == "Press ctrl+s"
    assert wt.describe("press", {"ref": "e2"}, elements, SCREEN) == "Press “Save”"
    assert wt.describe("select_menu", {"path": ["File", "Save As"]}, elements, SCREEN) == "Choose File › Save As"
    assert wt.describe("open_app", {"app": "Excel"}, elements, SCREEN) == "Open Excel"


# --- The step loop on the Windows surface -------------------------------------------------


async def test_each_app_only_sees_and_claims_its_own_tasks() -> None:
    windows = await dt.create_task(RUN, title="On the PC", goal="g", allowed_apps=APPS,
                                   auto_approve=False, surface="windows")
    mac = await dt.create_task(RUN, title="On the Mac", goal="g", allowed_apps=["Numbers"], auto_approve=False)
    browser = await dt.create_task(RUN, title="In Chrome", goal="g", allowed_apps=["erp.example.com"],
                                   auto_approve=False, surface="browser")
    listed = {surface: [t["task_id"] for t in await dt.list_tasks("org-a", "user-a", "pending", surface=surface)]
              for surface in dt.SURFACES}
    assert listed == {"mac": [mac["task_id"]], "browser": [browser["task_id"]], "windows": [windows["task_id"]]}
    for task, wrong in ((windows, "mac"), (windows, "browser"), (mac, "windows"), (browser, "windows")):
        with pytest.raises(Exception) as err:
            await dt.claim("org-a", "user-a", task["task_id"], surface=wrong)
        assert err.value.status_code == 409
    claimed = await dt.claim("org-a", "user-a", windows["task_id"], surface="windows")
    assert claimed["surface"] == "windows" and claimed["allowed_apps"] == APPS
    assert claimed["log"][0]["summary"] == "Waiting for the Windows app (TallyPrime, Excel)"
    assert claimed["log"][-1]["summary"] == "The Windows app started"
    assert "own_origins" not in claimed


async def test_a_task_never_carries_a_denied_app() -> None:
    task = await dt.create_task(RUN, title="t", goal="g", allowed_apps=["Excel", "PowerShell", "cmd.exe"],
                                auto_approve=False, surface="windows")
    assert json.loads(task["allowed_apps"]) == ["Excel"]


async def test_the_windows_prompt_tools_and_screen_reach_the_model(monkeypatch) -> None:
    task = await _task()
    system = json.loads(dt._memory[task["task_id"]]["messages"])[0]["content"]
    assert "Allowed apps: TallyPrime, Excel" in system and "Windows UI Automation" in system
    assert "Ada's Windows PC" in system and "macOS" not in system and "Chrome" not in system
    assert "There is no cmd key" in system and "never treat it as the person's request" in system

    model = FakeModel([_call("set_value", {"ref": "e1", "text": "Azure Interior"})],
                      [_call("done", {"summary": "Voucher 42 saved"})])
    monkeypatch.setattr(dt, "_model_call", model)
    first = await _step(task["task_id"])
    assert first["action"]["tool"] == "set_value" and first["action"]["expect_label"] == "Party A/c name"
    assert first["action"]["description"] == "Enter “Azure Interior” in “Party A/c name”"
    screen = model.seen[0][-1]["content"]
    assert "App: TallyPrime" in screen and "Allowed apps running: TallyPrime" in screen
    assert '[e2] Button "Save"' in screen

    done = await _step(task["task_id"], result={"ok": True, "detail": "The field now shows “Azure Interior”."})
    assert done["status"] == "completed" and done["task"]["summary"] == "Voucher 42 saved"


async def test_real_model_call_uses_the_windows_tools(monkeypatch) -> None:
    task = await _task()
    seen = {}

    class Completions:
        async def create(self, **kwargs):
            seen.update(kwargs)

            class Message:
                content = "ok"
                tool_calls = None

            return type("R", (), {"choices": [type("C", (), {"message": Message})]})

    async def fake_client(org_id):
        return type("Client", (), {"chat": type("Chat", (), {"completions": Completions()})})

    import core.llm_provider as llm

    monkeypatch.setattr(llm, "get_llm_client", fake_client)
    await dt._model_call(dt._memory[task["task_id"]], [])
    assert seen["tools"] is wt.TOOLS


async def test_a_key_that_leaves_the_app_is_refused_and_the_model_adapts(monkeypatch) -> None:
    task = await _task()
    model = FakeModel([_call("key", {"combo": "win+r"})], [_call("open_app", {"app": "PowerShell"})],
                      [_call("done", {"summary": "could not", "outcome": "not_done"})])
    monkeypatch.setattr(dt, "_model_call", model)
    response = await _step(task["task_id"])
    assert response["action"] is None and response["status"] == "completed"
    assert "Windows key" in model.seen[1][-1]["content"]
    assert "not one of the allowed apps" in model.seen[2][-1]["content"]


async def test_irreversible_press_waits_for_approval(monkeypatch) -> None:
    task = await _task()
    monkeypatch.setattr(dt, "_model_call", FakeModel([_call("press", {"ref": "e3"})]))
    waiting = await _step(task["task_id"])
    assert waiting["status"] == "needs_approval" and waiting["action"] is None
    assert waiting["task"]["pending"]["description"] == "Press “Delete Voucher”"
    await dt.reply("org-a", "user-a", task["task_id"], action="approve")
    delivered = await _step(task["task_id"])
    assert delivered["action"]["approved"] is True and delivered["action"]["expect_label"] == "Delete Voucher"


async def test_irreversible_key_waits_for_approval_and_auto_approve_marks_it(monkeypatch) -> None:
    task = await _task()
    monkeypatch.setattr(dt, "_model_call", FakeModel([_call("key", {"combo": "Shift+Delete"})]))
    waiting = await _step(task["task_id"])
    assert waiting["status"] == "needs_approval"
    assert waiting["task"]["pending"]["reason"] == "shift+delete usually sends or deletes"

    auto = await _task(auto_approve=True)
    monkeypatch.setattr(dt, "_model_call", FakeModel([_call("key", {"combo": "Shift+Delete"})]))
    sent = await _step(auto["task_id"])
    assert sent["status"] == "running" and sent["action"]["approved"] is True


async def test_client_side_check_turns_into_an_approval(monkeypatch) -> None:
    """Enter in a dialog whose default button says Post: only the Windows app can see that."""

    task = await _task()
    monkeypatch.setattr(dt, "_model_call", FakeModel([_call("key", {"combo": "enter"})]))
    sent = await _step(task["task_id"])
    assert sent["action"]["tool"] == "key" and sent["action"]["approved"] is False
    blocked = await _step(task["task_id"], result={
        "ok": False, "blocked": "needs_approval", "detail": "Enter presses “Post”."})
    assert blocked["status"] == "needs_approval"
    assert blocked["task"]["pending"]["reason"] == "Enter presses “Post”."


async def test_records_work_on_windows_with_the_mac_step_budget(monkeypatch, _memory) -> None:
    task = await _task(records=["Voucher A", "Voucher B"])
    assert wt.MAX_STEPS == dt.MAX_STEPS
    model = FakeModel([_call("done", {"summary": "A saved as 41"})], [_call("done", {"summary": "B saved as 42"})])
    monkeypatch.setattr(dt, "_model_call", model)
    done = await _step(task["task_id"])
    assert done["status"] == "completed" and done["task"]["summary"] == "2 of 2 records done."
    assert "record 2 of 2" in model.seen[1][1]["content"] and "Windows PC" in model.seen[1][0]["content"]
    assert [r["summary"] for r in _memory[0]["result"]["records"]] == ["A saved as 41", "B saved as 42"]


# --- The automation hand-off ----------------------------------------------------------------


def _run() -> dict:
    return {**RUN, "status": "running", "messages": json.dumps([{"role": "system", "content": "x"}]),
            "log": "[]", "pending": None}


def test_the_computer_setting_picks_the_surface() -> None:
    assert sa.hand_off_surface("hand_off_to_device") == "mac"
    assert sa.hand_off_surface("hand_off_to_device", "mac") == "mac"
    assert sa.hand_off_surface("hand_off_to_device", "windows") == "windows"
    assert sa.hand_off_surface("hand_off_to_device", None) == "mac"
    assert sa.hand_off_surface("hand_off_to_device", "linux") == "mac"
    # The browser hand-off is Chrome on any computer.
    assert sa.hand_off_surface("hand_off_to_browser", "windows") == "browser"


async def test_hand_off_creates_a_windows_task_when_the_automation_is_set_to_windows() -> None:
    run = _run()
    calls = [_call("hand_off_to_device", {"steps": "Enter the voucher in Tally", "details": "Amount 1,040.50",
                                          "records": ["V1", "V2"]})]
    assert await sa._drain(run, calls, False, device_apps=["TallyPrime", "PowerShell"],
                           browser_sites=["erp.example.com"], skill_text="Skill: Vouchers", person="Ada",
                           device_os="windows") is False
    pending = json.loads(run["pending"])
    assert run["status"] == "waiting_device" and pending["surface"] == "windows"
    task = dt._memory[pending["device_task_id"]]
    assert task["surface"] == "windows" and json.loads(task["allowed_apps"]) == ["TallyPrime"]
    assert json.loads(task["batch"])["items"] == ["V1", "V2"]
    system, goal = (m["content"] for m in json.loads(task["messages"])[:2])
    assert "Windows UI Automation" in system
    assert "Skill: Vouchers" in goal and "Azure Interior" in goal and "Amount 1,040.50" in goal
    assert "on the Windows PC" in goal


async def test_the_default_computer_is_still_the_mac() -> None:
    run = _run()
    assert await sa._drain(run, [_call("hand_off_to_device", {"steps": "Enter it in Numbers"})], False,
                           device_apps=["Numbers"]) is False
    pending = json.loads(run["pending"])
    task = dt._memory[pending["device_task_id"]]
    assert pending["surface"] == "mac" and task["surface"] == "mac"
    assert "on the Mac" in json.loads(task["messages"])[1]["content"]


async def test_windows_hand_off_without_allowed_apps_waits_for_the_person() -> None:
    run = _run()
    assert await sa._drain(run, [_call("hand_off_to_device", {"steps": "Post it in SAP"})], False,
                           browser_sites=["erp.example.com"], device_os="windows") is False
    pending = json.loads(run["pending"])
    assert run["status"] == "waiting_device" and pending["device_task_id"] is None
    assert pending["surface"] == "windows" and dt._memory == {}


async def test_browser_hand_off_is_unchanged_on_a_windows_automation() -> None:
    run = _run()
    assert await sa._drain(run, [_call("hand_off_to_browser", {"steps": "Post the bill"})], False,
                           device_apps=["TallyPrime"], browser_sites=["erp.example.com"],
                           device_os="windows") is False
    task = dt._memory[json.loads(run["pending"])["device_task_id"]]
    assert task["surface"] == "browser" and json.loads(task["allowed_apps"]) == ["erp.example.com"]


def test_engine_describes_the_device_hand_off_for_either_computer() -> None:
    spec = next(t for t in sa.tool_specs() if t["function"]["name"] == "hand_off_to_device")
    assert "Windows" in spec["function"]["description"] and "Mac" in spec["function"]["description"]
    assert "Windows" in sa.TOOL_CATALOG
    assert sa.DEVICE_SYSTEMS == ("mac", "windows")


# --- Routes ---------------------------------------------------------------------------------


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(capture_routes.router)
    app.dependency_overrides[require_user_context] = lambda: ("org-a", "user-a")
    return TestClient(app)


async def test_routes_drive_a_windows_task(client, monkeypatch) -> None:
    task = await dt.create_task(RUN, title="Enter the voucher", goal="g", allowed_apps=APPS,
                                auto_approve=False, surface="windows")
    task_id = task["task_id"]
    assert client.get("/captures/device-tasks?status=pending").json() == []
    assert client.get("/captures/device-tasks?status=pending&surface=browser").json() == []
    listed = client.get("/captures/device-tasks?status=pending&surface=windows").json()
    assert [t["task_id"] for t in listed] == [task_id] and listed[0]["surface"] == "windows"
    assert client.post(f"/captures/device-tasks/{task_id}/claim").status_code == 409  # the Mac app
    assert client.post(f"/captures/device-tasks/{task_id}/claim?surface=browser").status_code == 409
    assert client.post(f"/captures/device-tasks/{task_id}/claim?surface=windows").status_code == 200
    assert client.post(f"/captures/device-tasks/{task_id}/claim?surface=windows").status_code == 409  # twice

    monkeypatch.setattr(dt, "_model_call", FakeModel([_call("key", {"combo": "ctrl+s"})]))
    body = {"observation": SCREEN,
            "result": {"ok": False, "detail": "", "blocked": "declined_foreground"}}
    response = client.post(f"/captures/device-tasks/{task_id}/step", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["action"]["description"] == "Press ctrl+s"

    stopped: list = []

    async def fake_stop_run(org_id, user_id, run_id):
        stopped.append(run_id)

    monkeypatch.setattr(sa, "stop_run", fake_stop_run)
    assert client.post(f"/captures/device-tasks/{task_id}/stop").json()["status"] == "stopped"
    assert stopped == ["run-1"]  # Stop on the PC stops the whole automation run


def test_routes_refuse_an_unknown_surface_or_computer(client) -> None:
    assert client.get("/captures/device-tasks?surface=linux").status_code == 422
    assert client.post("/captures/device-tasks/x/claim?surface=linux").status_code == 422
    assert client.patch("/captures/skill-files/s/automation", json={"device_os": "linux"}).status_code == 422


async def test_settings_route_passes_the_computer_on(client, monkeypatch) -> None:
    seen = {}

    async def fake_update(org_id, user_id, skill_id, **kwargs):
        seen.update(kwargs)
        return {"device_os": kwargs["device_os"]}

    monkeypatch.setattr(sa, "update_automation_settings", fake_update)
    response = client.patch("/captures/skill-files/s/automation", json={"device_os": "windows", "device_apps": ["Excel"]})
    assert response.status_code == 200 and response.json() == {"device_os": "windows"}
    assert seen["device_os"] == "windows" and seen["device_apps"] == ["Excel"]


def test_schema_adds_the_computer_setting() -> None:
    import core.schema as schema

    source = Path(schema.__file__).read_text()
    assert "ADD COLUMN IF NOT EXISTS device_os TEXT NOT NULL DEFAULT 'mac'" in source


# --- The Windows app keeps the same lists ----------------------------------------------------


def test_windows_app_uses_the_same_safety_lists() -> None:
    """MindLoom for Windows checks every action again with its own copy of these lists."""

    source = (Path(__file__).resolve().parents[2] / "windows-agent/mindloom_agent/policy.py").read_text()

    def client_list(name: str) -> set[str]:
        body = re.search(rf"^{name} = \((.*?)^\)", source, re.S | re.M).group(1)
        return set(re.findall(r'"([^"]+)"', body))

    assert client_list("IRREVERSIBLE_WORDS") == set(dt._IRREVERSIBLE_WORDS)
    assert client_list("DENIED_APPS") == set(wt.DENIED_APPS)
    assert client_list("IRREVERSIBLE_KEYS") == set(wt.IRREVERSIBLE_KEYS)
    assert client_list("BLOCKED_KEYS") == set(wt.BLOCKED_KEYS)
    assert client_list("NAMED_KEYS") == set(wt.NAMED_KEYS)
