"""This PC's own check of an action before it touches an app.

The API already checked it; this repeats the checks against what the app shows right now,
so a missed flag, a changed screen or a bad server reply can't send, delete or type a
password. It is the Windows counterpart of ``ActionGate.swift`` in the Mac app and
``task-policy.js`` in the Chrome extension.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import keys, policy
from .outline import OutlineEntry

TOOLS = frozenset({
    "open_app", "focus_window", "press", "set_value", "type_text", "key", "select_menu", "scroll", "wait",
})

# Why an action was refused, as the API's ``blocked`` field.
NEEDS_APPROVAL = "needs_approval"
SECURE_FIELD = "secure_field"
APP_NOT_ALLOWED = "app_not_allowed"
DECLINED_FOREGROUND = "declined_foreground"


@dataclass(frozen=True)
class Action:
    """One action the API asks for. ``approved`` is true only when the person approved it
    (or auto-approve is on for their own skill); ``expect_label`` is the label the target
    had on the screen the action was chosen from."""

    id: str
    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    description: str = ""
    approved: bool = False
    expect_label: str | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "Action":
        args = data.get("args")
        return cls(
            id=str(data.get("id") or ""), tool=str(data.get("tool") or ""),
            args=args if isinstance(args, dict) else {}, description=str(data.get("description") or ""),
            # Only a real ``true`` counts: never "true", 1 or anything else truthy.
            approved=data.get("approved") is True,
            expect_label=data.get("expect_label") if isinstance(data.get("expect_label"), str) else None,
        )

    def string(self, key: str) -> str:
        value = self.args.get(key)
        return "" if value is None or isinstance(value, (dict, list)) else str(value)

    def flag(self, key: str) -> bool:
        return self.args.get(key) is True

    @property
    def ref(self) -> str | None:
        return self.string("ref") or None

    @property
    def path(self) -> list[str]:
        value = self.args.get("path")
        return [str(p) for p in value if str(p).strip()] if isinstance(value, list) else []


@dataclass(frozen=True)
class Context:
    """What the app shows right now for this action's target."""

    target: OutlineEntry | None = None
    # The label of the window's default button (what Enter presses).
    default_button_label: str | None = None
    # The label of the focused element when it is a button (what Enter and Space press).
    focused_button_label: str | None = None
    # The focused element is a password field.
    focused_is_secure: bool = False
    app_name: str = ""
    app_allowed: bool = True


@dataclass(frozen=True)
class Verdict:
    proceed: bool
    # Bring the app to the front for this action (the person gets a notice first).
    foreground: bool = False
    blocked: str | None = None
    detail: str = ""


def _refuse(detail: str, blocked: str | None = None) -> Verdict:
    return Verdict(proceed=False, blocked=blocked, detail=detail)


def _normalized(text: str | None) -> str:
    return " ".join(str(text or "").lower().split())


def check(action: Action, context: Context) -> Verdict:
    tool = action.tool
    if tool not in TOOLS:
        return _refuse(f"MindLoom doesn't know how to “{tool}”.")
    if tool not in {"open_app", "wait"} and not context.app_allowed:
        return _refuse(f"{context.app_name or 'This app'} isn't one of the apps allowed for this automation.",
                       APP_NOT_ALLOWED)

    # The target must still be what the action was chosen against.
    if tool in {"press", "set_value", "scroll"} or (tool == "type_text" and action.ref):
        target = context.target
        if target is None:
            return _refuse(f"The element {action.ref or '?'} is no longer on screen. Look again.")
        if action.expect_label is not None and _normalized(action.expect_label) != _normalized(target.label):
            return _refuse(f"The screen changed: {action.ref} is now “{target.label}”, "
                           f"not “{action.expect_label}”. Look again.")

    # Passwords are the person's.
    if tool in {"set_value", "type_text"}:
        if (context.target is not None and context.target.secure) or (not action.ref and context.focused_is_secure):
            return _refuse("That is a password field. MindLoom never fills those: ask the person.", SECURE_FIELD)
    if tool == "key" and context.focused_is_secure:
        return _refuse("A password field has the keyboard. MindLoom won't press keys now: ask the person.",
                       SECURE_FIELD)

    combo = None
    if tool == "key":
        combo = keys.parse(action.string("combo"))
        reason = keys.blocked_reason(combo)
        if reason:
            return _refuse(reason)
    if tool == "select_menu" and not action.path:
        return _refuse('Give the menu path, e.g. ["File", "Save"].')

    # Anything that can't be undone needs the person's approval, whatever the API said.
    if not action.approved:
        reason = irreversible_reason(action, context, combo)
        if reason:
            return _refuse(reason, NEEDS_APPROVAL)

    # Typed keys only reach the window in front.
    return Verdict(proceed=True, foreground=tool in {"type_text", "key", "select_menu"})


def irreversible_reason(action: Action, context: Context, combo: keys.KeyCombo | None = None) -> str | None:
    tool = action.tool
    if tool == "press":
        if action.flag("irreversible"):
            return "Marked as not undoable."
        label = context.target.label if context.target else ""
        return f"“{label}” looks like it can't be undone." if policy.looks_irreversible(label) else None
    if tool == "select_menu":
        if action.flag("irreversible"):
            return "Marked as not undoable."
        item = action.path[-1] if action.path else ""
        return f"“{item}” looks like it can't be undone." if policy.looks_irreversible(item) else None
    if tool == "key":
        if action.flag("irreversible"):
            return "Marked as not undoable."
        combo = combo or keys.parse(action.string("combo"))
        if keys.is_irreversible(combo):
            return f"{combo.normalized} usually sends or deletes."
        return _enter_reason(combo, context)
    if tool == "type_text" and any(c in action.string("text") for c in "\r\n"):
        # A line break is typed as Enter.
        return _enter_reason(keys.parse("return"), context)
    return None


def _enter_reason(combo: keys.KeyCombo, context: Context) -> str | None:
    name = "Enter" if combo.key == "return" else "Space"
    if keys.presses_focused_button(combo) and policy.looks_irreversible(context.focused_button_label):
        return f"{name} presses “{context.focused_button_label}”."
    if keys.presses_default_button(combo) and policy.looks_irreversible(context.default_button_label):
        return f"Enter presses “{context.default_button_label}”."
    return None


def check_focus_now(action: Action, *, focused_button_label: str | None, focused_is_secure: bool) -> Verdict:
    """Once more with the app in front, just before keys are sent: bringing a window forward
    can move the focus, and keys go wherever the focus is."""

    if action.tool not in {"key", "type_text"}:
        return Verdict(proceed=True)
    if focused_is_secure:
        return _refuse("A password field has the keyboard. MindLoom won't type now: ask the person.", SECURE_FIELD)
    if not action.approved:
        combo = keys.parse(action.string("combo")) if action.tool == "key" else keys.parse("return")
        presses_enter = action.tool == "key" or any(c in action.string("text") for c in "\r\n")
        if presses_enter:
            reason = _enter_reason(combo, Context(focused_button_label=focused_button_label))
            if reason:
                return _refuse(reason, NEEDS_APPROVAL)
    return Verdict(proceed=True)
