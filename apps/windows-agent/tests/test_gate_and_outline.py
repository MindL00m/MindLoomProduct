import pytest

from mindloom_agent import gate
from mindloom_agent.gate import Action, Context
from mindloom_agent.outline import OutlineEntry, OutlineNode, render

SAVE = OutlineEntry(ref="e2", handle=2, role="Button", label="Save")
SEND = OutlineEntry(ref="e3", handle=3, role="Button", label="Send Report")
NAME = OutlineEntry(ref="e1", handle=1, role="Edit", label="Name")
PASSWORD = OutlineEntry(ref="e4", handle=4, role="Edit", label="Password", secure=True)


def act(tool, approved=False, label=None, **args):
    return Action(id="a1", tool=tool, args=args, approved=approved, expect_label=label)


def test_ordinary_actions_go_through():
    assert gate.check(act("press", ref="e2", label="Save"), Context(target=SAVE)) == gate.Verdict(True)
    assert gate.check(act("set_value", ref="e1", text="Ada"), Context(target=NAME)).proceed
    assert gate.check(act("scroll", ref="e1", direction="down"), Context(target=NAME)).proceed
    assert gate.check(act("focus_window", title="Voucher"), Context()).proceed
    assert gate.check(act("wait", seconds=2), Context(app_allowed=False)).proceed
    # Keys, typing and menus need the app in front.
    for action in (act("key", combo="ctrl+s"), act("type_text", text="hello"), act("select_menu", path=["File", "Save"])):
        verdict = gate.check(action, Context())
        assert verdict.proceed and verdict.foreground
    assert not gate.check(act("press", ref="e2"), Context(target=SAVE)).foreground


def test_unknown_tools_and_apps_that_are_not_allowed_are_refused():
    assert not gate.check(act("open_url", url="https://example.com"), Context()).proceed
    assert not gate.check(act("run_command", command="dir"), Context()).proceed
    verdict = gate.check(act("press", ref="e2"), Context(target=SAVE, app_name="Notepad", app_allowed=False))
    assert not verdict.proceed and verdict.blocked == gate.APP_NOT_ALLOWED and "Notepad" in verdict.detail
    assert gate.check(act("key", combo="ctrl+s"), Context(app_allowed=False)).blocked == gate.APP_NOT_ALLOWED


def test_the_target_must_still_be_what_was_chosen():
    gone = gate.check(act("press", ref="e9"), Context(target=None))
    assert not gone.proceed and gone.blocked is None and "no longer on screen" in gone.detail
    changed = gate.check(act("press", ref="e3", label="Add Item"), Context(target=SEND))
    assert not changed.proceed and changed.blocked is None and "screen changed" in changed.detail
    # Spacing and case don't count as a change.
    assert gate.check(act("press", ref="e2", label=" save "), Context(target=SAVE)).proceed


def test_irreversible_actions_need_approval_whatever_the_api_said():
    refused = gate.check(act("press", ref="e3", label="Send Report"), Context(target=SEND))
    assert not refused.proceed and refused.blocked == gate.NEEDS_APPROVAL and "Send Report" in refused.detail
    assert gate.check(act("press", ref="e3", label="Send Report", approved=True), Context(target=SEND)).proceed
    assert gate.check(act("press", ref="e2", irreversible=True), Context(target=SAVE)).blocked == gate.NEEDS_APPROVAL
    assert gate.check(act("select_menu", path=["List", "Delete Everything"]), Context()).blocked == gate.NEEDS_APPROVAL
    assert gate.check(act("select_menu", path=["List", "Clear List"]), Context()).proceed
    for combo in ("shift+delete", "Shift+Del", "ctrl+enter", "alt+s", "alt+d", "ctrl+d"):
        assert gate.check(act("key", combo=combo), Context()).blocked == gate.NEEDS_APPROVAL
        assert gate.check(act("key", combo=combo, approved=True), Context()).proceed
    assert gate.check(act("key", combo="ctrl+a", irreversible=True), Context()).blocked == gate.NEEDS_APPROVAL


def test_enter_and_space_are_checked_against_what_they_press():
    dialog = Context(default_button_label="Send Report")
    assert gate.check(act("key", combo="enter"), dialog).blocked == gate.NEEDS_APPROVAL
    assert gate.check(act("key", combo="enter", approved=True), dialog).proceed
    assert gate.check(act("key", combo="space"), dialog).proceed  # Space does not press the default button
    assert gate.check(act("key", combo="tab"), dialog).proceed
    assert gate.check(act("key", combo="enter"), Context(default_button_label="OK")).proceed
    on_delete = Context(focused_button_label="Delete")
    assert gate.check(act("key", combo="space"), on_delete).blocked == gate.NEEDS_APPROVAL
    assert gate.check(act("key", combo="enter"), on_delete).blocked == gate.NEEDS_APPROVAL
    # A line break in typed text is an Enter.
    assert gate.check(act("type_text", text="line one\nline two"), dialog).blocked == gate.NEEDS_APPROVAL
    assert gate.check(act("type_text", text="one line"), dialog).proceed


def test_passwords_are_never_filled_even_when_approved():
    for action in (act("set_value", ref="e4", text="hunter2", approved=True),
                   act("type_text", ref="e4", text="hunter2", approved=True)):
        verdict = gate.check(action, Context(target=PASSWORD))
        assert not verdict.proceed and verdict.blocked == gate.SECURE_FIELD
    focused = Context(focused_is_secure=True)
    assert gate.check(act("type_text", text="hunter2", approved=True), focused).blocked == gate.SECURE_FIELD
    assert gate.check(act("key", combo="enter", approved=True), focused).blocked == gate.SECURE_FIELD
    # Typing into a named, ordinary field is fine even if a password field has the focus now.
    assert gate.check(act("type_text", ref="e1", text="Ada"), Context(target=NAME, focused_is_secure=True)).proceed


