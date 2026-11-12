"""The task loop, with a stand-in driver and overlay, against the real API routes
(``apps/api``, in-memory store, a scripted model). This is the contract between this app
and the server: what it sends must pass the routes' validation, and what the server sends
back must mean what this app thinks it means."""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.routes import captures as capture_routes  # noqa: E402
from keeper.auth import require_user_context  # noqa: E402
from shuttle import device_tasks as dt  # noqa: E402
from shuttle import skill_automation as sa  # noqa: E402

from mindloom_agent import gate, policy  # noqa: E402
from mindloom_agent.api import ApiClient, ApiError  # noqa: E402
from mindloom_agent.outline import OutlineNode, render  # noqa: E402
from mindloom_agent.runner import App, Outcome, Runner, Snapshot  # noqa: E402

RUN = {"run_id": "run-1", "org_id": "org-a", "user_id": "user-a", "skill_id": "skill-1", "inputs": "{}"}
APPS = ["TallyPrime"]


def call(name, args, call_id="c1"):
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


class Model:
    def __init__(self, *turns):
        self.turns = list(turns)
        self.seen = []

    async def __call__(self, task, messages):
        self.seen.append(json.loads(json.dumps(messages)))
        return "", self.turns.pop(0)


class ServerApi(ApiClient):
    """The agent's API client, sending through the real routes."""

    def __init__(self, client):
        super().__init__("http://localhost", "token")
        self.client = client
        self.sent = []

    def _send(self, method, path, body=None):
        self.sent.append((method, path, body))
        response = self.client.request(method, path, json=body)
        if response.status_code >= 400:
            raise ApiError(response.status_code, response.text)
        return response.json()


class FakeDriver:
    """A voucher screen with a name field, a password field, Save and Delete Voucher."""

    def __init__(self):
        self.app = App(pid=100, name="TallyPrime", names=("tally", "TallyPrime"))
        self.values = {"Party": ""}
        self.performed: list[gate.Action] = []
        self.opened: list[str] = []
        self.in_front = False
        self.focus = "Party"
        self.default_button = "Delete Voucher"
        self.person = False
        self.events: list[str] = []
        self.refuse_front = False
        self.allowed = True
        self.blocked_by_driver: str | None = None

    def preferred_app(self, allowed):
        return self.app if policy.is_allowed(self.app.names, allowed) else None

    def is_running(self, app):
        return True

    def snapshot(self, app, allowed):
        nodes = [
            OutlineNode(1, "Edit", label="Party", value=self.values["Party"], focused=self.focus == "Party"),
            OutlineNode(2, "Edit", label="Password", secure=True, focused=self.focus == "Password"),
            OutlineNode(3, "Button", label="Save", focused=self.focus == "Save"),
            OutlineNode(4, "Button", label="Delete Voucher", is_default=True, focused=self.focus == "Delete Voucher"),
            OutlineNode(5, "Text", label="Status: " + self.values.get("status", "idle")),
        ] if app else []
        outline = render(nodes, header=['Window: "Voucher" (in the background)'] if app else ["No allowed app is open."])
        focused = next((e for e in outline.entries if e.focused), None)
        return Snapshot(
            outline=outline, pid=app.pid if app else None, app_name=app.name if app else "",
            app_allowed=self.allowed, default_button_label=self.default_button if app else None,
            focused_button_label=focused.label if focused and focused.role == "Button" else None,
            focused_is_secure=bool(focused and focused.secure), content="\n".join(outline.text.split("\n")[1:]),
            observation={"app": app.name if app else "", "bundle_id": "tally", "text": outline.text,
                         "elements": [e.summary() for e in outline.entries], "running_apps": ["TallyPrime"]})

    def open_app(self, name, allowed):
        self.opened.append(name)
        return self.app, f"{name} is open."

    def perform(self, action, snapshot, keep_going):
        if self.blocked_by_driver:
            return Outcome(False, "Enter presses “Delete Voucher”.", self.blocked_by_driver)
        self.performed.append(action)
        entry = snapshot.outline.entry(action.ref)
        if action.tool == "set_value":
            self.values[entry.label] = action.string("text")
            return Outcome(True, f"The field now shows “{action.string('text')}”.")
        if action.tool == "press":
            self.values["status"] = {"Save": "saved", "Delete Voucher": "deleted"}[entry.label]
            return Outcome(True, f"Pressed “{entry.label}”.")
        if action.tool == "key":
            self.values["status"] = "key " + action.string("combo")
            return Outcome(True, "Pressed.")
        return Outcome(True, "ok")

    def is_in_front(self, app):
        return self.in_front

    def bring_forward(self, app):
        if self.refuse_front:
            return None
        self.events.append("forward")
        self.in_front = True
        return ("previous",)

    def restore_front(self, token):
        self.events.append("restore")
        self.in_front = False

    def person_active(self):
        return self.person

    def reset_input(self):
        pass


