"""Browser tasks: MindLoom operates web pages in the person's Chrome for an automation run.

``hand_off_to_browser`` in an automation run creates a device task with ``surface``
"browser". The MindLoom Chrome extension claims it and drives it through the same step
loop as the Mac app (:mod:`shuttle.device_tasks`): it sends the page as a numbered
outline (``[e12] button "Save"``), the model picks one action, this module checks it,
and the extension performs it and sends the next page.

What is specific to the browser lives here:

- only sites the person allowed for this automation are opened or acted on, over
  https, and never sites that hold sign-ins, passwords or MindLoom itself
  (:data:`DENIED_SITES`), so a run can never approve its own actions;
- a control whose label sends, submits, posts, deletes, pays or confirms waits for
  approval, and so does accepting a "are you sure?" dialog. The extension checks the
  label again before it clicks, and checks what Enter would submit;
- password, one-time-code and card fields are never read or typed into;
- page content is data, never instructions.
"""

from __future__ import annotations

import ipaddress
import re
from typing import Any
from urllib.parse import urlsplit

from shuttle.device_tasks import _IRREVERSIBLE, _REF, _UNTRUSTED, _tool, looks_irreversible, normalize_combo

SURFACE = "browser"
WHERE = "Chrome"
MAX_STEPS = 150
MAX_SITES = 20
MAX_TABS = 12

# Sites MindLoom never operates, whatever an automation allows: sign-in and account
# security pages, password managers, and the extension store. A host matches when it is
# one of these or a subdomain of one. The extension has the same list (task-client.js).
DENIED_SITES = frozenset({
    "accounts.google.com", "myaccount.google.com", "passwords.google.com",
    "chromewebstore.google.com", "login.microsoftonline.com", "login.live.com",
    "account.microsoft.com", "appleid.apple.com", "1password.com", "bitwarden.com",
    "lastpass.com", "dashlane.com", "keepersecurity.com", "nordpass.com", "okta.com",
    "auth0.com", "duosecurity.com",
})

ACTION_TOOLS = frozenset({
    "open_url", "switch_tab", "press", "set_value", "select_option", "type_text", "key",
    "hover", "scroll", "wait", "read", "answer_dialog", "go_back",
})
REF_TOOLS = frozenset({"press", "set_value", "select_option", "hover", "read"})
# While a page dialog (alert / confirm) is open nothing else on the page responds.
DIALOG_TOOLS = frozenset({"answer_dialog"})

_SYSTEM = f"""\
You operate web pages in {{person}}'s Chrome for part of a MindLoom automation, through
the MindLoom extension. Each turn you get the page you're working in as a numbered
outline, e.g. [e12] button "Save", with the visible text in between, and the result of
your last action. Call exactly one tool per turn.

Allowed sites: {{sites}}. Never open or act on any other site.

How to act:
- Use the element refs from the latest page only; refs change after every action.
- Start with open_url if no page is open or the page is the wrong one. Other open tabs
  on allowed sites are listed as [t1], [t2]: switch_tab to use one, e.g. a tab a link
  opened.
- Text fields: set_value replaces what is there. For a search box or a dropdown that
  suggests as you type, set_value, then read the next page and press the suggestion.
- Dropdowns: select_option for a real select (its choices are listed). For any other
  dropdown, press it, then press the choice on the next page.
- Checkboxes, radios, tabs, rows, links and buttons: press. Menus that open on hover:
  hover, then press the item.
- key for Enter, Tab, Escape and arrow keys. Tab moves to the next cell in grids that
  only take the keyboard.
- The outline shows what is on screen first. When it says more is above or below, or a
  table shows only some rows, scroll (pass the list's or table's ref to scroll inside
  it), and use read to get the full text of a table or section.
- Pages load and save in the background: if the result does not show yet, wait a few
  seconds and look again before you act twice. Never repeat a press that saves or
  submits without checking whether the first one worked.
- After every action, check the next page: a message, a new record number, a changed
  value. If an action failed twice, try another way or call ask_person.
- For a task over many records, finish one record completely before the next, and keep
  count in your done summary.
- Set irreversible=true on any action that sends, submits, posts, deletes, pays, buys,
  books, confirms, approves or can't be undone. The person approves it first.
- Fields marked (secure) are passwords, codes or card numbers: never fill them.
  Sign-in pages, two-factor codes and CAPTCHAs are for the person: call ask_person with
  one clear instruction and wait.
- File uploads and downloads are for the person: call ask_person.
- Use the exact values you were given. Never guess names, amounts, dates, codes or
  addresses; call ask_person instead.
- When the steps are done, call done with a short summary that includes every value
  you were asked to read or that the page gave back (record numbers, totals, statuses).
  If they cannot be done, call done with outcome "not_done" and say why.
- {_UNTRUSTED}
"""

