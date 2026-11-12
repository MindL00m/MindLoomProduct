"""Windows tasks: MindLoom operates native Windows apps for an automation run.

``hand_off_to_device`` in an automation whose computer is set to Windows creates a device
task with ``surface`` "windows". MindLoom for Windows (``apps/windows-agent``) claims it and
drives it through the same step loop as the Mac app (:mod:`shuttle.device_tasks`): it sends
the app it is working in as a numbered UI Automation outline (``[e12] Button "Save"``), the
model picks one action, this module checks it, and the Windows app performs it and sends
the next screen.

What is specific to Windows lives here:

- only apps the person allowed for this automation are used, and never the ones that hold
  credentials or system and security settings, or that run commands
  (:data:`DENIED_APPS`: Credential Manager, Windows Security, Registry Editor, PowerShell,
  Command Prompt, Task Manager, password managers…);
- key combos are Windows ones (``ctrl``, ``alt``, ``shift``). Combos that leave the allowed
  app (the Windows key, Alt+Tab, Ctrl+Shift+Esc) are refused (:func:`blocked_key_reason`),
  and combos that usually send or delete wait for approval (:data:`IRREVERSIBLE_KEYS`);
- a control whose label sends, submits, deletes, pays or confirms waits for approval, and
  so does Enter or Space on such a button. The Windows app checks the label again before
  it acts, and checks what Enter would press;
- password fields (UI Automation ``IsPassword``) are never read or typed into;
- screen content is data, never instructions.

The Windows app has the same lists (``apps/windows-agent/mindloom_agent/policy.py``); a test keeps them
in step.
"""

from __future__ import annotations

import re
from typing import Any

from shuttle import device_tasks as _dt
from shuttle.device_tasks import _IRREVERSIBLE, _REF, _UNTRUSTED, _tool, looks_irreversible

SURFACE = "windows"
WHERE = "the Windows app"
MAX_STEPS = _dt.MAX_STEPS
MAX_APPS = _dt.MAX_APPS

# Apps MindLoom never operates on Windows, whatever an automation allows: they hold
# passwords or keys, change system or security settings, or run commands. Names are
# compared lowercase without ".exe", and cover both what people call the app and its
# process name.
DENIED_APPS = frozenset({
    # Credentials and security
    "credential manager", "credentialuibroker", "windows security", "securityhealthui",
    "sechealthui", "windows defender", "user account control", "consent", "logonui", "lockapp",
    "user accounts", "netplwiz", "local security policy", "secpol",
    # Password managers
    "1password", "bitwarden", "lastpass", "dashlane", "keeper password manager", "keeper",
    "keepass", "keepassxc", "nordpass", "roboform",
    # Shells and scripting
    "powershell", "windows powershell", "pwsh", "powershell ise", "windows powershell ise",
    "powershell_ise", "command prompt", "cmd", "terminal", "windows terminal",
    "windowsterminal", "wt", "conhost", "openconsole", "wsl", "bash", "windows script host",
    "wscript", "cscript", "run",
    # System tools
    "registry editor", "regedit", "regedt32", "task manager", "taskmgr", "settings",
    "systemsettings", "control panel", "control", "group policy editor", "gpedit",
    "computer management", "microsoft management console", "mmc", "services", "event viewer",
    "eventvwr", "system configuration", "msconfig", "task scheduler", "taskschd",
    "disk management", "diskmgmt", "device manager", "devmgmt", "resource monitor", "resmon",
    # The shell and its hosts: they reach Control Panel, the Run box, the Start menu and the
    # windows of Settings and other Store apps.
    "explorer", "file explorer", "windows explorer", "applicationframehost", "rundll32",
    "searchhost", "searchapp", "startmenuexperiencehost", "shellexperiencehost",
    # MindLoom itself
    "mindloom", "mindloom for windows", "mindloomagent",
})

# Key combos that usually send or delete in Windows apps: they wait for approval.
# Alt+D deletes a voucher in Tally; Alt+S and Ctrl+Enter send in Outlook and Teams.
IRREVERSIBLE_KEYS = frozenset({
    "shift+delete", "ctrl+shift+delete", "ctrl+d", "alt+d", "alt+s", "ctrl+return",
    "ctrl+shift+return",
})