class FakeOverlay:
    """Answers by state: {"approval": ["approve"]}. Stops the run if nobody answers for long."""

    def __init__(self, **answers):
        self.answers = {state: list(choices) for state, choices in answers.items()}
        self.state = "idle"
        self.shown: list[tuple] = []
        self.asked = 0

    def _show(self, state, *details):
        self.state = state
        self.shown.append((state, *details))

    def show_working(self, title, line, step): self._show("working", line)
    def show_consent(self, apps): self._show("consent", apps)
    def show_approval(self, title, description, reason): self._show("approval", description, reason)
    def show_ask(self, title, instruction): self._show("ask", instruction)
    def show_countdown(self, title, app_name, seconds): self._show("countdown", app_name, seconds)
    def show_paused(self, title, app_name): self._show("paused", app_name)
    def show_finished(self, title, status, message): self._show("finished", status, message)
    def show_notice(self, title, message): self._show("notice", message)
    def hide(self): self._show("hidden")

    def states(self):
        return [s[0] for s in self.shown]

    def take_choice(self):
        queued = self.answers.get(self.state)
        if queued:
            return queued.pop(0)
        self.asked += 1
        return "stop" if self.asked > 300 else None


@pytest.fixture
def world(monkeypatch):
    dt.use_memory_store(True)
    handed, stopped = [], []

    async def fake_continue(org_id, user_id, run_id, *, task_id, result):
        handed.append(result)

    async def fake_stop_run(org_id, user_id, run_id):
        stopped.append(run_id)

    monkeypatch.setattr(sa, "continue_after_device", fake_continue)
    monkeypatch.setattr(sa, "stop_run", fake_stop_run)
    app = FastAPI()
    app.include_router(capture_routes.router)
    app.dependency_overrides[require_user_context] = lambda: ("org-a", "user-a")

    class World:
        api = ServerApi(TestClient(app))
        driver = FakeDriver()
        consented = True

        def task(self, apps=APPS, surface="windows", **kwargs):
            return asyncio.run(dt.create_task(RUN, title="Enter the voucher", goal="Enter it", allowed_apps=list(apps),
                                              auto_approve=False, surface=surface, **kwargs))

        def model(self, *turns):
            model = Model(*turns)
            monkeypatch.setattr(dt, "_model_call", model)
            return model

        def runner(self, overlay):
            def consent():
                World.consented = True

            return Runner(self.api, self.driver, overlay, consented=lambda: World.consented,
                          set_consented=consent, pace=0)

        def stored(self, task):
            return dt._memory[task["task_id"]]

    World.handed, World.stopped = handed, stopped
    yield World()
    dt.use_memory_store(False)


def test_a_task_is_done_end_to_end(world):
    task = world.task()
    model = world.model([call("set_value", {"ref": "e1", "text": "Azure Interior"})],
                        [call("press", {"ref": "e3"})], [call("done", {"summary": "Voucher saved"})])
    overlay = FakeOverlay()
    assert world.runner(overlay).poll_once() is True

    assert world.driver.values == {"Party": "Azure Interior", "status": "saved"}
    assert [a.tool for a in world.driver.performed] == ["set_value", "press"]
    assert world.stored(task)["status"] == "completed" and world.handed[0]["summary"] == "Voucher saved"
    assert overlay.shown[-1] == ("finished", "completed", "Voucher saved")
    # It polled and claimed as the Windows app, and every step passed the routes' validation.
    paths = [path for _, path, _ in world.api.sent]
    assert paths[0] == "/captures/device-tasks?status=pending&surface=windows"
    assert paths[1].endswith("/claim?surface=windows") and all(p.endswith("/step") for p in paths[2:])
    # The model saw the Windows outline and each result, with whether the screen changed.
    assert '[e3] Button "Save"' in model.seen[0][-1]["content"] and "Windows UI Automation" in model.seen[0][0]["content"]
    assert '"screen_changed": true' in model.seen[1][-1]["content"] and "now shows" in model.seen[1][-1]["content"]
    steps = [body for _, path, body in world.api.sent if path.endswith("/step")]
    assert steps[0]["result"] is None and steps[1]["result"]["ok"] is True and steps[1]["result"]["changed"] is True


def test_the_servers_approval_reaches_the_person_and_approve_delivers_it(world):
    task = world.task()
    world.model([call("press", {"ref": "e4"})], [call("done", {"summary": "Deleted"})])
    overlay = FakeOverlay(approval=["approve"])
    world.runner(overlay).poll_once()
    approval = next(s for s in overlay.shown if s[0] == "approval")
    assert approval[1] == "Press “Delete Voucher”" and "can't be undone" in approval[2]
    assert [(a.tool, a.approved) for a in world.driver.performed] == [("press", True)]
    assert world.driver.values["status"] == "deleted" and world.stored(task)["status"] == "completed"


