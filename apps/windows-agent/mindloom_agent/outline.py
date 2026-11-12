"""Turns a UI Automation element tree into the compact, numbered text the model reads:

    [e1] Edit "Party A/c name" value="Azure Interior" (focused)
    [e2] Button "Save"

Unlabelled layout containers are left out (their children move up), password fields never
show a value, long values are cut, and the outline stops at a size budget.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Containers that only lay things out: shown only when they carry a label.
LAYOUT_ROLES = frozenset({
    "Pane", "Group", "Custom", "List", "Tree", "Table", "DataGrid", "Tab", "ToolBar", "Header",
    "Window", "StatusBar", "MenuBar", "Menu", "Image", "Separator", "Calendar",
})
# Controls that can be acted on: always listed, labelled or not.
ACTIONABLE_ROLES = frozenset({
    "Button", "CheckBox", "RadioButton", "ComboBox", "Edit", "Hyperlink", "ListItem", "TreeItem",
    "TabItem", "MenuItem", "Slider", "Spinner", "SplitButton", "DataItem", "Document",
})
VALUE_ROLES = frozenset({
    "Edit", "ComboBox", "Document", "Slider", "Spinner", "CheckBox", "RadioButton", "ProgressBar",
    "DataItem", "ListItem", "Custom",
})
# Never walked: chrome that only adds noise.
SKIPPED_ROLES = frozenset({"ScrollBar", "Thumb", "TitleBar", "ToolTip"})

MAX_VALUE_LENGTH = 200
MAX_TEXT_LENGTH = 300


@dataclass
class OutlineNode:
    """One UI Automation element as the driver read it. ``handle`` indexes the driver's own
    element table, so a ref in the outline maps back to the live element."""

    handle: int
    role: str
    label: str = ""
    value: str = ""
    enabled: bool = True
    focused: bool = False
    selected: bool = False
    secure: bool = False
    expanded: bool | None = None
    is_default: bool = False
    children: list["OutlineNode"] = field(default_factory=list)


@dataclass(frozen=True)
class OutlineEntry:
    """One numbered line of the outline."""

    ref: str
    handle: int
    role: str
    label: str
    secure: bool = False
    focused: bool = False
    is_default: bool = False

    def summary(self) -> dict:
        return {"ref": self.ref, "role": self.role, "label": self.label,
                "secure": self.secure, "focused": self.focused}


@dataclass
class RenderedOutline:
    text: str
    entries: list[OutlineEntry]

    def entry(self, ref: str | None) -> OutlineEntry | None:
        return next((e for e in self.entries if e.ref == ref), None) if ref else None


def _clip(text: str, limit: int) -> str:
    flat = " ".join(str(text).replace("\r", "").replace("\n", " ⏎ ").split())
    return flat[:limit] + "…" if len(flat) > limit else flat


def _quote(text: str) -> str:
    return '"' + text.replace('"', "'") + '"'


def label_of(node: OutlineNode) -> str:
    # One short of the limit: the API takes labels of at most 300 characters, "…" included.
    return _clip(node.label, MAX_TEXT_LENGTH - 1)


def should_show(node: OutlineNode) -> bool:
    if node.role in ACTIONABLE_ROLES or node.secure:
        return True
    has_label = bool(node.label.strip())
    if node.role in LAYOUT_ROLES:
        return has_label
    return has_label or bool(node.value.strip())


def describe(node: OutlineNode, ref: str, depth: int) -> str:
    line = "  " * min(depth, 6) + f"[{ref}] {node.role}"
    text = label_of(node)
    if text:
        line += " " + _quote(text)
    if node.secure:
        line += " (secure)"  # a password field never shows its value
    elif node.role in VALUE_ROLES and node.value and node.value != node.label:
        line += " value=" + _quote(_clip(node.value, MAX_VALUE_LENGTH))
    flags = []
    if node.focused:
        flags.append("focused")
    if node.selected:
        flags.append("selected")
    if node.expanded is True:
        flags.append("expanded")
    elif node.expanded is False:
        flags.append("collapsed")
    if not node.enabled:
        flags.append("disabled")
    if flags:
        line += f" ({', '.join(flags)})"
    return line


def render(
    roots: list[OutlineNode], header: list[str] | None = None, max_lines: int = 400, max_chars: int = 14_000,
) -> RenderedOutline:
    """``header`` lines (app, windows, menu bar) come first and take no refs."""

    lines = list(header or [])
    entries: list[OutlineEntry] = []
    chars = sum(len(line) + 1 for line in lines)
    omitted = 0

    def visit(node: OutlineNode, depth: int) -> None:
        nonlocal chars, omitted
        shown = should_show(node)
        if shown:
            ref = f"e{len(entries) + 1}"
            line = describe(node, ref, depth)
            if len(entries) >= max_lines or chars + len(line) + 1 > max_chars:
                omitted += 1
            else:
                entries.append(OutlineEntry(ref=ref, handle=node.handle, role=node.role, label=label_of(node),
                                            secure=node.secure, focused=node.focused, is_default=node.is_default))
                lines.append(line)
                chars += len(line) + 1
        for child in node.children:
            visit(child, depth + 1 if shown else depth)

    for root in roots:
        visit(root, 0)
    if omitted:
        lines.append(f"… {omitted} more elements not shown (scroll, or switch to the window you need)")
    if not entries:
        lines.append("(This window reports no usable controls through UI Automation. "
                     "Work with key and type_text, or ask the person.)")
    return RenderedOutline(text="\n".join(lines), entries=entries)
