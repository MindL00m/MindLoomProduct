import pytest

from mindloom_agent import keys, policy


@pytest.mark.parametrize(("raw", "name"), [
    ("EXCEL.EXE", "excel"), ("  Tally   Prime ", "tally prime"), ("Excel.lnk", "excel"), ("", ""), (None, ""),
])
def test_app_names_are_normalized(raw, name):
    assert policy.normalize_app(raw) == name


@pytest.mark.parametrize("names", [
    ["powershell"], ["PowerShell.exe"], ["cmd"], ["regedit", "Registry Editor"], ["Taskmgr", "Task Manager"],
    ["CredentialUIBroker"], ["SecHealthUI", "Windows Security"], ["1Password"], ["KeePassXC"],
    ["explorer", "Windows Explorer"], ["ApplicationFrameHost"], ["MindLoom"], ["WindowsTerminal"],
])
def test_denied_apps(names):
    assert policy.is_denied(names)
    # Allowing it by name changes nothing.
    assert not policy.is_allowed(names, names)


def test_allowed_apps_match_by_any_name_but_never_a_denied_one():
    excel = ("EXCEL", "Microsoft Excel", "Microsoft Office")
    assert policy.is_allowed(excel, ["Excel"]) and policy.is_allowed(excel, ["microsoft excel"])
    assert policy.is_allowed(excel, ["excel.exe"])
    assert not policy.is_allowed(excel, ["Word"]) and not policy.is_allowed(excel, [])
    assert not policy.is_allowed((), ["Excel"]) and not policy.is_allowed(("", " "), [""])
    # One denied name is enough, whatever else the process calls itself.
    assert not policy.is_allowed(("Excel", "PowerShell"), ["Excel"])
    assert not policy.is_denied(excel) and not policy.is_denied(["tally", "TallyPrime"])


@pytest.mark.parametrize("label", [
    "Send", "Send Report", "Resend", "Sending…", "Delete Voucher", "Don't Save", "Submit order", "Pay now",
    "Check out", "Approve", "Accept", "Post", "Empty Recycle Bin", "Confirm", "&Delete",
])
def test_labels_that_look_irreversible(label):
    assert policy.looks_irreversible(label)


@pytest.mark.parametrize("label", ["Save", "Sendai", "Signature", "Add Item", "Cancel", "Bookkeeping", "", None,
                                   "Bookmark", "Payee name", "Close"])
def test_labels_that_do_not(label):
    assert not policy.looks_irreversible(label)


def test_lists_are_lowercase_and_unique():
    for values in (policy.DENIED_APPS, policy.IRREVERSIBLE_WORDS, policy.IRREVERSIBLE_KEYS, policy.BLOCKED_KEYS,
                   policy.NAMED_KEYS):
        assert len(values) == len(set(values))
        assert all(v == v.lower().strip() and v for v in values)
    for name in policy.DENIED_APPS:
        assert policy.normalize_app(name) == name


@pytest.mark.parametrize(("raw", "combo"), [
    ("Ctrl + Shift + S", "ctrl+shift+s"), ("shift+ctrl+s", "ctrl+shift+s"), ("Control-S", "ctrl+s"),
    ("Enter", "return"), ("ALT+F4", "alt+f4"), ("Esc", "escape"), ("del", "delete"), ("Backspace", "backspace"),
    ("opt+s", "alt+s"), ("Windows+R", "win+r"), ("cmd+s", "cmd+s"), ("ctrl++", "ctrl++"), ("-", "-"),
    ("ctrl+-", "ctrl+-"), ("PgDn", "pagedown"), ("", ""),
])
def test_combos_parse_like_the_server(raw, combo):
    assert keys.parse(raw).normalized == combo


@pytest.mark.parametrize("combo", ["enter", "tab", "shift+tab", "ctrl+s", "alt+f4", "f2", "f12", "ctrl+shift+end",
                                   "ctrl+a", "a", "7", "ctrl++", "alt+down", "ctrl+-", "space"])
def test_keys_that_may_be_pressed(combo):
    parsed = keys.parse(combo)
    assert keys.blocked_reason(parsed) is None and parsed.virtual_key is not None


@pytest.mark.parametrize(("combo", "why"), [
    ("win+r", "Windows key"), ("win", "Windows key"), ("win+l", "Windows key"), ("cmd+s", "no cmd key"),
    ("alt+tab", "outside the app"), ("ctrl+shift+esc", "outside the app"), ("ctrl+alt+del", "outside the app"),
    ("ctrl+esc", "outside the app"), ("printscreen", "outside the app"), ("ctrl", "one key"), ("", "one key"),
    ("ctrl+a+b", "one key"), ("ctrl+banana", "can't press"), ("é", "can't press"),
])
def test_keys_that_are_never_pressed(combo, why):
    assert why in keys.blocked_reason(keys.parse(combo))


def test_every_listed_key_is_in_normal_form_and_pressable_unless_blocked():
    for combo in policy.IRREVERSIBLE_KEYS:
        parsed = keys.parse(combo)
        assert parsed.normalized == combo and keys.blocked_reason(parsed) is None and keys.is_irreversible(parsed)
    for combo in policy.BLOCKED_KEYS:
        assert keys.parse(combo).normalized == combo and keys.blocked_reason(keys.parse(combo))
    for name in policy.NAMED_KEYS:
        assert name in keys.NAMED_VK


def test_virtual_keys():
    assert keys.parse("a").virtual_key == 0x41 and keys.parse("ctrl+5").virtual_key == 0x35
    assert keys.parse("enter").virtual_key == 0x0D and keys.parse("f1").virtual_key == 0x70
    assert keys.parse("f12").virtual_key == 0x7B and keys.parse("delete").virtual_key == 0x2E
    assert keys.parse("backspace").virtual_key == 0x08
    assert keys.presses_default_button(keys.parse("Enter")) and not keys.presses_default_button(keys.parse("ctrl+enter"))
    assert keys.presses_focused_button(keys.parse("space")) and not keys.presses_focused_button(keys.parse("tab"))