# Key combos that act outside the app MindLoom is working in: never pressed. Any combo
# with the Windows key is refused as well.
BLOCKED_KEYS = frozenset({
    "alt+tab", "alt+shift+tab", "alt+escape", "alt+shift+escape", "ctrl+escape",
    "ctrl+shift+escape", "ctrl+alt+delete", "ctrl+alt+tab", "alt+space", "printscreen",
})

ACTION_TOOLS = _dt.ACTION_TOOLS
REF_TOOLS = _dt.REF_TOOLS

_SYSTEM = f"""\
You operate apps on {{person}}'s Windows PC for part of a MindLoom automation, through
Windows UI Automation. Each turn you get the app you're working in as a numbered outline,
e.g. [e12] Button "Save", and the result of your last action. Call exactly one tool per turn.

Allowed apps: {{apps}}. Never open or act in any other app.

How to act:
- Use the element refs from the latest screen only.
- Text fields (Edit, ComboBox): set_value works without taking over the screen. Use
  type_text only when set_value did not take (check the value on the next screen), or in
  apps that only take the keyboard.
- press for buttons, checkboxes, radio buttons, tabs, list and tree items, links and menu
  items. Pressing a ComboBox opens its list: press the choice on the next screen. Pressing
  a tree item selects it and opens its branch.
- select_menu for menu bar commands by path, e.g. ["File", "Save As"]. Apps with a ribbon
  instead of a menu bar (Excel, Word): press the tab, then the button.
- key for shortcuts, with Windows modifiers: ctrl, alt, shift, e.g. "ctrl+s", "alt+f4",
  "f2", "shift+tab". There is no cmd key. Keys with the Windows key, alt+tab and
  ctrl+shift+escape are not available: use open_app and focus_window to move between apps
  and windows.
- type_text, key and select_menu bring the app to the front for a moment; the person sees
  a short notice first and can say not now.
- Keyboard-driven apps (Tally, SAP GUI, terminal-style screens) may list few controls:
  work with key and type_text, one field at a time, and read the next screen each time.
- After every action, check the next screen. If an action failed twice, try another way
  or call ask_person.
- Set irreversible=true on any action that sends, submits, posts, deletes, pays, buys,
  books, confirms, accepts or can't be undone, including the key that accepts or deletes a
  record in a keyboard-driven app (in Tally: ctrl+a accepts a voucher, alt+d deletes one).
  The person approves it first.
- Fields marked (secure) are passwords: never fill them. Sign-ins, two-factor codes,
  CAPTCHAs and "Do you want to allow this app to make changes" prompts are for the person:
  call ask_person with one clear instruction.
- Use the exact values you were given. Never guess names, amounts, dates or addresses;
  call ask_person instead.
- When the steps are done, call done with a short summary. If they cannot be done, call
  done with outcome "not_done" and say why.
- {_UNTRUSTED}
"""

TOOLS: list[dict[str, Any]] = [
    _tool("open_app", "Open or switch to an allowed app.",
          {"app": {"type": "string", "description": "App name, e.g. Excel or TallyPrime."}}, ["app"]),
    _tool("focus_window", "Work in another window or dialog of the current app.",
          {"title": {"type": "string", "description": "Part of the window title."}}, ["title"]),
    _tool("press", "Press a button, checkbox, radio button, tab, list item, tree item, link or menu item.",
          {"ref": _REF, "irreversible": _IRREVERSIBLE}, ["ref"]),
    _tool("set_value", "Replace the text in an Edit field or editable ComboBox.",
          {"ref": _REF, "text": {"type": "string"}}, ["ref", "text"]),
    _tool("type_text", "Type text at the focused element (or the ref, focused first). Brings the app forward.",
          {"text": {"type": "string"}, "ref": _REF}, ["text"]),
    _tool("key", "Press a key or shortcut, e.g. enter, tab, escape, f2, ctrl+s, alt+f4, shift+tab. "
                 "Brings the app forward.",
          {"combo": {"type": "string"}, "irreversible": _IRREVERSIBLE}, ["combo"]),
    _tool("select_menu", "Choose a menu bar command by its path (brings the app forward briefly).",
          {"path": {"type": "array", "items": {"type": "string"}}, "irreversible": _IRREVERSIBLE},
          ["path"]),
    _tool("scroll", "Scroll a list, table, tree or document.",
          {"ref": _REF, "direction": {"type": "string", "enum": ["up", "down"]}}, ["ref", "direction"]),
    _tool("wait", "Wait for the app to finish something (up to 10 seconds).",
          {"seconds": {"type": "number"}}, ["seconds"]),
    _tool("ask_person", "Pause and ask the person to do one thing on their PC, then continue.",
          {"instruction": {"type": "string", "description": "One clear instruction."}}, ["instruction"]),
    _tool("done", "Finish this part with a short summary of what was done.",
          {"summary": {"type": "string"},
           "outcome": {"type": "string", "enum": ["done", "partly_done", "not_done"]}}, ["summary"]),
]