_TAB = {"type": "string", "description": "Tab ref from the latest page, e.g. t2."}

TOOLS: list[dict[str, Any]] = [
    _tool("open_url", "Open a page on an allowed site in the working tab.",
          {"url": {"type": "string", "description": "Full https address on an allowed site."}}, ["url"]),
    _tool("switch_tab", "Work in another open tab on an allowed site.", {"tab": _TAB}, ["tab"]),
    _tool("go_back", "Go back one page in the working tab.", {}, []),
    _tool("press", "Click a button, link, checkbox, radio, tab, row, menu item or other control.",
          {"ref": _REF, "irreversible": _IRREVERSIBLE}, ["ref"]),
    _tool("set_value", "Replace the text in a text field, text area, search box or editor.",
          {"ref": _REF, "text": {"type": "string"}}, ["ref", "text"]),
    _tool("select_option", "Choose one of the listed choices of a select.",
          {"ref": _REF, "option": {"type": "string", "description": "The choice's text."}}, ["ref", "option"]),
    _tool("type_text", "Type text at the focused element without clearing it first.",
          {"text": {"type": "string"}}, ["text"]),
    _tool("key", "Press a key: enter, tab, shift+tab, escape, backspace, delete, space, up, down, "
                 "left, right, home, end, pageup, pagedown.",
          {"combo": {"type": "string"}, "irreversible": _IRREVERSIBLE}, ["combo"]),
    _tool("hover", "Move the pointer over an element, to open a menu that appears on hover.",
          {"ref": _REF}, ["ref"]),
    _tool("scroll", "Scroll the page, or a list or table when its ref is given.",
          {"direction": {"type": "string", "enum": ["up", "down", "top", "bottom"]}, "ref": _REF},
          ["direction"]),
    _tool("wait", "Wait for the page to finish loading or saving (up to 15 seconds).",
          {"seconds": {"type": "number"}}, ["seconds"]),
    _tool("read", "Get the full text of an element (a table, a list, a section), beyond the outline.",
          {"ref": _REF}, ["ref"]),
    _tool("answer_dialog", "Answer the page's dialog (alert or confirm): accept is OK, otherwise Cancel.",
          {"accept": {"type": "boolean"}}, ["accept"]),
    _tool("ask_person", "Pause and ask the person to do one thing in Chrome, then continue.",
          {"instruction": {"type": "string", "description": "One clear instruction."}}, ["instruction"]),
    _tool("done", "Finish this part with a summary of what was done and the values read.",
          {"summary": {"type": "string"},
           "outcome": {"type": "string", "enum": ["done", "partly_done", "not_done"]}}, ["summary"]),
]

_KEYS = frozenset({
    "return", "tab", "escape", "esc", "delete", "forwarddelete", "space", "up", "down", "left",
    "right", "home", "end", "pageup", "pagedown",
})
_KEY_MODIFIERS = frozenset({"shift"})


# --- Sites --------------------------------------------------------------------------------