def test_reject_means_the_action_is_never_performed(world):
    world.task()
    model = world.model([call("press", {"ref": "e4"})], [call("done", {"summary": "Left it", "outcome": "not_done"})])
    world.runner(FakeOverlay(approval=["reject"])).poll_once()
    assert world.driver.performed == [] and "status" not in world.driver.values
    assert any('"rejected": true' in str(m["content"]) for m in model.seen[1])


def test_this_apps_own_check_catches_what_the_server_cannot_see(world):
    """Enter with a default button that deletes: the server sends the key unapproved."""

    task = world.task()
    world.model([call("key", {"combo": "enter"})], [call("done", {"summary": "ok"})])
    overlay = FakeOverlay(approval=["approve"])
    world.runner(overlay).poll_once()
    steps = [body for _, path, body in world.api.sent if path.endswith("/step")]
    assert steps[1]["result"] == {"action_id": "c1", "ok": False, "detail": "Enter presses “Delete Voucher”.",
                                  "blocked": "needs_approval", "changed": False}
    approval = next(s for s in overlay.shown if s[0] == "approval")
    assert approval[2] == "Enter presses “Delete Voucher”."
    # Only after the person approved was the key pressed, with the app brought forward and put back.
    assert [(a.tool, a.approved) for a in world.driver.performed] == [("key", True)]
    assert world.driver.events == ["forward", "restore"] and world.stored(task)["status"] == "completed"


def test_the_drivers_last_look_can_still_ask_for_approval(world):
    world.task()
    world.driver.default_button = None
    world.driver.blocked_by_driver = "needs_approval"  # the focus moved onto Delete when the app came forward
    world.model([call("key", {"combo": "enter"})], [call("done", {"summary": "Left it", "outcome": "not_done"})])
    overlay = FakeOverlay(approval=["reject"])
    world.runner(overlay).poll_once()
    assert world.driver.performed == [] and "approval" in overlay.states()


class ScriptedApi:
    """A server that answers whatever the test says: a wrong or hostile reply."""

    access_token = "token"

    def __init__(self, *actions, allowed=APPS):
        self.actions = list(actions)
        self.results = []
        self.stopped = False
        self.allowed = allowed

    def list_tasks(self, status="pending"):
        return [{"task_id": "t1", "surface": "windows", "allowed_apps": self.allowed, "status": "pending"}]

    def claim(self, task_id):
        return {"task_id": "t1", "title": "T", "allowed_apps": self.allowed, "status": "running"}

    def step(self, task_id, observation, result):
        self.results.append(result)
        if not self.actions:
            return {"status": "completed", "action": None, "task": {"status": "completed", "summary": "end"}}
        return {"status": "running", "action": self.actions.pop(0), "task": {"status": "running"}}

    def stop(self, task_id):
        self.stopped = True
        return {"status": "stopped"}


def run_scripted(*actions, driver=None, overlay=None, allowed=APPS):
    api, driver, overlay = ScriptedApi(*actions, allowed=allowed), driver or FakeDriver(), overlay or FakeOverlay()
    Runner(api, driver, overlay, consented=lambda: True, set_consented=lambda: None, pace=0).poll_once()
    return api, driver, overlay


def test_a_bad_server_reply_cannot_delete_type_a_password_or_leave_the_app():
    api, driver, _ = run_scripted(
        {"id": "a1", "tool": "press", "args": {"ref": "e4"}, "approved": False},
        {"id": "a2", "tool": "press", "args": {"ref": "e4"}, "approved": "true"},
        {"id": "a3", "tool": "set_value", "args": {"ref": "e2", "text": "hunter2"}, "approved": True},
        {"id": "a4", "tool": "key", "args": {"combo": "win+r"}, "approved": True},
        {"id": "a5", "tool": "open_app", "args": {"app": "PowerShell"}, "approved": True},
        {"id": "a6", "tool": "open_app", "args": {"app": "Notepad"}, "approved": True},
        {"id": "a7", "tool": "run_shell", "args": {"command": "del /s"}, "approved": True},
        {"id": "a8", "tool": "press", "args": {"ref": "e3"}, "approved": False, "expect_label": "Cancel"},
        {"id": "a9", "tool": "key", "args": {"combo": "shift+delete"}},
    )
    assert driver.performed == [] and driver.opened == [] and "status" not in driver.values
    blocked = [(r["action_id"], r["ok"], r["blocked"]) for r in api.results[1:]]
    assert blocked == [
        ("a1", False, "needs_approval"), ("a2", False, "needs_approval"), ("a3", False, "secure_field"),
        ("a4", False, None), ("a5", False, "app_not_allowed"), ("a6", False, "app_not_allowed"),
        ("a7", False, None), ("a8", False, None), ("a9", False, "needs_approval"),
    ]