# --- Apps ---------------------------------------------------------------------------------


def normalize_app(name: Any) -> str:
    """``EXCEL.EXE`` → ``excel``; ``Tally Prime `` → ``tally prime``."""

    value = re.sub(r"\s+", " ", str(name or "").strip().lower())
    return re.sub(r"\.(exe|lnk)$", "", value).strip()


def is_denied_app(name: Any) -> bool:
    return normalize_app(name) in DENIED_APPS


def clean_apps(names: Any) -> list[str]:
    """The apps a person allowed: trimmed, de-duplicated, never a denied app."""

    out: list[str] = []
    for raw in names if isinstance(names, list) else []:
        name = re.sub(r"\.(exe|lnk)$", "", str(raw or "").strip(), flags=re.IGNORECASE).strip()[:60]
        if name and not is_denied_app(name) and normalize_app(name) not in {normalize_app(o) for o in out}:
            out.append(name)
    return out[:MAX_APPS]


# --- Keys ---------------------------------------------------------------------------------

_MODIFIER_ALIASES = {
    "ctrl": "ctrl", "control": "ctrl", "ctl": "ctrl", "alt": "alt", "option": "alt", "opt": "alt",
    "shift": "shift", "win": "win", "windows": "win", "meta": "win", "super": "win",
    # Not Windows keys. Kept apart so the model is told, instead of pressing something else.
    "cmd": "cmd", "command": "cmd", "⌘": "cmd",
}
_KEY_ALIASES = {
    "enter": "return", "esc": "escape", "bksp": "backspace", "del": "delete", "ins": "insert",
    "pgup": "pageup", "pgdn": "pagedown", "pgdown": "pagedown", "page_up": "pageup",
    "page_down": "pagedown", "arrowup": "up", "arrowdown": "down", "arrowleft": "left",
    "arrowright": "right", "spacebar": "space", "prtsc": "printscreen", "prtscn": "printscreen",
    "print_screen": "printscreen",
}
_MODIFIER_ORDER = {"cmd": 0, "win": 1, "ctrl": 2, "alt": 3, "shift": 4}
# Keys the Windows app can press, besides single letters, digits and punctuation.
NAMED_KEYS = frozenset({
    "return", "tab", "escape", "space", "backspace", "delete", "insert", "home", "end", "pageup",
    "pagedown", "up", "down", "left", "right", "printscreen", *(f"f{n}" for n in range(1, 13)),
})


def _parse_combo(combo: Any) -> tuple[list[str], list[str]]:
    """(modifiers in a fixed order, keys). A valid combo has exactly one key."""

    raw = str(combo or "").strip().lower()
    if raw in {"+", "-"}:
        return [], [raw]
    parts = [p for p in re.split(r"[+\s]+", raw) if p]
    if raw.endswith("++"):
        parts.append("+")  # "ctrl++": the key is "+"
    # "ctrl-s" uses "-" as a separator; a lone "-" is the key.
    parts = [q for p in parts for q in (p.split("-") if len(p) > 1 and "-" in p else [p]) if q]
    mods = sorted({_MODIFIER_ALIASES[p] for p in parts if p in _MODIFIER_ALIASES}, key=_MODIFIER_ORDER.__getitem__)
    keys = [_KEY_ALIASES.get(p, p) for p in parts if p not in _MODIFIER_ALIASES]
    return mods, keys


