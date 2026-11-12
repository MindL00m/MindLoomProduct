"""Key combos as the API writes them ("ctrl+shift+s", "Alt+F4", "enter") and as Windows
presses them (virtual-key codes). Parsing matches ``windows_tasks._parse_combo`` on the
server."""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import policy

_MODIFIER_ALIASES = {
    "ctrl": "ctrl", "control": "ctrl", "ctl": "ctrl", "alt": "alt", "option": "alt", "opt": "alt",
    "shift": "shift", "win": "win", "windows": "win", "meta": "win", "super": "win",
    # Not a Windows key: kept apart so it is refused instead of pressed as something else.
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

# Virtual-key codes (winuser.h VK_*).
MODIFIER_VK = {"ctrl": 0x11, "alt": 0x12, "shift": 0x10}
NAMED_VK = {
    "return": 0x0D, "tab": 0x09, "escape": 0x1B, "space": 0x20, "backspace": 0x08, "delete": 0x2E,
    "insert": 0x2D, "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22, "up": 0x26,
    "down": 0x28, "left": 0x25, "right": 0x27, "printscreen": 0x2C,
    **{f"f{n}": 0x6F + n for n in range(1, 13)},
}
# Keys on the extended part of the keyboard: SendInput needs KEYEVENTF_EXTENDEDKEY for them.
EXTENDED_KEYS = frozenset({"delete", "insert", "home", "end", "pageup", "pagedown", "up", "down", "left", "right"})
# Punctuation on a US layout, for when VkKeyScan has no answer.
PUNCTUATION_VK = {
    ";": 0xBA, "=": 0xBB, ",": 0xBC, "-": 0xBD, ".": 0xBE, "/": 0xBF, "`": 0xC0, "[": 0xDB,
    "\\": 0xDC, "]": 0xDD, "'": 0xDE, "+": 0xBB,
}


@dataclass(frozen=True)
class KeyCombo:
    modifiers: tuple[str, ...]
    keys: tuple[str, ...]

    @property
    def normalized(self) -> str:
        return "+".join(self.modifiers + self.keys)

    @property
    def key(self) -> str:
        return self.keys[0] if len(self.keys) == 1 else ""

    @property
    def virtual_key(self) -> int | None:
        """The key's virtual-key code; None for a key this app does not press."""

        key = self.key
        if key in NAMED_VK:
            return NAMED_VK[key]
        if len(key) == 1 and (key.isascii() and key.isalnum()):
            return ord(key.upper())
        return PUNCTUATION_VK.get(key)


def parse(combo: object) -> KeyCombo:
    raw = str(combo or "").strip().lower()
    if raw in {"+", "-"}:
        return KeyCombo((), (raw,))
    parts = [p for p in re.split(r"[+\s]+", raw) if p]
    if raw.endswith("++"):
        parts.append("+")  # "ctrl++": the key is "+"
    # "ctrl-s" uses "-" as a separator; a lone "-" is the key.
    parts = [q for p in parts for q in (p.split("-") if len(p) > 1 and "-" in p else [p]) if q]
    mods = sorted({_MODIFIER_ALIASES[p] for p in parts if p in _MODIFIER_ALIASES}, key=_MODIFIER_ORDER.__getitem__)
    keys = [_KEY_ALIASES.get(p, p) for p in parts if p not in _MODIFIER_ALIASES]
    return KeyCombo(tuple(mods), tuple(keys))


def blocked_reason(combo: KeyCombo) -> str | None:
    """Why this combo is never pressed, or None."""

    if "cmd" in combo.modifiers:
        return "There is no cmd key on Windows. Use ctrl, e.g. ctrl+s."
    if "win" in combo.modifiers:
        return "Keys with the Windows key act outside the allowed apps. Use open_app or focus_window."
    if len(combo.keys) != 1:
        return "Give one key with its modifiers, e.g. enter, f2 or ctrl+s."
    if combo.normalized in policy.BLOCKED_KEYS:
        return f"{combo.normalized} acts outside the app you are working in. Use open_app or focus_window."
    if (len(combo.key) > 1 and combo.key not in policy.NAMED_KEYS) or combo.virtual_key is None:
        return f"MindLoom can't press “{combo.key}”."
    return None


def is_irreversible(combo: KeyCombo) -> bool:
    return combo.normalized in policy.IRREVERSIBLE_KEYS


def presses_default_button(combo: KeyCombo) -> bool:
    """Enter presses a dialog's default button."""

    return not combo.modifiers and combo.key == "return"


def presses_focused_button(combo: KeyCombo) -> bool:
    """Enter and Space press the button that has the keyboard focus."""

    return not combo.modifiers and combo.key in {"return", "space"}