def normalize_site(value: Any) -> str | None:
    """A host the person can allow: ``erp.example.com`` or ``*.example.com``.

    Takes a host or a pasted address. Returns None for anything that is not a public
    host name (or localhost, for development)."""

    raw = str(value or "").strip().lower()
    if not raw:
        return None
    wildcard = raw.startswith("*.")
    if wildcard:
        raw = raw[2:]
    if "://" in raw:
        split = urlsplit(raw)
        if split.scheme not in {"http", "https"}:
            return None
        raw = split.hostname or ""
    else:
        raw = raw.split("/", 1)[0].split("?", 1)[0].rsplit("@", 1)[-1].split(":", 1)[0]
    host = raw.strip(".")
    if not host or len(host) > 253 or not re.fullmatch(r"[a-z0-9.-]+", host):
        return None
    if host == "localhost":
        return None if wildcard else host
    try:
        ipaddress.ip_address(host)
        return None  # a site is named, not numbered
    except ValueError:
        pass
    labels = host.split(".")
    if len(labels) < 2 or any(not part or part.startswith("-") or part.endswith("-") for part in labels):
        return None
    return f"*.{host}" if wildcard else host


def _own_origins() -> set[tuple[str, int]]:
    """MindLoom's own web app: a run must never click its own Approve button."""

    try:
        from core.config import get_settings

        settings = get_settings()
        origins = [settings.frontend_url, *settings.resolved_cors_origins()]
    except Exception:  # noqa: BLE001 - settings are not needed to refuse the fixed list
        origins = []
    out: set[tuple[str, int]] = set()
    for origin in origins:
        try:
            split = urlsplit(str(origin or ""))
            if split.hostname:
                out.add((split.hostname.lower(), split.port or (80 if split.scheme == "http" else 443)))
        except ValueError:
            continue
    return out


def own_origins() -> list[str]:
    """MindLoom's own origins as the extension compares them (``https://app.example.com``)."""

    out = []
    for host, port in sorted(_own_origins()):
        scheme = "http" if host == "localhost" or port == 80 else "https"
        default = 80 if scheme == "http" else 443
        out.append(f"{scheme}://{host}" + ("" if port == default else f":{port}"))
    return out


def is_denied_site(host: str) -> bool:
    host = str(host or "").strip().lower().removeprefix("*.").strip(".")
    if not host:
        return True
    if any(host == denied or host.endswith("." + denied) for denied in DENIED_SITES):
        return True
    # On localhost only the port tells MindLoom from a test site; url_allowed checks it.
    return host != "localhost" and host in {own for own, _ in _own_origins()}


def clean_sites(values: Any) -> list[str]:
    """The sites a person allowed: normalized, de-duplicated, never a denied site."""

    out: list[str] = []
    for raw in values if isinstance(values, list) else []:
        site = normalize_site(raw)
        if site and not is_denied_site(site) and site not in out:
            out.append(site)
    return out[:MAX_SITES]


def host_allowed(host: str, allowed: list[str]) -> bool:
    host = str(host or "").strip().lower().strip(".")
    if not host or is_denied_site(host):
        return False
    for site in allowed:
        if site.startswith("*."):
            if host == site[2:] or host.endswith(site[1:]):
                return True
        elif host == site:
            return True
    return False


def url_allowed(url: Any, allowed: list[str]) -> bool:
    """An https page (http only on localhost) on an allowed site."""

    try:
        split = urlsplit(str(url or "").strip())
    except ValueError:
        return False
    host = (split.hostname or "").lower()
    if split.scheme != "https" and not (split.scheme == "http" and host == "localhost"):
        return False
    if split.username or split.password:
        return False
    try:
        port = split.port or (80 if split.scheme == "http" else 443)
    except ValueError:
        return False
    if (host, port) in _own_origins():
        return False
    return host_allowed(host, allowed)


