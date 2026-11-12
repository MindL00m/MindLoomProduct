"""Reads and operates Windows apps through UI Automation.

Windows only. Reading and most actions work on a window in the background: pressing a
button is the Invoke pattern, filling a field is the Value pattern. Typed keys only reach
the window in front, so ``type_text``, ``key`` and ``select_menu`` run with the app
brought forward (the runner announces that first and puts the person's window back).

Nothing here decides whether an action is allowed: :mod:`gate` does, before the runner
calls :meth:`WindowsDriver.perform`. Two checks are repeated here because only the live
element can answer them: a field is re-read as "not a password" right before it is
filled, and the focus is re-read once the app is in front, right before keys are sent.

Create the driver on the thread that will use it: it joins the COM multithreaded
apartment there.
"""

from __future__ import annotations

import os
import re
import sys
import threading
import time
from typing import Any, Callable

sys.coinit_flags = 0  # COINIT_MULTITHREADED, read by comtypes when it is first imported

import comtypes  # noqa: E402
import comtypes.client  # noqa: E402
from comtypes import COMError  # noqa: E402

from . import gate, keys, policy, win32  # noqa: E402
from .outline import SKIPPED_ROLES, OutlineNode, render  # noqa: E402
from .runner import App, Outcome, Snapshot  # noqa: E402

comtypes.client.GetModule("UIAutomationCore.dll")
from comtypes.gen import UIAutomationClient as UIA  # noqa: E402

# UI Automation control types (UIA_*ControlTypeId) by the names the outline uses.
CONTROL_TYPES = {
    50000: "Button", 50001: "Calendar", 50002: "CheckBox", 50003: "ComboBox", 50004: "Edit",
    50005: "Hyperlink", 50006: "Image", 50007: "ListItem", 50008: "List", 50009: "Menu",
    50010: "MenuBar", 50011: "MenuItem", 50012: "ProgressBar", 50013: "RadioButton", 50014: "ScrollBar",
    50015: "Slider", 50016: "Spinner", 50017: "StatusBar", 50018: "Tab", 50019: "TabItem", 50020: "Text",
    50021: "ToolBar", 50022: "ToolTip", 50023: "Tree", 50024: "TreeItem", 50025: "Custom", 50026: "Group",
    50027: "Thumb", 50028: "DataGrid", 50029: "DataItem", 50030: "Document", 50031: "SplitButton",
    50032: "Window", 50033: "Pane", 50034: "Header", 50035: "HeaderItem", 50036: "Table",
    50037: "TitleBar", 50038: "Separator",
}
MENU_TYPE, MENU_BAR_TYPE, MENU_ITEM_TYPE = 50009, 50010, 50011
BUTTON_ROLES = frozenset({"Button", "SplitButton", "Hyperlink", "MenuItem"})

# Properties read with every element in one call (UIA_*PropertyId).
P_PROCESS_ID, P_CONTROL_TYPE, P_NAME = 30002, 30003, 30005
P_HAS_FOCUS, P_IS_ENABLED, P_AUTOMATION_ID, P_IS_PASSWORD, P_IS_OFFSCREEN = 30008, 30010, 30011, 30019, 30022
P_VALUE, P_EXPAND_STATE, P_IS_SELECTED, P_TOGGLE_STATE, P_LEGACY_STATE = 30045, 30070, 30079, 30086, 30096
CACHED = (P_PROCESS_ID, P_CONTROL_TYPE, P_NAME, P_HAS_FOCUS, P_IS_ENABLED, P_AUTOMATION_ID, P_IS_PASSWORD,
          P_IS_OFFSCREEN, P_VALUE, P_EXPAND_STATE, P_IS_SELECTED, P_TOGGLE_STATE, P_LEGACY_STATE)

# Patterns (UIA_*PatternId).
PAT_INVOKE, PAT_VALUE, PAT_SCROLL, PAT_EXPAND, PAT_SELECTION_ITEM, PAT_TOGGLE, PAT_LEGACY = (
    10000, 10002, 10004, 10005, 10010, 10015, 10018)