def test_keys_that_leave_the_app_are_refused_even_when_approved():
    for combo in ("win+r", "alt+tab", "ctrl+shift+esc", "cmd+q", "ctrl+alt+del", "ctrl+banana", ""):
        verdict = gate.check(act("key", combo=combo, approved=True), Context())
        assert not verdict.proceed and verdict.blocked is None
    assert not gate.check(act("select_menu", path=[]), Context()).proceed


def test_only_a_real_true_approves():
    for value in ("true", 1, "yes", None, [True]):
        assert Action.from_json({"id": "a", "tool": "press", "approved": value}).approved is False
    assert Action.from_json({"id": "a", "tool": "press", "approved": True}).approved is True
    parsed = Action.from_json({"id": "a", "tool": "press", "args": "nonsense", "expect_label": 7})
    assert parsed.args == {} and parsed.expect_label is None
    assert Action.from_json({"id": "a", "tool": "press", "args": {"irreversible": "false"}}).flag("irreversible") is False


def test_the_focus_is_checked_again_once_the_app_is_in_front():
    look = lambda action, button=None, secure=False: gate.check_focus_now(  # noqa: E731
        action, focused_button_label=button, focused_is_secure=secure)
    assert look(act("key", combo="enter")).proceed
    assert look(act("key", combo="enter"), button="Delete Voucher").blocked == gate.NEEDS_APPROVAL
    assert look(act("key", combo="space"), button="Delete Voucher").blocked == gate.NEEDS_APPROVAL
    assert look(act("key", combo="enter", approved=True), button="Delete Voucher").proceed
    assert look(act("key", combo="tab"), button="Delete Voucher").proceed
    assert look(act("key", combo="enter"), button="OK").proceed
    assert look(act("type_text", text="abc"), button="Delete Voucher").proceed
    assert look(act("type_text", text="abc\n"), button="Delete Voucher").blocked == gate.NEEDS_APPROVAL
    assert look(act("key", combo="a", approved=True), secure=True).blocked == gate.SECURE_FIELD
    assert look(act("type_text", text="x", approved=True), secure=True).blocked == gate.SECURE_FIELD
    assert look(act("press", ref="e1"), secure=True).proceed  # not a key action


# --- Outline ----------------------------------------------------------------------------------


def tree():
    return [OutlineNode(1, "Pane", children=[
        OutlineNode(2, "Edit", label="Name", value="Ada", focused=True),
        OutlineNode(3, "Edit", label="Password", value="hunter2", secure=True),
        OutlineNode(4, "Pane", children=[OutlineNode(5, "Button", label="Send Report", is_default=True),
                                         OutlineNode(6, "Button", label="Add", enabled=False)]),
        OutlineNode(7, "CheckBox", label="Urgent", value="on"),
        OutlineNode(8, "Text", label="Status: idle"),
        OutlineNode(9, "Group", label="Totals", children=[OutlineNode(10, "TreeItem", label="Sales", expanded=False,
                                                                       selected=True)]),
        OutlineNode(11, "Image"),
        OutlineNode(12, "Button"),
    ])]


def test_outline_lists_controls_and_hides_layout():
    outline = render(tree(), header=['Window: "Fixture" (in the background)'])
    assert outline.text.split("\n") == [
        'Window: "Fixture" (in the background)',
        '[e1] Edit "Name" value="Ada" (focused)',
        '[e2] Edit "Password" (secure)',
        '[e3] Button "Send Report"',
        '[e4] Button "Add" (disabled)',
        '[e5] CheckBox "Urgent" value="on"',
        '[e6] Text "Status: idle"',
        '[e7] Group "Totals"',
        '  [e8] TreeItem "Sales" (selected, collapsed)',
        "[e9] Button",
    ]
    assert "hunter2" not in outline.text  # a password field never shows its value
    assert outline.entry("e2").secure and outline.entry("e1").focused and outline.entry("e3").is_default
    assert outline.entry("e3").handle == 5 and outline.entry("e99") is None and outline.entry(None) is None
    assert outline.entries[1].summary() == {"ref": "e2", "role": "Edit", "label": "Password", "secure": True,
                                            "focused": False}


def test_outline_stays_inside_what_the_api_accepts():
    nodes = [OutlineNode(n, "Button", label="x" * 500 + '"quoted"\nline') for n in range(1, 900)]
    outline = render(nodes)
    assert len(outline.entries) <= 400 and len(outline.text) <= 14_200
    assert all(len(e.label) <= 300 and len(e.ref) <= 12 and len(e.role) <= 60 for e in outline.entries)
    assert "more elements not shown" in outline.text
    empty = render([], header=["Window"])
    assert empty.entries == [] and "no usable controls" in empty.text


def test_long_values_are_cut():
    outline = render([OutlineNode(1, "Edit", label="Notes", value="a" * 500 + "\nsecond line")])
    assert len(outline.text) < 300 and "…" in outline.text


@pytest.mark.parametrize("role", ["ScrollBar", "Thumb", "TitleBar", "ToolTip"])
def test_chrome_is_listed_as_skipped(role):
    from mindloom_agent.outline import SKIPPED_ROLES

    assert role in SKIPPED_ROLES