def sites_in_skill(skill: Any) -> list[str]:
    """Sites the recorded steps were captured on: what to offer on the plan screen."""

    hosts: list[str] = []
    for step in getattr(skill, "guide_steps", None) or []:
        anchor = getattr(step, "browser_anchor", None)
        site = normalize_site(getattr(anchor, "url", "")) if anchor else None
        if site and not is_denied_site(site) and site not in hosts:
            hosts.append(site)
    return hosts[:MAX_SITES]


# --- The driver interface used by device_tasks.advance ------------------------------------


def system_prompt(person: str, allowed: list[str]) -> str:
    return _SYSTEM.format(person=person, sites=", ".join(allowed) or "(none)")


def clean_allowed(values: Any) -> list[str]:
    return clean_sites(values)


def start_summary(allowed: list[str]) -> str:
    return f"Waiting for Chrome ({', '.join(allowed)})"


def _dialog(observation: dict[str, Any]) -> dict[str, str] | None:
    dialog = observation.get("dialog")
    if not isinstance(dialog, dict) or not dialog.get("type"):
        return None
    return {"type": str(dialog.get("type"))[:20], "message": str(dialog.get("message") or "")[:500]}


def _tabs(observation: dict[str, Any]) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for item in (observation.get("tabs") or [])[:MAX_TABS]:
        if isinstance(item, dict) and item.get("ref"):
            out[str(item["ref"])[:8]] = {
                "title": str(item.get("title") or "")[:120], "url": str(item.get("url") or "")[:300],
            }
    return out


def screen_header(observation: dict[str, Any], allowed: list[str]) -> str:
    url = str(observation.get("url") or "")[:500]
    lines = [f"Page: {str(observation.get('app') or '(no page open)')[:160]}"]
    if url:
        lines.append(f"Address: {url}")
        if not url_allowed(url, allowed):
            lines.append("This page is not on an allowed site: nothing on it can be used. "
                         "open_url an allowed page, or ask_person if they must sign in here.")
    tabs = _tabs(observation)
    if tabs:
        lines.append("Open tabs on allowed sites: " + "; ".join(
            f"[{ref}] {tab['title'] or tab['url']}" for ref, tab in tabs.items()))
    dialog = _dialog(observation)
    if dialog:
        lines.append(f"A {dialog['type']} dialog is open and blocks the page: “{dialog['message']}”. "
                     "Answer it with answer_dialog.")
    return "\n".join(lines)


def validate_action(
    name: str, args: dict[str, Any], elements: dict[str, dict[str, Any]], allowed: list[str],
    observation: dict[str, Any],
) -> str | None:
    """An error for the model when the action can't be sent to Chrome as it is."""

    if name not in ACTION_TOOLS:
        return f"Unknown tool {name}."
    dialog = _dialog(observation)
    if dialog and name not in DIALOG_TOOLS:
        return "A dialog is open. Answer it with answer_dialog first."
    if name == "answer_dialog":
        return None if dialog else "No dialog is open."
    if name == "open_url":
        url = str(args.get("url") or "").strip()
        if not url_allowed(url, allowed):
            return (f"{url[:200] or '(no address)'} is not an https page on an allowed site "
                    f"({', '.join(allowed)}).")
        return None
    if name == "switch_tab":
        tabs = _tabs(observation)
        ref = str(args.get("tab") or "")
        if ref not in tabs:
            return f"No tab {ref or '(none)'}. Open tabs: {', '.join(tabs) or 'none'}."
        return None
    if name in {"wait", "go_back"}:
        return None
    current = str(observation.get("url") or "")
    if not url_allowed(current, allowed):
        return "The page is not on an allowed site. open_url an allowed page, or call ask_person."
    ref = str(args.get("ref") or "")
    if name in REF_TOOLS or (name == "scroll" and ref):
        if ref not in elements:
            return f"No element {ref or '(none)'} on the latest page. Use a ref from it."
        if name in {"set_value", "select_option", "read"} and elements[ref].get("secure"):
            return "That is a password, code or card field. Never fill or read it: call ask_person."
    if name == "type_text":
        focused = next((e for e in elements.values() if e.get("focused")), None)
        if focused and focused.get("secure"):
            return "The focused field is a password, code or card field. Never type into it: call ask_person."
    if name in {"set_value", "type_text"} and len(str(args.get("text") or "")) > 20_000:
        return "Text too long (20,000 characters at most)."
    if name == "select_option" and not str(args.get("option") or "").strip():
        return "Give the choice's text."
    if name == "scroll" and args.get("direction") not in {"up", "down", "top", "bottom"}:
        return "direction must be up, down, top or bottom."
    if name == "key":
        combo = normalize_combo(str(args.get("combo") or ""))
        parts = combo.split("+") if combo else []
        if not parts or parts[-1] not in _KEYS or any(p not in _KEY_MODIFIERS for p in parts[:-1]):
            return ("Use one of: enter, tab, shift+tab, escape, backspace, delete, space, up, down, "
                    "left, right, home, end, pageup, pagedown.")
    return None


