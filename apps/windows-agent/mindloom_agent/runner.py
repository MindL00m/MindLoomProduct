"""Performs device tasks: the parts of an automation run that happen in apps on this PC.

Polls for pending tasks, claims one, then loops: read the app, send the screen and the
last result to the API, check the action it sends (:mod:`gate`), perform it, and read the
result back.

The person stays in control:

- the first task asks for consent; only the automation's allowed apps are touched;
- actions that can't be undone wait for Approve (here or on the web page);
- bringing an app to the front is announced with a countdown and can be declined;
- typing or clicking in the app MindLoom is working in pauses the run until Resume;
- Stop on the overlay or Ctrl+Alt+. stops the task and its whole automation run.

The driver (UI Automation) and the overlay are passed in, so this loop runs unchanged in
the tests with stand-ins for both.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Protocol

from . import gate, policy
from .api import ApiClient, ApiError
from .outline import RenderedOutline

PENDING_POLL_SECONDS = 3.0
WAIT_POLL_SECONDS = 1.5
SETTLE_SECONDS = 0.6
COUNTDOWN_SECONDS = 3
# Later foreground moments in the same task get a shorter notice.
REPEAT_COUNTDOWN_SECONDS = 1
MAX_PENDING_AGE = timedelta(minutes=30)
MAX_CONTACT_FAILURES = 10
DECLINED_SNOOZE = timedelta(minutes=10)
CONSENT_WAIT_SECONDS = 5 * 60

FINISHED = frozenset({"completed", "failed", "stopped"})
WAITING = frozenset({"needs_approval", "needs_person"})


@dataclass
class App:
    """A running app: one process and the names it goes by."""

    pid: int
    name: str
    names: tuple[str, ...] = ()


@dataclass
class Snapshot:
    """What the app showed at one moment."""

    outline: RenderedOutline
    observation: dict[str, Any]
    pid: int | None = None
    app_name: str = ""
    app_allowed: bool = False
    default_button_label: str | None = None
    focused_button_label: str | None = None
    focused_is_secure: bool = False
    # The outline without its header (which says whether the app is in front).
    content: str = ""
    # Driver-private: the live elements behind the refs.
    handles: dict[int, Any] = field(default_factory=dict)


@dataclass
class Outcome:
    """How performing an action went. ``blocked`` is set when the driver's own last look,
    with the app in front, found a reason to refuse (:func:`gate.check_focus_now`)."""

    ok: bool
    detail: str
    blocked: str | None = None


class Driver(Protocol):
    def preferred_app(self, allowed: list[str]) -> App | None: ...
    def is_running(self, app: App) -> bool: ...
    def snapshot(self, app: App | None, allowed: list[str]) -> Snapshot: ...
    def open_app(self, name: str, allowed: list[str]) -> tuple[App | None, str]: ...
    def perform(self, action: gate.Action, snapshot: Snapshot, keep_going: Callable[[], bool]) -> Outcome: ...
    def is_in_front(self, app: App) -> bool: ...
    def bring_forward(self, app: App) -> Any: ...
    def restore_front(self, token: Any) -> None: ...
    def person_active(self) -> bool: ...
    def reset_input(self) -> None: ...


class Overlay(Protocol):
    def show_working(self, title: str, line: str, step: int) -> None: ...
    def show_consent(self, apps: list[str]) -> None: ...
    def show_approval(self, title: str, description: str, reason: str | None) -> None: ...
    def show_ask(self, title: str, instruction: str) -> None: ...
    def show_countdown(self, title: str, app_name: str, seconds: int) -> None: ...
    def show_paused(self, title: str, app_name: str) -> None: ...
    def show_finished(self, title: str, status: str, message: str | None) -> None: ...
    def show_notice(self, title: str, message: str) -> None: ...
    def hide(self) -> None: ...
    def take_choice(self) -> str | None: ...


def _parse_time(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def result_of(action: gate.Action, ok: bool, detail: str, blocked: str | None = None) -> dict[str, Any]:
    return {"action_id": action.id[:100], "ok": bool(ok), "detail": str(detail)[:2000], "blocked": blocked}


class Runner:
    def __init__(
        self, api: ApiClient, driver: Driver, overlay: Overlay, *,
        consented: Callable[[], bool], set_consented: Callable[[], None], pace: float = 1.0,
    ) -> None:
        self.api = api
        self.driver = driver
        self.overlay = overlay
        self._consented = consented
        self._set_consented = set_consented
        # Tests run the same loop with ``pace=0`` so nothing waits.
        self.pace = pace
        self.active_task_id: str | None = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._snoozed: dict[str, datetime] = {}
        self._title = "Automation"
        # Why the last poll failed (401: the sign-in ran out), for the status line.
        self.last_poll_error: ApiError | None = None

    # --- Control -------------------------------------------------------------------------------

    def stop(self) -> None:
        """The Stop button and the stop hot key."""

        if self.active_task_id is not None:
            self._stop.set()
            self._wake.set()

    def _nap(self, seconds: float, wakeable: bool = True) -> None:
        if self.pace <= 0 or seconds <= 0:
            return
        if wakeable:
            self._wake.wait(seconds * self.pace)
            self._wake.clear()
        else:
            threading.Event().wait(seconds * self.pace)

    def _choice(self) -> str | None:
        choice = self.overlay.take_choice()
        if choice == "stop":
            self._stop.set()
            return None
        return choice

    # --- Picking up tasks ----------------------------------------------------------------------

    def poll_once(self) -> bool:
        """Take the oldest fresh pending task and run it. True when one was run."""

        if self.active_task_id is not None or not self.api.access_token:
            return False
        try:
            pending = self.api.list_tasks("pending")
            self.last_poll_error = None
        except ApiError as error:
            self.last_poll_error = error
            return False
        now = datetime.now(timezone.utc)
        fresh = [
            t for t in pending
            if t.get("surface", "windows") == "windows"
            and (_parse_time(t.get("created_at")) or now) > now - MAX_PENDING_AGE
            and self._snoozed.get(str(t.get("task_id")), now) <= now
        ]
        if not fresh:
            return False
        task = fresh[-1]  # the list is newest first
        task_id = str(task["task_id"])
        allowed = [str(a) for a in task.get("allowed_apps") or []]

        self.active_task_id = task_id  # hold the slot while asking for consent
        self._stop.clear()
        try:
            if not self._consented():
                if not self._ask_consent(allowed):
                    self._snoozed[task_id] = now + DECLINED_SNOOZE
                    self.overlay.hide()
                    return False
                self._set_consented()
            try:
                claimed = self.api.claim(task_id)
            except ApiError:
                self.overlay.hide()
                return False  # someone else took it, or it is too old
            self._run(claimed)
            return True
        finally:
            self.active_task_id = None
            self._stop.clear()

    def _ask_consent(self, apps: list[str]) -> bool:
        self.overlay.take_choice()
        self.overlay.show_consent(apps)
        waited = 0.0
        while waited < CONSENT_WAIT_SECONDS:
            choice = self._choice()
            if choice == "allow":
                return True
            if choice == "not_now" or self._stop.is_set():
                return False
            self._nap(1)
            waited += 1
        return False

    # --- The loop --------------------------------------------------------------------------------

    def _run(self, claimed: dict[str, Any]) -> None:
        task_id = str(claimed["task_id"])
        self._title = str(claimed.get("title") or "") or "Automation"
        # The server already removed denied apps; this app checks its own list too.
        allowed = [str(a) for a in claimed.get("allowed_apps") or [] if not policy.is_denied([a])]
        app = self.driver.preferred_app(allowed)
        result: dict[str, Any] | None = None
        last_content: str | None = None
        contact_failures = 0
        asked_foreground = False
        step = 0
        self.overlay.show_working(self._title, f"Looking at {app.name if app else 'your apps'}…", 0)

        while True:
            if self._stop.is_set():
                try:
                    self.api.stop(task_id)
                except ApiError:
                    pass
                self.overlay.show_finished(self._title, "stopped", "Stopped. Nothing more was done.")
                return
            if app is not None and not self.driver.is_running(app):
                app = None
            snapshot = self.driver.snapshot(app, allowed)
            if result is not None and last_content is not None:
                result["changed"] = snapshot.content != last_content

            try:
                response = self.api.step(task_id, snapshot.observation, result)
                contact_failures = 0
            except ApiError as error:
                if error.status == 404:
                    self.overlay.show_finished(self._title, "failed", "This task no longer exists.")
                    return
                if error.status == 401:
                    self.overlay.show_finished(self._title, "failed", "Signed out. Sign in again; the run is "
                                                                      "still open on the web page.")
                    return
                contact_failures += 1
                if contact_failures >= MAX_CONTACT_FAILURES:
                    self.overlay.show_finished(self._title, "failed", "Lost contact with MindLoom. The run is "
                                                                      "still open on the web page.")
                    return
                self._nap(2)
                continue
            result = None
            last_content = snapshot.content

            status = str(response.get("status") or "")
            task = response.get("task") or {}
            if status in FINISHED:
                self.overlay.show_finished(self._title, status, task.get("summary") or task.get("error"))
                return
            if status in WAITING:
                self._wait_for_person(task_id, task or {"status": status})
                continue
            if status != "running" or not isinstance(response.get("action"), dict):
                self._nap(WAIT_POLL_SECONDS if status == "pending" else 1)
                continue

            action = gate.Action.from_json(response["action"])
            step += 1
            self.overlay.show_working(self._title, action.description or action.tool, step)
            app, result, asked_foreground = self._perform(action, snapshot, app, allowed, asked_foreground)

    def _perform(
        self, action: gate.Action, snapshot: Snapshot, app: App | None, allowed: list[str], asked_foreground: bool,
    ) -> tuple[App | None, dict[str, Any], bool]:
        def fail(detail: str, blocked: str | None = None) -> tuple[App | None, dict[str, Any], bool]:
            return app, result_of(action, False, detail, blocked), asked_foreground

        if action.tool == "open_app":
            name = action.string("app")
            if policy.is_denied([name]) or not policy.is_allowed([name], allowed):
                return fail(f"{name} isn't one of the apps allowed for this automation.", gate.APP_NOT_ALLOWED)
            opened, detail = self.driver.open_app(name, allowed)
            self.driver.reset_input()
            self._nap(SETTLE_SECONDS, wakeable=False)
            return opened or app, result_of(action, opened is not None, detail), asked_foreground
        if action.tool == "wait":
            try:
                seconds = float(action.args.get("seconds") or 1)
            except (TypeError, ValueError):
                seconds = 1.0
            self._nap(min(10.0, max(0.0, seconds)))
            return app, result_of(action, True, "Waited."), asked_foreground
        if action.tool not in gate.TOOLS:
            return fail(f"MindLoom doesn't know how to “{action.tool}”.")
        if app is None or snapshot.pid != app.pid:
            return fail("No allowed app is being used. Use open_app first.")

        target = snapshot.outline.entry(action.ref)
        verdict = gate.check(action, gate.Context(
            target=target, default_button_label=snapshot.default_button_label,
            focused_button_label=snapshot.focused_button_label, focused_is_secure=snapshot.focused_is_secure,
            app_name=snapshot.app_name, app_allowed=snapshot.app_allowed,
        ))
        if not verdict.proceed:
            return fail(verdict.detail, verdict.blocked)

        # Don't act in an app the person is using right now.
        if not self._wait_while_person_uses(app):
            return fail("Stopped.")

        previous = None
        brought_forward = False
        if verdict.foreground and not self.driver.is_in_front(app):
            seconds = REPEAT_COUNTDOWN_SECONDS if asked_foreground else COUNTDOWN_SECONDS
            if not self._countdown(snapshot.app_name, seconds):
                return fail(f"The person said not now to bringing {snapshot.app_name} to the front. "
                            "Ask them, or try a way that works in the background.", gate.DECLINED_FOREGROUND)
            asked_foreground = True
            previous = self.driver.bring_forward(app)
            if previous is None:
                return fail(f"Windows did not let MindLoom bring {snapshot.app_name} to the front. "
                            "Call ask_person: they can click on its window.")
            brought_forward = True
            self._nap(0.4, wakeable=False)

        try:
            outcome = self.driver.perform(action, snapshot, lambda: not self._stop.is_set())
        except Exception as error:  # noqa: BLE001 - UI Automation calls fail in many ways
            outcome = Outcome(False, f"The app did not take it ({type(error).__name__}). "
                                     "Look again and try another way.")
        self.driver.reset_input()
        self._nap(SETTLE_SECONDS, wakeable=False)
        if brought_forward:
            self.driver.restore_front(previous)
            self.driver.reset_input()
        return app, result_of(action, outcome.ok, outcome.detail, outcome.blocked), asked_foreground

    def _wait_while_person_uses(self, app: App) -> bool:
        """False if the run was stopped while paused."""

        def using_it() -> bool:
            return self.driver.person_active() and self.driver.is_in_front(app)

        if not using_it():
            return not self._stop.is_set()
        self.overlay.take_choice()
        self.overlay.show_paused(self._title, app.name)
        while not self._stop.is_set():
            if self._choice() == "resume":
                self.driver.reset_input()
                return True
            self._nap(0.5)
        return False

    def _countdown(self, app_name: str, seconds: int) -> bool:
        self.overlay.take_choice()
        for remaining in range(seconds, 0, -1):
            self.overlay.show_countdown(self._title, app_name, remaining)
            self._nap(1)
            choice = self._choice()
            if self._stop.is_set() or choice == "not_now":
                return False
        return True

    def _wait_for_person(self, task_id: str, first: dict[str, Any]) -> None:
        """The task waits for an approval or for the person to do something. Answers can come
        from the overlay or the web page; either way the next poll sees it."""

        task = first
        shown: Any = object()
        self.overlay.take_choice()
        while not self._stop.is_set() and task.get("status") in WAITING:
            pending = task.get("pending") or {}
            if pending != shown:
                shown = pending
                if task["status"] == "needs_approval":
                    self.overlay.show_approval(self._title, str(pending.get("description") or "An action"),
                                               pending.get("reason"))
                else:
                    self.overlay.show_ask(self._title, str(pending.get("instruction") or "Check the app."))
            answer = self._choice()
            reply = {("needs_approval", "approve"): "approve", ("needs_approval", "reject"): "reject",
                     ("needs_person", "done"): "done", ("needs_person", "cant"): "cant"}.get(
                         (str(task.get("status")), str(answer)))
            if reply:
                self.driver.reset_input()
                try:
                    task = self.api.reply(task_id, reply)
                    continue
                except ApiError:
                    pass
            self._nap(WAIT_POLL_SECONDS)
            try:
                task = self.api.get_task(task_id)
            except ApiError:
                pass
        self.driver.reset_input()
        if not self._stop.is_set():
            self.overlay.show_working(self._title, "Carrying on…", 0)
