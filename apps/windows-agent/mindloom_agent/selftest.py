"""``python -m mindloom_agent --driver-selftest <LoomFixture.exe> <log file>``

Drives the fixture app through the driver and the gate, the same way a device task does,
and checks each result by reading the app back: reading the window, setting a value,
pressing, the approval gate, the password gate, menus and typed keys. It needs a signed-in
Windows desktop (UI Automation does not work in a service session) and nobody typing
meanwhile. ``scripts/driver-selftest.ps1`` builds the fixture and runs this.
"""

from __future__ import annotations

import subprocess
import time
from typing import Any

from . import gate, policy

FIXTURE_NAME = "LoomFixture"


def run(arguments: list[str]) -> int:
    if len(arguments) < 2:
        print("usage: --driver-selftest <LoomFixture.exe> <log file>")
        return 64
    fixture_path, log_path = arguments[0], arguments[1]
    failures = 0
    log = open(log_path, "w", encoding="utf-8")  # noqa: SIM115 - open for the whole run

    def say(line: str) -> None:
        print(line)
        log.write(line + "\n")
        log.flush()

    def check(ok: Any, name: str, detail: Any = "") -> bool:
        nonlocal failures
        if not ok:
            failures += 1
        say(f"{'PASS' if ok else 'FAIL'} {name}" + (f" — {detail}" if detail else ""))
        return bool(ok)

    def note(name: str, detail: Any) -> None:
        say(f"INFO {name} — {detail}")

    from . import win32
    from .driver import WindowsDriver
    from .runner import Outcome

    allowed = [FIXTURE_NAME]
    driver = WindowsDriver()
    front_before = win32.foreground_window()
    process = subprocess.Popen([fixture_path])  # noqa: S603 - the fixture this script just built
    try:
        deadline = time.monotonic() + 15
        fixture = None
        while time.monotonic() < deadline and fixture is None:
            time.sleep(0.5)
            fixture = next((a for a in driver.running_allowed_apps(allowed) if a.pid == process.pid), None)
        if not check(fixture is not None, f"the fixture opened from {fixture_path}"):
            return 1
        # Put the person's window back in front, so the first checks run in the background.
        if front_before and win32.window_pid(front_before) != process.pid:
            win32.bring_to_front(front_before)
            time.sleep(0.5)
        note("window in front during background checks", win32.window_title(win32.foreground_window()) or "?")

        def snap():
            return driver.snapshot(fixture, allowed)

        def entry(s, label: str, role: str | None = None):
            return next((e for e in s.outline.entries if e.label == label and (role is None or e.role == role)), None)

        def act(tool: str, args: dict, s, approved: bool = False, label: str | None = None):
            """The gate's verdict and, when it lets the action through, the driver's outcome."""

            action = gate.Action(id=tool, tool=tool, args=args, approved=approved, expect_label=label)
            verdict = gate.check(action, gate.Context(
                target=s.outline.entry(action.ref), default_button_label=s.default_button_label,
                focused_button_label=s.focused_button_label, focused_is_secure=s.focused_is_secure,
                app_name=s.app_name, app_allowed=s.app_allowed))
            if not verdict.proceed:
                return verdict, None
            token = None
            if verdict.foreground and not driver.is_in_front(fixture):
                token = driver.bring_forward(fixture)
                if token is None:
                    return verdict, Outcome(False, "could not bring the fixture to the front")
                time.sleep(0.4)
            outcome = driver.perform(action, s, lambda: True)
            time.sleep(0.5)
            if token is not None:
                driver.restore_front(token)
                time.sleep(0.3)
            return verdict, outcome

        def status(s) -> str:
            return next((line.strip() for line in s.outline.text.split("\n") if "Status:" in line), "(no status)")

        def in_front() -> bool:
            return driver.is_in_front(fixture)

        # 1. Reading the window.
        s = snap()
        say(f"---- outline ----\n{s.outline.text}\n-----------------")
        for label, role in (("Name", "Edit"), ("Notes", "Edit"), ("Urgent", "CheckBox"),
                            ("Add Item", "Button"), ("Send Report", "Button")):
            check(entry(s, label, role) is not None, f"outline lists {role} “{label}”")
        password = next((e for e in s.outline.entries if e.secure), None)
        check(password is not None, "outline marks the password field secure")
        check("s3cret-fixture" not in s.outline.text and "s3cret-fixture" not in str(s.observation),
              "the password field's value is not read")
        check("Menu bar:" in s.outline.text and "List" in s.outline.text, "outline lists the menu bar")
        check(len(s.observation["elements"]) == len(s.outline.entries), "observation carries every element")
        check(s.default_button_label == "Send Report", "reads the default button", s.default_button_label)
        check(s.app_allowed, "fixture counts as an allowed app")
        check(not policy.is_allowed(fixture.names, ["Excel"]), "fixture is not allowed under another app's name")

        name, add, urgent = entry(s, "Name", "Edit"), entry(s, "Add Item"), entry(s, "Urgent")
        notes, send = entry(s, "Notes", "Edit"), entry(s, "Send Report")
        if not all((name, add, urgent, notes, send)):
            say(f"SELFTEST FAILED ({failures})")
            return 1

        # 2. Background value, press and checkbox.
        verdict, outcome = act("set_value", {"ref": name.ref, "text": "Ada Lovelace"}, s, label="Name")
        check(outcome and outcome.ok, "set_value fills Name in the background", outcome.detail if outcome else verdict.detail)
        verdict, outcome = act("press", {"ref": add.ref}, s, label="Add Item")
        check(outcome and outcome.ok, "press Add Item", outcome.detail if outcome else verdict.detail)
        s = snap()
        check("Ada Lovelace" in s.outline.text and "added Ada Lovelace" in status(s), "the app shows the added item", status(s))
        check(not in_front(), "the fixture stayed in the background")
        verdict, outcome = act("press", {"ref": urgent.ref}, s, label="Urgent")
        s = snap()
        check(outcome and outcome.ok and "urgent on" in status(s), "press toggles the Urgent checkbox", status(s))

        # 3. The gate.
        verdict, outcome = act("press", {"ref": send.ref}, s, label="Send Report")
        check(not verdict.proceed and verdict.blocked == gate.NEEDS_APPROVAL and outcome is None,
              "unapproved Send Report is refused", verdict.detail)
        verdict, outcome = act("key", {"combo": "enter"}, s)
        check(not verdict.proceed and verdict.blocked == gate.NEEDS_APPROVAL,
              "Enter on the default Send button is refused", verdict.detail)
        verdict, outcome = act("key", {"combo": "shift+delete"}, s)
        check(not verdict.proceed and verdict.blocked == gate.NEEDS_APPROVAL, "Shift+Delete needs approval", verdict.detail)
        verdict, outcome = act("key", {"combo": "win+r"}, s, approved=True)
        check(not verdict.proceed and outcome is None, "a Windows-key combo is refused even when approved", verdict.detail)
        if password is not None:
            verdict, outcome = act("set_value", {"ref": password.ref, "text": "hunter2"}, s, approved=True)
            check(not verdict.proceed and verdict.blocked == gate.SECURE_FIELD,
                  "password field is refused even when approved", verdict.detail)
            verdict, outcome = act("type_text", {"ref": password.ref, "text": "hunter2"}, s, approved=True)
            check(not verdict.proceed and verdict.blocked == gate.SECURE_FIELD,
                  "typing into the password field is refused", verdict.detail)
            # The driver asks the live element as well, whatever the gate was told.
            direct = driver.perform(gate.Action(id="x", tool="set_value", args={"ref": password.ref, "text": "x"}),
                                    s, lambda: True)
            check(not direct.ok and direct.blocked == gate.SECURE_FIELD, "the driver itself refuses a password field",
                  direct.detail)
        verdict, outcome = act("press", {"ref": send.ref}, s, label="Add Item")
        check(not verdict.proceed and verdict.blocked is None, "a changed target is refused", verdict.detail)
        s = snap()
        check("report sent" not in status(s), "nothing was sent by the refused actions", status(s))
        for denied in ("PowerShell", "cmd", "Registry Editor", "Task Manager"):
            opened, detail = driver.open_app(denied, [denied])
            check(opened is None, f"open_app refuses {denied}", detail)
        opened, detail = driver.open_app(FIXTURE_NAME, allowed)
        check(opened is not None and opened.pid == fixture.pid, "open_app finds the running fixture", detail)

        # 4. Typing and keys (the fixture comes to the front, then the person's window is put back).
        verdict, outcome = act("type_text", {"ref": notes.ref, "text": "hello world"}, s, label="Notes")
        s = snap()
        check(outcome and outcome.ok and "hello world" in s.outline.text, "type_text types into Notes",
              outcome.detail if outcome else verdict.detail)
        verdict, outcome = act("key", {"combo": "ctrl+k"}, s)
        s = snap()
        check(outcome and outcome.ok and "cleared" in status(s), "ctrl+k (Clear List) reaches the app", status(s))
        check(not in_front() or win32.window_pid(front_before) == fixture.pid, "the person's window is back in front")

        # 5. Approved Send.
        send = entry(s, "Send Report")
        if check(send is not None, "Send Report is still listed"):
            verdict, outcome = act("press", {"ref": send.ref}, s, approved=True, label="Send Report")
            s = snap()
            check("report sent" in status(s), "approved Send Report goes through", status(s))

        # 6. Menu commands.
        verdict, outcome = act("select_menu", {"path": ["List", "Clear List"]}, s)
        s = snap()
        check(outcome and outcome.ok and "cleared" in status(s), "select_menu List › Clear List",
              outcome.detail if outcome else verdict.detail)
        verdict, outcome = act("select_menu", {"path": ["List", "Delete Everything"]}, s)
        check(not verdict.proceed and verdict.blocked == gate.NEEDS_APPROVAL, "menu Delete needs approval", verdict.detail)
        verdict, outcome = act("select_menu", {"path": ["List", "Nope"]}, s)
        check(outcome is not None and not outcome.ok and "Clear List" in outcome.detail,
              "a missing menu item lists the real ones", outcome.detail if outcome else verdict.detail)

        # 7. Timing: how long one read takes.
        started = time.monotonic()
        snap()
        note("snapshot time", f"{(time.monotonic() - started) * 1000:.0f} ms")

        say("SELFTEST PASSED" if failures == 0 else f"SELFTEST FAILED ({failures})")
        return 0 if failures == 0 else 1
    finally:
        process.terminate()
        log.close()