def normalize_combo(combo: Any) -> str:
    """``Ctrl + Shift + S`` → ``ctrl+shift+s``. Backspace and Delete stay different keys."""

    mods, keys = _parse_combo(combo)
    return "+".join(mods + keys)


def blocked_key_reason(combo: Any) -> str | None:
    """Why a combo is never pressed, or None."""

    mods, keys = _parse_combo(combo)
    if "cmd" in mods:
        return "There is no cmd key on Windows. Use ctrl, e.g. ctrl+s."
    if "win" in mods:
        return "Keys with the Windows key act outside the allowed apps. Use open_app or focus_window."
    if len(keys) != 1:
        return "Give one key with its modifiers, e.g. enter, f2 or ctrl+s."
    normalized = "+".join(mods + keys)
    if normalized in BLOCKED_KEYS:
        return f"{normalized} acts outside the app you are working in. Use open_app or focus_window."
    if len(keys[0]) > 1 and keys[0] not in NAMED_KEYS:
        return (f"Unknown key “{keys[0]}”. Use a letter, a digit, f1 to f12, enter, tab, escape, space, "
                "backspace, delete, insert, home, end, pageup, pagedown or an arrow.")
    return None


# --- The driver interface used by device_tasks.advance ------------------------------------


def system_prompt(person: str, allowed: list[str]) -> str:
    return _SYSTEM.format(person=person, apps=", ".join(allowed) or "(none)")


def clean_allowed(values: Any) -> list[str]:
    return clean_apps(values)


def start_summary(allowed: list[str]) -> str:
    return f"Waiting for the Windows app ({', '.join(allowed)})"


def screen_header(observation: dict[str, Any], allowed: list[str]) -> str:
    running = [str(a)[:60] for a in (observation.get("running_apps") or [])[:30]]
    header = f"App: {str(observation.get('app') or '?')[:120]}"
    return header + (f"\nAllowed apps running: {', '.join(running)}" if running else "")


def validate_action(
    name: str, args: dict[str, Any], elements: dict[str, dict[str, Any]], allowed: list[str],
    observation: dict[str, Any],
) -> str | None:
    """An error for the model when the action can't be sent to the PC as it is."""

    if name not in ACTION_TOOLS:
        return f"Unknown tool {name}."
    if name == "open_app":
        app = normalize_app(args.get("app"))
        if not app or is_denied_app(app) or app not in {normalize_app(a) for a in allowed}:
            return f"{args.get('app')} is not one of the allowed apps ({', '.join(allowed)})."
        return None
    if name == "key":
        if not normalize_combo(args.get("combo")):
            return "Give a key, e.g. enter or ctrl+s."
        return blocked_key_reason(args.get("combo"))
    # Refs, password fields, menu paths and text length are the same on every desktop.
    return _dt.validate_action(name, args, elements, allowed)


def irreversible_reason(
    name: str, args: dict[str, Any], elements: dict[str, dict[str, Any]], observation: dict[str, Any],
) -> str | None:
    """Why this action needs approval, or None. Either check is enough."""

    if name != "key":
        return _dt.irreversible_reason(name, args, elements)
    if args.get("irreversible") is True:
        return "MindLoom marked it as not undoable"
    combo = normalize_combo(args.get("combo"))
    if combo in IRREVERSIBLE_KEYS:
        return f"{combo} usually sends or deletes"
    if combo in {"return", "space"}:
        focused = next((e for e in elements.values() if e.get("focused")), None) or {}
        label = str(focused.get("label") or "")
        if str(focused.get("role") or "").lower() in {"button", "hyperlink", "menuitem", "splitbutton"} \
                and looks_irreversible(label):
            return f"{combo} on “{label}” looks like it can't be undone"
    return None


def describe(
    name: str, args: dict[str, Any], elements: dict[str, dict[str, Any]], observation: dict[str, Any],
) -> str:
    """One line for the run log and the approval card."""

    if name == "key":
        return f"Press {normalize_combo(args.get('combo'))}"
    return _dt.describe(name, args, elements)
