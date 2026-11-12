"""What MindLoom may do on this PC, whatever the API asks.

The lists are the server's (``apps/api/shuttle/windows_tasks.py`` and the irreversible
words of ``device_tasks.py``). A backend test (``tests/test_windows_tasks.py``) reads this
file and fails when they drift, so keep each list a plain tuple of double-quoted strings.
"""

from __future__ import annotations

import re
from typing import Iterable

# Apps MindLoom never operates: they hold passwords or keys, change system or security
# settings, or run commands. Lowercase, without ".exe"; both the name people use and the
# process name.
DENIED_APPS = (
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
    # The shell and its hosts
    "explorer", "file explorer", "windows explorer", "applicationframehost", "rundll32",
    "searchhost", "searchapp", "startmenuexperiencehost", "shellexperiencehost",
    # MindLoom itself
    "mindloom", "mindloom for windows", "mindloomagent",
)

# Control labels that send, delete, pay or can't be undone.
IRREVERSIBLE_WORDS = (
    "send", "submit", "post", "publish", "share", "delete", "remove", "erase", "trash",
    "discard", "don't save", "dont save", "replace", "overwrite", "pay", "purchase", "buy",
    "order", "checkout", "check out", "transfer", "book", "reserve", "confirm", "approve",
    "accept", "decline", "sign", "unsubscribe", "cancel subscription", "empty",
)

# Key combos that usually send or delete: they need the person's approval.
IRREVERSIBLE_KEYS = (
    "shift+delete", "ctrl+shift+delete", "ctrl+d", "alt+d", "alt+s", "ctrl+return",
    "ctrl+shift+return",
)

# Key combos that act outside the app MindLoom is working in: never pressed. Any combo
# with the Windows key is refused too.
BLOCKED_KEYS = (
    "alt+tab", "alt+shift+tab", "alt+escape", "alt+shift+escape", "ctrl+escape",
    "ctrl+shift+escape", "ctrl+alt+delete", "ctrl+alt+tab", "alt+space", "printscreen",
)

# Keys that can be pressed, besides single letters, digits and punctuation.
NAMED_KEYS = (
    "return", "tab", "escape", "space", "backspace", "delete", "insert", "home", "end", "pageup",
    "pagedown", "up", "down", "left", "right", "printscreen",
    "f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10", "f11", "f12",
)

_DENIED = frozenset(DENIED_APPS)

# A word on its own or with a common prefix/suffix ("Resend", "Deleting"), not inside
# another word ("Sendai", "Signature").
_IRREVERSIBLE_RE = re.compile(
    r"(?<![a-z])(?:re)?(" + "|".join(re.escape(w) for w in IRREVERSIBLE_WORDS)
    + r")(?:s|es|d|ed|ing|al)?(?![a-z])",
    re.IGNORECASE,
)


def normalize_app(name: object) -> str:
    """``EXCEL.EXE`` → ``excel``; ``Tally   Prime`` → ``tally prime``."""

    value = re.sub(r"\s+", " ", str(name or "").strip().lower())
    return re.sub(r"\.(exe|lnk)$", "", value).strip()


def is_denied(names: Iterable[object]) -> bool:
    """Any of an app's names (process name, description, product) is on the deny list."""

    return any(normalize_app(n) in _DENIED for n in names)


def is_allowed(names: Iterable[object], allowed: Iterable[object]) -> bool:
    """The app is one the person allowed, by any of its names, and not a denied app."""

    candidates = [n for n in (normalize_app(n) for n in names) if n]
    if not candidates or any(n in _DENIED for n in candidates):
        return False
    wanted = {normalize_app(a) for a in allowed}
    return any(n in wanted for n in candidates)


def looks_irreversible(label: object) -> bool:
    return bool(_IRREVERSIBLE_RE.search(str(label or "")))