STATE_SYSTEM_DEFAULT = 0x100  # the button Enter presses
TREE_SCOPE_CHILDREN, TREE_SCOPE_DESCENDANTS = 0x2, 0x4
SCROLL_NO_AMOUNT, SCROLL_LARGE_DECREMENT, SCROLL_LARGE_INCREMENT = 2, 0, 3

MAX_NODES = 1500
MAX_DEPTH = 30
CALL_TIMEOUT = 4.0
OPEN_WAIT_SECONDS = 12.0
_LAUNCH_NAME = re.compile(r"[\w .+&()'-]{1,60}")


def _clean_menu_name(name: str) -> str:
    """"&Save As...\\tCtrl+Shift+S" → "save as"."""

    text = str(name or "").split("\t", 1)[0].replace("&", "")
    return " ".join(text.replace("…", "").rstrip(". ").lower().split())


class WindowsDriver:
    def __init__(self) -> None:
        try:
            comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        except OSError:
            pass  # this thread already joined an apartment
        try:
            self.uia = comtypes.client.CreateObject(UIA.CUIAutomation8, interface=UIA.IUIAutomation2)
            self.uia.TransactionTimeout = 5000  # a hung app must not hang the run
            self.uia.ConnectionTimeout = 3000
        except (COMError, AttributeError, OSError):
            self.uia = comtypes.client.CreateObject(UIA.CUIAutomation, interface=UIA.IUIAutomation)
        self.walker = self.uia.ControlViewWalker
        self.cache = self.uia.CreateCacheRequest()
        for property_id in CACHED:
            self.cache.AddProperty(property_id)
        self.own_pid = os.getpid()
        # The window being worked in, per app (focus_window changes it).
        self._working: dict[int, int] = {}
        # Apps this driver started from an allowed name, by process: the name they were allowed under.
        self._aliases: dict[int, str] = {}

    # --- Apps ---------------------------------------------------------------------------------

    def _names(self, pid: int) -> tuple[str, ...]:
        names = win32.process_names(pid)
        alias = self._aliases.get(pid)
        return names + ((alias,) if alias and names else ())

    def _app(self, pid: int, allowed: list[str]) -> App | None:
        """The app behind ``pid`` if the person allowed it; never this app, never a denied one."""

        if not pid or pid == self.own_pid:
            return None
        names = self._names(pid)
        if not names or not policy.is_allowed(names, allowed):
            return None
        wanted = {policy.normalize_app(a): a for a in allowed}
        shown = next((wanted[policy.normalize_app(n)] for n in names if policy.normalize_app(n) in wanted), names[0])
        return App(pid=pid, name=shown, names=names)

    def running_allowed_apps(self, allowed: list[str]) -> list[App]:
        apps: dict[int, App] = {}
        for _, pid, _ in win32.top_level_windows():
            if pid not in apps:
                app = self._app(pid, allowed)
                if app:
                    apps[pid] = app
        return list(apps.values())

    def preferred_app(self, allowed: list[str]) -> App | None:
        """An allowed app the person has in front, else any allowed app that's open."""

        running = self.running_allowed_apps(allowed)
        front = win32.foreground_pid()
        return next((a for a in running if a.pid == front), running[0] if running else None)

    def is_running(self, app: App) -> bool:
        return win32.process_alive(app.pid)

    def is_allowed(self, app: App, allowed: list[str]) -> bool:
        return self._app(app.pid, allowed) is not None

    def open_app(self, name: str, allowed: list[str]) -> tuple[App | None, str]:
        if policy.is_denied([name]) or not policy.is_allowed([name], allowed):
            return None, f"{name} isn't one of the apps allowed for this automation."
        wanted = policy.normalize_app(name)

        def running() -> App | None:
            return next((a for a in self.running_allowed_apps(allowed)
                         if wanted in {policy.normalize_app(n) for n in a.names}), None)

        app = running()
        if app:
            return app, f"{app.name} is open."
        if not _LAUNCH_NAME.fullmatch(name.strip()):
            return None, f"MindLoom can't start “{name}” by that name. Ask the person to open it."
        # A Start menu shortcut with that name, else the program by name ("excel" → excel.exe,
        # which Windows finds through its App Paths).
        stem = re.sub(r"\.(exe|lnk)$", "", name.strip(), flags=re.IGNORECASE)
        target = self._start_menu_shortcut(wanted) or stem + ".exe"
        started, pid = win32.start(target)
        if not started:
            return None, f"Windows could not start {name}. Ask the person to open it."
        deadline = time.monotonic() + OPEN_WAIT_SECONDS
        while time.monotonic() < deadline:
            time.sleep(0.5)
            if pid and any(p == pid for _, p, _ in win32.top_level_windows()):
                # Started from the allowed name: the process counts as that app, unless it is
                # a denied one (a shortcut can point anywhere).
                if not policy.is_denied(win32.process_names(pid)):
                    self._aliases[pid] = name
            app = running()
            if app:
                return app, f"Opened {app.name}."
        return None, (f"Started {name}, but no window of an allowed app appeared. It may need the person "
                      "(a sign-in or a prompt): call ask_person.")

    @staticmethod
    def _start_menu_shortcut(wanted: str) -> str | None:
        roots = [os.path.join(os.environ.get(variable, ""), "Microsoft", "Windows", "Start Menu", "Programs")
                 for variable in ("ProgramData", "APPDATA") if os.environ.get(variable)]
        for root in roots:
            for folder, _, files in os.walk(root):
                for file in files:
                    if file.lower().endswith(".lnk") and policy.normalize_app(file) == wanted:
                        return os.path.join(folder, file)
        return None

    # --- The foreground and the person ---------------------------------------------------------

    def is_in_front(self, app: App) -> bool:
        return win32.foreground_pid() == app.pid

    def bring_forward(self, app: App) -> Any:
        """Bring the app's working window to the front. Returns what was in front (to put
        back), or None when Windows refused."""

        hwnd = self._working_window(app.pid)
        if not hwnd:
            return None
        previous = win32.foreground_window()
        return (previous,) if win32.bring_to_front(hwnd) else None

    def restore_front(self, token: Any) -> None:
        previous = token[0] if token else 0
        if previous and win32.window_pid(previous) not in (0, self.own_pid) and not win32.is_minimized(previous):
            win32.bring_to_front(previous)

    def person_active(self) -> bool:
        return win32.person_active()

    def reset_input(self) -> None:
        win32.reset_input()

    # --- Reading ---------------------------------------------------------------------------------

    def _windows(self, pid: int) -> list[tuple[int, str]]:
        return [(hwnd, title) for hwnd, owner, title in win32.top_level_windows() if owner == pid]

    def _working_window(self, pid: int) -> int:
        windows = self._windows(pid)
        handles = [hwnd for hwnd, _ in windows]
        chosen = self._working.get(pid)
        if chosen in handles and win32.is_enabled(chosen):
            return chosen
        front = win32.foreground_window()
        if front in handles:
            return front
        # A dialog disables the window behind it: the first enabled one is where work happens.
        return next((h for h in handles if win32.is_enabled(h)), handles[0] if handles else 0)

    @staticmethod
    def _get(element: Any, name: str, default: Any) -> Any:
        try:
            value = getattr(element, name)
            return default if value is None else value
        except (COMError, OSError, ValueError):
            return default

    @staticmethod
    def _property(element: Any, property_id: int) -> Any:
        """A cached pattern property, or None when the element does not have that pattern."""

        try:
            value = element.GetCachedPropertyValueEx(property_id, 1)
        except (COMError, OSError, ValueError):
            return None
        return value if isinstance(value, (bool, int, str)) else None

    def _node(self, element: Any, handles: dict[int, Any]) -> OutlineNode:
        role = CONTROL_TYPES.get(int(self._get(element, "CachedControlType", 0)), "Custom")
        secure = bool(self._get(element, "CachedIsPassword", False))
        value = ""
        if not secure:  # a password field's value is never read
            if role == "CheckBox":
                state = self._property(element, P_TOGGLE_STATE)
                value = {0: "off", 1: "on", 2: "mixed"}.get(state, "") if state is not None else ""
            else:
                raw = self._property(element, P_VALUE)
                value = raw if isinstance(raw, str) else ""
        expand = self._property(element, P_EXPAND_STATE)
        legacy = self._property(element, P_LEGACY_STATE)
        handle = len(handles) + 1
        handles[handle] = element
        return OutlineNode(
            handle=handle, role=role, label=str(self._get(element, "CachedName", "") or ""), value=value,
            enabled=bool(self._get(element, "CachedIsEnabled", True)),
            focused=bool(self._get(element, "CachedHasKeyboardFocus", False)),
            selected=self._property(element, P_IS_SELECTED) is True, secure=secure,
            expanded={0: False, 1: True, 2: True}.get(expand) if isinstance(expand, int) else None,
            is_default=role == "Button" and isinstance(legacy, int) and bool(legacy & STATE_SYSTEM_DEFAULT),
        )

    def _walk(self, root: Any, handles: dict[int, Any], skip_offscreen: bool) -> tuple[OutlineNode, list[str], Any]:
        """The element tree under ``root`` as outline nodes, plus the menu bar's item names
        and the menu bar element."""

        budget = [MAX_NODES]
        menu_names: list[str] = []
        menu_bar: list[Any] = []

        def children_of(element: Any, depth: int) -> list[OutlineNode]:
            out: list[OutlineNode] = []
            if depth >= MAX_DEPTH:
                return out
            try:
                child = self.walker.GetFirstChildElementBuildCache(element, self.cache)
            except (COMError, OSError):
                return out
            while child and budget[0] > 0:
                budget[0] -= 1
                role = CONTROL_TYPES.get(int(self._get(child, "CachedControlType", 0)), "Custom")
                offscreen = skip_offscreen and bool(self._get(child, "CachedIsOffscreen", False))
                if role not in SKIPPED_ROLES and not offscreen:
                    node = self._node(child, handles)
                    node.children = children_of(child, depth + 1)
                    if role == "MenuBar" and not menu_bar \
                            and str(self._get(child, "CachedAutomationId", "")) != "SystemMenuBar":
                        menu_bar.append(child)
                        menu_names.extend(c.label for c in node.children if c.role == "MenuItem" and c.label)
                    out.append(node)
                try:
                    child = self.walker.GetNextSiblingElementBuildCache(child, self.cache)
                except (COMError, OSError):
                    break
            return out

        top = self._node(root, handles)
        top.children = children_of(root, 0)
        return top, menu_names, (menu_bar[0] if menu_bar else None)

    def snapshot(self, app: App | None, allowed: list[str]) -> Snapshot:
        running = self.running_allowed_apps(allowed)
        running_names = [a.name[:60] for a in running][:30]

        def empty(text: str, name: str = "") -> Snapshot:
            outline = render([], header=[text])
            return Snapshot(outline=outline, content=outline.text, app_name=name, observation={
                "app": name[:120], "bundle_id": "", "text": outline.text, "elements": [],
                "running_apps": running_names})

        if app is None:
            return empty("No allowed app is open. Use open_app.")
        allowed_now = self._app(app.pid, allowed) is not None
        hwnd = self._working_window(app.pid)
        if not hwnd:
            return empty(f"{app.name} has no open window. Use open_app, or ask the person.", app.name)

        windows = self._windows(app.pid)
        title = next((t for h, t in windows if h == hwnd), "")
        state = ("minimized" if win32.is_minimized(hwnd)
                 else "in front" if win32.foreground_window() == hwnd else "in the background")
        header = [f'Window: "{title}" ({state})']
        others = [t for h, t in windows if h != hwnd][:10]
        if others:
            header.append("Other windows: " + ", ".join(f'"{t[:80]}"' for t in others))

        handles: dict[int, Any] = {}
        try:
            root = self.uia.ElementFromHandleBuildCache(hwnd, self.cache)
            top, menu_names, menu_bar = self._walk(root, handles, skip_offscreen=True)
            if not top.children and not win32.is_minimized(hwnd):
                # Some apps report everything as off screen: read it all rather than nothing.
                handles.clear()
                top, menu_names, menu_bar = self._walk(root, handles, skip_offscreen=False)
        except (COMError, OSError):
            return empty(f"{app.name} is not answering UI Automation right now. Wait, then look again.", app.name)
        if menu_names:
            header.append("Menu bar: " + ", ".join(menu_names[:20]))
        if win32.is_minimized(hwnd):
            header.append("(The window is minimized: focus_window brings it back.)")

        outline = render(top.children, header=header)
        default_button = next((e.label for e in outline.entries if e.is_default and e.label), None)
        focused = next((e for e in outline.entries if e.focused), None)
        handles[0] = root
        handles[-1] = menu_bar
        return Snapshot(
            outline=outline, pid=app.pid, app_name=app.name, app_allowed=allowed_now,
            default_button_label=default_button,
            focused_button_label=focused.label if focused and focused.role in BUTTON_ROLES else None,
            focused_is_secure=bool(focused and focused.secure),
            content="\n".join(outline.text.split("\n")[1:]), handles=handles,
            observation={
                "app": app.name[:120], "bundle_id": (app.names[0] if app.names else "")[:200],
                "text": outline.text[:60_000], "elements": [e.summary() for e in outline.entries],
                "running_apps": running_names,
            },
        )

    # --- Acting ----------------------------------------------------------------------------------

    @staticmethod
    def _run(call: Callable[[], Any], timeout: float = CALL_TIMEOUT) -> tuple[bool, BaseException | None]:
        """Run a UI Automation call that may block (a button that opens a dialog does not
        return until the dialog closes). Returns (finished in time, error)."""

        box: dict[str, BaseException] = {}

        def work() -> None:
            comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
            try:
                call()
            except BaseException as error:  # noqa: BLE001
                box["error"] = error
            finally:
                comtypes.CoUninitialize()

        thread = threading.Thread(target=work, name="mindloom-uia-call", daemon=True)
        thread.start()
        thread.join(timeout)
        return not thread.is_alive(), box.get("error")

    @staticmethod
    def _pattern(element: Any, pattern_id: int, interface: Any) -> Any:
        try:
            unknown = element.GetCurrentPattern(pattern_id)
            return unknown.QueryInterface(interface) if unknown else None
        except (COMError, OSError, ValueError):
            return None

    def _done(self, call: Callable[[], Any], what: str) -> Outcome:
        finished, error = self._run(call)
        if error is not None:
            return Outcome(False, f"The app refused ({type(error).__name__}). Look again and try another way.")
        if not finished:
            return Outcome(True, f"{what}. The app has not answered yet (it may be showing a dialog): look again.")
        return Outcome(True, f"{what}.")

    def perform(self, action: gate.Action, snapshot: Snapshot, keep_going: Callable[[], bool]) -> Outcome:
        tool = action.tool
        pid = snapshot.pid or 0
        element = None
        entry = snapshot.outline.entry(action.ref)
        if entry is not None:
            element = snapshot.handles.get(entry.handle)
        if tool in {"press", "set_value", "scroll"} and element is None:
            return Outcome(False, f"The element {action.ref or '?'} is no longer on screen. Look again.")

        if tool == "focus_window":
            return self._focus_window(pid, action.string("title"))
        if tool == "press":
            return self._press(element, entry.role if entry else "", entry.label if entry else "")
        if tool == "set_value":
            return self._set_value(element, action.string("text"))
        if tool == "scroll":
            return self._scroll(element, action.string("direction"))
        if tool == "select_menu":
            return self._select_menu(snapshot, action.path)
        if tool in {"type_text", "key"}:
            return self._keys(action, pid, element, keep_going)
        return Outcome(False, f"MindLoom doesn't know how to “{tool}”.")

    def _focus_window(self, pid: int, title: str) -> Outcome:
        windows = self._windows(pid)
        wanted = title.strip().lower()
        match = next((h for h, t in windows if wanted and wanted in t.lower()), None)
        if match is None:
            names = ", ".join(f"“{t[:80]}”" for _, t in windows[:10]) or "none"
            return Outcome(False, f"No window with “{title}” in its title. Open windows: {names}.")
        self._working[pid] = match
        if win32.is_minimized(match):
            win32.show_without_activating(match)
        return Outcome(True, f"Now working in “{win32.window_title(match)[:120]}”.")

    def _press(self, element: Any, role: str, label: str) -> Outcome:
        if not self._get(element, "CurrentIsEnabled", True):
            return Outcome(False, f"“{label}” is disabled right now.")
        what = f"Pressed “{label}”" if label else "Pressed it"
        toggle = self._pattern(element, PAT_TOGGLE, UIA.IUIAutomationTogglePattern)
        select = self._pattern(element, PAT_SELECTION_ITEM, UIA.IUIAutomationSelectionItemPattern)
        expand = self._pattern(element, PAT_EXPAND, UIA.IUIAutomationExpandCollapsePattern)
        invoke = self._pattern(element, PAT_INVOKE, UIA.IUIAutomationInvokePattern)

        def flip() -> None:
            (expand.Collapse if expand.CurrentExpandCollapseState == 1 else expand.Expand)()

        if role == "CheckBox" and toggle:
            return self._done(toggle.Toggle, what)
        if role in {"RadioButton", "ListItem", "TabItem", "DataItem"} and select:
            return self._done(select.Select, f"Selected “{label}”")
        if role == "TreeItem" and (select or expand):
            def open_branch() -> None:
                if select:
                    select.Select()
                if expand and expand.CurrentExpandCollapseState == 0:
                    expand.Expand()
            return self._done(open_branch, f"Selected “{label}”")
        if role == "ComboBox" and expand:
            return self._done(flip, f"Opened “{label}”")
        if invoke:
            return self._done(invoke.Invoke, what)
        if toggle:
            return self._done(toggle.Toggle, what)
        if select:
            return self._done(select.Select, f"Selected “{label}”")
        if expand:
            return self._done(flip, f"Opened “{label}”")
        legacy = self._pattern(element, PAT_LEGACY, UIA.IUIAutomationLegacyIAccessiblePattern)
        if legacy:
            return self._done(legacy.DoDefaultAction, what)
        return Outcome(False, f"“{label}” can't be pressed through UI Automation. Try key (tab to it, then "
                              "enter or space), or ask the person.")

    def _set_value(self, element: Any, text: str) -> Outcome:
        # Asked of the live element, whatever the outline said a moment ago.
        if self._get(element, "CurrentIsPassword", True):
            return Outcome(False, "That is a password field. MindLoom never fills those: ask the person.",
                           gate.SECURE_FIELD)
        if not self._get(element, "CurrentIsEnabled", True):
            return Outcome(False, "That field is disabled right now.")
        value = self._pattern(element, PAT_VALUE, UIA.IUIAutomationValuePattern)
        if value is None:
            return Outcome(False, "That field can't be set directly. Use type_text with its ref.")
        if self._get(value, "CurrentIsReadOnly", False):
            return Outcome(False, "That field is read-only.")
        finished, error = self._run(lambda: value.SetValue(text))
        if error is not None or not finished:
            return Outcome(False, "The field did not take the value. Use type_text with its ref.")
        shown = str(self._get(value, "CurrentValue", "") or "")
        if shown.replace("\r\n", "\n").strip() == text.replace("\r\n", "\n").strip():
            return Outcome(True, f"The field now shows “{shown[:200]}”.")
        return Outcome(False, f"The field shows “{shown[:200]}” instead. Use type_text with its ref.")

    def _scroll(self, element: Any, direction: str) -> Outcome:
        amount = SCROLL_LARGE_INCREMENT if direction == "down" else SCROLL_LARGE_DECREMENT
        current = element
        for _ in range(8):
            scroll = self._pattern(current, PAT_SCROLL, UIA.IUIAutomationScrollPattern)
            if scroll and self._get(scroll, "CurrentVerticallyScrollable", False):
                return self._done(lambda: scroll.Scroll(SCROLL_NO_AMOUNT, amount), f"Scrolled {direction}")
            try:
                current = self.walker.GetParentElement(current)
            except (COMError, OSError):
                break
            if not current:
                break
        return Outcome(False, "Nothing there scrolls. Try key pagedown or pageup with the list focused.")

    def _live_focus(self, pid: int) -> tuple[str | None, bool, bool]:
        """(focused button's label, focus is a password field, focus is in this app)."""

        try:
            focused = self.uia.GetFocusedElementBuildCache(self.cache)
        except (COMError, OSError):
            return None, False, False
        if not focused or int(self._get(focused, "CachedProcessId", 0)) != pid:
            return None, False, False
        role = CONTROL_TYPES.get(int(self._get(focused, "CachedControlType", 0)), "Custom")
        label = str(self._get(focused, "CachedName", "") or "")
        return (label if role in BUTTON_ROLES else None), bool(self._get(focused, "CachedIsPassword", False)), True

    def _keys(self, action: gate.Action, pid: int, element: Any, keep_going: Callable[[], bool]) -> Outcome:
        if win32.foreground_pid() != pid:
            return Outcome(False, "The app is not in front, so keys would go elsewhere. Nothing was typed.")
        if action.tool == "type_text" and element is not None:
            if self._get(element, "CurrentIsPassword", True):
                return Outcome(False, "That is a password field. MindLoom never fills those: ask the person.",
                               gate.SECURE_FIELD)
            finished, error = self._run(element.SetFocus)
            if error is not None or not finished:
                return Outcome(False, "That field did not take the keyboard focus. Press it first, then type.")
            time.sleep(0.15)

        # Keys go wherever the focus is now, so look once more.
        button, secure, in_app = self._live_focus(pid)
        verdict = gate.check_focus_now(action, focused_button_label=button, focused_is_secure=secure)
        if not verdict.proceed:
            return Outcome(False, verdict.detail, verdict.blocked)
        if win32.foreground_pid() != pid:
            return Outcome(False, "The app is no longer in front. Nothing was typed.")

        if action.tool == "key":
            combo = keys.parse(action.string("combo"))
            if keys.blocked_reason(combo):
                return Outcome(False, keys.blocked_reason(combo) or "")
            vk = combo.virtual_key
            if len(combo.key) == 1 and not combo.key.isalnum():
                vk = win32.layout_vk(combo.key) or vk
            if vk is None:
                return Outcome(False, f"MindLoom can't press “{combo.key}”.")
            modifiers = [keys.MODIFIER_VK[m] for m in combo.modifiers]
            ok = win32.press_combo(modifiers, vk, combo.key in keys.EXTENDED_KEYS)
            return Outcome(ok, f"Pressed {combo.normalized}." if ok else "Windows did not take the key.")

        def still_ours() -> bool:
            return keep_going() and win32.foreground_pid() == pid

        text = action.string("text")
        finished, typed = win32.type_text(text, still_ours)
        if finished:
            return Outcome(True, f"Typed {typed} characters.")
        return Outcome(False, f"Stopped after {typed} of {len(text)} characters: the app was no longer in front "
                              "or the run was stopped. Look again before typing more.")

    # --- Menus -----------------------------------------------------------------------------------

    def _menu_items(self, parent: Any) -> list[tuple[str, Any]]:
        condition = self.uia.CreatePropertyCondition(P_CONTROL_TYPE, MENU_ITEM_TYPE)
        try:
            found = parent.FindAll(TREE_SCOPE_DESCENDANTS, condition)
            return [(str(self._get(found.GetElement(i), "CurrentName", "") or ""), found.GetElement(i))
                    for i in range(found.Length)]
        except (COMError, OSError):
            return []

    def _popup_items(self, pid: int) -> list[tuple[str, Any]]:
        """Items of the menus that are open right now. A classic menu opens as a window of
        its own, next to the app's window."""

        items: list[tuple[str, Any]] = []
        condition = self.uia.CreateAndCondition(
            self.uia.CreatePropertyCondition(P_CONTROL_TYPE, MENU_TYPE),
            self.uia.CreatePropertyCondition(P_PROCESS_ID, pid))
        try:
            menus = self.uia.GetRootElement().FindAll(TREE_SCOPE_CHILDREN, condition)
            for index in range(menus.Length):
                items += self._menu_items(menus.GetElement(index))
        except (COMError, OSError):
            pass
        return items

    def _select_menu(self, snapshot: Snapshot, path: list[str]) -> Outcome:
        menu_bar = snapshot.handles.get(-1)
        pid = snapshot.pid or 0
        if menu_bar is None:
            return Outcome(False, "This window has no menu bar. Press its buttons or tabs instead, or use key.")
        if win32.foreground_pid() != pid:
            return Outcome(False, "The app is not in front, so its menu can't be opened.")

        def find(items: list[tuple[str, Any]], name: str) -> Any:
            wanted = _clean_menu_name(name)
            return next((e for n, e in items if _clean_menu_name(n) == wanted), None)

        def names(items: list[tuple[str, Any]]) -> str:
            return ", ".join(dict.fromkeys(n.split("\t", 1)[0].replace("&", "") for n, _ in items if n)) or "none"

        def close() -> None:
            win32.press_combo([], keys.NAMED_VK["escape"])
            win32.press_combo([], keys.NAMED_VK["escape"])

        items = self._menu_items(menu_bar)
        opened: list[str] = []
        for index, name in enumerate(path):
            item = find(items, name)
            if item is None:
                if opened:
                    close()
                where = " › ".join(opened) or "the menu bar"
                return Outcome(False, f"No “{name}” in {where}. It has: {names(items)}.")
            if not self._get(item, "CurrentIsEnabled", True):
                if opened:
                    close()
                return Outcome(False, f"“{name}” is disabled right now.")
            last = index == len(path) - 1
            invoke = self._pattern(item, PAT_INVOKE, UIA.IUIAutomationInvokePattern)
            expand = self._pattern(item, PAT_EXPAND, UIA.IUIAutomationExpandCollapsePattern)
            if last:
                call = invoke.Invoke if invoke else (expand.Expand if expand else None)
                if call is None:
                    close()
                    return Outcome(False, f"“{name}” can't be chosen through UI Automation.")
                return self._done(call, "Chose " + " › ".join(path))
            call = expand.Expand if expand else (invoke.Invoke if invoke else None)
            if call is None:
                return Outcome(False, f"“{name}” does not open.")
            self._run(call, timeout=2.0)
            opened.append(name)
            time.sleep(0.3)
            # The opened menu's items: inside the item (menu strips) or in a popup window (classic menus).
            items = self._menu_items(item) or self._popup_items(pid)
        return Outcome(False, "Give the menu path, e.g. [\"File\", \"Save\"].")