def test_denied_apps_are_dropped_even_if_the_task_lists_them():
    api, driver, _ = run_scripted({"id": "a1", "tool": "open_app", "args": {"app": "cmd"}},
                                  allowed=["TallyPrime", "cmd", "PowerShell"])
    assert driver.opened == [] and api.results[1]["blocked"] == "app_not_allowed"


def test_nothing_is_done_in_an_app_that_is_not_allowed():
    driver = FakeDriver()
    driver.allowed = False
    api, driver, _ = run_scripted({"id": "a1", "tool": "press", "args": {"ref": "e3"}},
                                  {"id": "a2", "tool": "set_value", "args": {"ref": "e1", "text": "x"}}, driver=driver)
    assert driver.performed == [] and [r["blocked"] for r in api.results[1:]] == ["app_not_allowed"] * 2


def test_stop_ends_the_task_and_the_run(world):
    task = world.task()
    world.model([call("press", {"ref": "e4"})])
    overlay = FakeOverlay(approval=["stop"])
    world.runner(overlay).poll_once()
    assert world.stored(task)["status"] == "stopped" and world.stopped == ["run-1"]
    assert world.driver.performed == [] and overlay.shown[-1][:2] == ("finished", "stopped")


def test_first_use_asks_for_consent(world):
    task = world.task()
    type(world).consented = False
    overlay = FakeOverlay(consent=["not_now"])
    runner = world.runner(overlay)
    assert runner.poll_once() is False
    assert overlay.shown[0] == ("consent", APPS) and world.stored(task)["status"] == "pending"
    assert not type(world).consented and runner.poll_once() is False  # not asked again right away
    assert overlay.states().count("consent") == 1

    world.model([call("done", {"summary": "ok"})])
    allowing = FakeOverlay(consent=["allow"])
    assert world.runner(allowing).poll_once() is True
    assert type(world).consented and world.stored(task)["status"] == "completed"


def test_only_fresh_windows_tasks_are_taken(world):
    old = world.task()
    world.stored(old)["created_at"] -= timedelta(minutes=31)
    world.task(apps=["Numbers"], surface="mac")
    world.task(apps=["erp.example.com"], surface="browser")
    overlay = FakeOverlay()
    assert world.runner(overlay).poll_once() is False
    assert overlay.shown == [] and world.driver.performed == []
    assert all(t["status"] == "pending" for t in dt._memory.values())


def test_coming_forward_is_announced_and_can_be_declined(world):
    world.task()
    model = world.model([call("key", {"combo": "ctrl+s"})], [call("done", {"summary": "ok"})])
    overlay = FakeOverlay(countdown=["not_now"])
    world.runner(overlay).poll_once()
    assert ("countdown", "TallyPrime", 3) in overlay.shown
    assert world.driver.performed == [] and world.driver.events == []
    assert '"blocked": "declined_foreground"' in model.seen[1][-1]["content"]


def test_keys_are_not_sent_when_windows_refuses_the_foreground():
    driver = FakeDriver()
    driver.refuse_front = True
    api, driver, _ = run_scripted({"id": "a1", "tool": "type_text", "args": {"text": "hello"}}, driver=driver)
    assert driver.performed == [] and "ask_person" in api.results[1]["detail"]


def test_the_run_pauses_while_the_person_uses_the_app():
    driver = FakeDriver()
    driver.person, driver.in_front = True, True
    overlay = FakeOverlay(paused=["resume"])
    api, driver, overlay = run_scripted({"id": "a1", "tool": "press", "args": {"ref": "e3"}}, driver=driver, overlay=overlay)
    assert ("paused", "TallyPrime") in overlay.shown and [a.tool for a in driver.performed] == ["press"]

    busy = FakeDriver()
    busy.person, busy.in_front = True, True
    api, busy, _ = run_scripted({"id": "a1", "tool": "press", "args": {"ref": "e3"}}, driver=busy,
                                overlay=FakeOverlay(paused=["stop"]))
    assert busy.performed == [] and api.stopped


def test_ask_person_waits_for_done(world):
    task = world.task()
    model = world.model([call("ask_person", {"instruction": "Sign in to Tally"})], [call("done", {"summary": "ok"})])
    overlay = FakeOverlay(ask=["done"])
    world.runner(overlay).poll_once()
    assert ("ask", "Sign in to Tally") in overlay.shown and world.stored(task)["status"] == "completed"
    assert any('"person": "done"' in str(m["content"]) for m in model.seen[1])