def irreversible_reason(
    name: str, args: dict[str, Any], elements: dict[str, dict[str, Any]], observation: dict[str, Any],
) -> str | None:
    """Why this action needs approval, or None. Either check is enough."""

    if name == "answer_dialog":
        dialog = _dialog(observation) or {"type": "", "message": ""}
        if args.get("accept") is True and dialog["type"] != "alert":
            return f"The page asks “{dialog['message'][:200]}” and OK may not be undoable"
        return None
    if name not in {"press", "key"}:
        return None
    if args.get("irreversible") is True:
        return "MindLoom marked it as not undoable"
    if name == "press":
        label = str((elements.get(str(args.get("ref") or "")) or {}).get("label") or "")
        return f"“{label}” looks like it can't be undone" if looks_irreversible(label) else None
    combo = normalize_combo(str(args.get("combo") or ""))
    if combo in {"return", "space"}:
        focused = next((e for e in elements.values() if e.get("focused")), None) or {}
        label = str(focused.get("label") or "")
        if focused.get("role") in {"button", "link", "menuitem"} and looks_irreversible(label):
            return f"{combo} on “{label}” looks like it can't be undone"
    return None


def describe(
    name: str, args: dict[str, Any], elements: dict[str, dict[str, Any]], observation: dict[str, Any],
) -> str:
    """One line for the run log and the approval card."""

    def target() -> str:
        element = elements.get(str(args.get("ref") or "")) or {}
        label = element.get("label") or element.get("role") or args.get("ref") or ""
        return f"“{label}”" if label else "the page"

    if name == "open_url":
        return f"Open {str(args.get('url') or '')[:200]}"
    if name == "switch_tab":
        tab = _tabs(observation).get(str(args.get("tab") or "")) or {}
        return f"Switch to the tab “{tab.get('title') or tab.get('url') or args.get('tab')}”"
    if name == "go_back":
        return "Go back one page"
    if name == "press":
        return f"Click {target()}"
    if name == "set_value":
        return f"Enter “{str(args.get('text') or '')[:120]}” in {target()}"
    if name == "select_option":
        return f"Choose “{str(args.get('option') or '')[:120]}” in {target()}"
    if name == "type_text":
        return f"Type “{str(args.get('text') or '')[:120]}”"
    if name == "key":
        return f"Press {normalize_combo(str(args.get('combo') or ''))}"
    if name == "hover":
        return f"Point at {target()}"
    if name == "scroll":
        return f"Scroll {args.get('direction')}" + (f" in {target()}" if args.get("ref") else "")
    if name == "wait":
        return f"Wait {args.get('seconds')} s"
    if name == "read":
        return f"Read {target()}"
    if name == "answer_dialog":
        dialog = _dialog(observation) or {"message": ""}
        return f"Answer “{dialog['message'][:160]}” with {'OK' if args.get('accept') else 'Cancel'}"
    return name
