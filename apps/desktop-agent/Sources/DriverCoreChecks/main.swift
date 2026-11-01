import DriverCore
import Foundation

var failures = 0

func check(_ condition: @autoclosure () -> Bool, _ message: String, line: Int = #line) {
    if !condition() {
        failures += 1
        print("FAIL line \(line): \(message)")
    }
}

// MARK: - Policy

for label in ["Send", "Send Now", "Resend", "Sending…", "Move to Trash", "Delete…", "Pay $40",
              "Place Order", "Buy", "Submit", "Don't Save", "Replace", "Confirm Transfer", "Empty Trash"] {
    check(ActionPolicy.looksIrreversible(label), "\(label) is irreversible")
}
for label in ["Save", "Open", "New Message", "Sendai office", "Signature", "Cancel", "Edit", ""] {
    check(!ActionPolicy.looksIrreversible(label), "\(label) is ordinary")
}
check(!ActionPolicy.looksIrreversible(nil), "no label is ordinary")

check(ActionPolicy.isDenied(name: "Terminal", bundleId: nil), "Terminal denied by name")
check(ActionPolicy.isDenied(name: "Whatever", bundleId: "com.apple.keychainaccess"), "Keychain denied by id")
check(ActionPolicy.isDenied(name: "System Settings.app", bundleId: nil), "System Settings denied")
check(!ActionPolicy.isDenied(name: "Mail", bundleId: "com.apple.mail"), "Mail is fine")
check(ActionPolicy.isAllowed(names: ["Mail"], bundleId: "com.apple.mail", allowed: ["mail.app"]), "allowed by name")
check(ActionPolicy.isAllowed(names: ["Numbers"], bundleId: "com.apple.iWork.Numbers", allowed: ["com.apple.iwork.numbers"]),
      "allowed by bundle id")
check(!ActionPolicy.isAllowed(names: ["Numbers"], bundleId: "com.apple.iWork.Numbers", allowed: ["Mail"]), "other app refused")
check(!ActionPolicy.isAllowed(names: ["Terminal"], bundleId: "com.apple.Terminal", allowed: ["Terminal"]),
      "denied app refused even when allowed")
check(!ActionPolicy.isAllowed(names: [""], bundleId: nil, allowed: [""]), "empty names never match")

// MARK: - Key combos

check(KeyCombo("Command+Shift+D")?.normalized == "cmd+shift+d", "aliases and order")
check(KeyCombo("shift + cmd + d")?.normalized == "cmd+shift+d", "spaces and order")
check(KeyCombo("⌘⇧s")?.normalized == "cmd+shift+s", "symbols without separators")
check(KeyCombo("⌘-Enter")?.normalized == "cmd+return", "dash separator and enter alias")
check(KeyCombo("Return")?.normalized == "return", "plain key")
check(KeyCombo("cmd++")?.key == "+", "plus as the key")
check(KeyCombo("cmd+s")?.keyCode == 1, "key code for s")
check(KeyCombo("f5")?.keyCode == 96, "function key code")
check(KeyCombo("escape")?.keyCode == 53, "escape key code")
check(KeyCombo("cmd+shift") == nil, "modifiers alone are not a combo")
check(KeyCombo("a+b") == nil, "two keys are not a combo")
check(KeyCombo("") == nil, "empty is not a combo")
check(ActionPolicy.isIrreversibleKey(KeyCombo("cmd+backspace")!), "cmd+backspace deletes")
check(ActionPolicy.isIrreversibleKey(KeyCombo("Cmd+Shift+D")!), "Mail send shortcut")
check(!ActionPolicy.isIrreversibleKey(KeyCombo("cmd+n")!), "cmd+n is ordinary")
check(ActionPolicy.pressesDefaultButton(KeyCombo("enter")!), "enter presses the default button")
check(!ActionPolicy.pressesDefaultButton(KeyCombo("shift+return")!), "shift+return doesn't")

// MARK: - Outline

let tree = [OutlineNode(handle: 0, role: "AXWindow", label: "New Message", children: [
    OutlineNode(handle: 1, role: "AXGroup", children: [
        OutlineNode(handle: 2, role: "AXTextField", label: "To", value: "ada@example.com", focused: true),
        OutlineNode(handle: 3, role: "AXSecureTextField", label: "Password", value: "hunter2"),
        OutlineNode(handle: 4, role: "AXButton", label: "Send"),
        OutlineNode(handle: 5, role: "AXButton", enabled: false),
        OutlineNode(handle: 6, role: "AXStaticText", value: "Line one\nline \"two\""),
        OutlineNode(handle: 7, role: "AXTextArea", value: String(repeating: "x", count: 500)),
        OutlineNode(handle: 8, role: "AXGroup"),
    ]),
])]
let outline = Outline.render(tree, header: ["App: Mail"])
let lines = outline.text.split(separator: "\n").map(String.init)
check(lines.first == "App: Mail", "header comes first")
check(lines.contains("[e1] AXWindow \"New Message\""), "window line")
check(lines.contains("  [e2] AXTextField \"To\" value=\"ada@example.com\" (focused)"),
      "unlabelled group is skipped and its children move up")
check(lines.contains("  [e3] AXSecureTextField \"Password\" (secure)"), "secure field shows no value")
check(!outline.text.contains("hunter2"), "password never appears")
check(lines.contains("  [e5] AXButton (disabled)"), "unlabelled button still listed, marked disabled")
check(lines.contains("  [e6] AXStaticText \"Line one ⏎ line 'two'\""), "static text as label, newlines and quotes flattened")
check(outline.text.contains("value=\"" + String(repeating: "x", count: 200) + "…\""), "long values are cut")
check(outline.entries.count == 7, "one entry per listed element, empty group omitted")
check(outline.entry("e3")?.secure == true && outline.entry("e3")?.handle == 3, "entries keep handle and secure flag")
check(outline.entry("e2")?.summary == ElementSummary(ref: "e2", role: "AXTextField", label: "To", secure: false, focused: true),
      "summary for the API")

let wide = (0..<50).map { OutlineNode(handle: $0, role: "AXButton", label: "Button \($0)") }
let capped = Outline.render(wide, maxLines: 10)
check(capped.entries.count == 10, "line budget")
check(capped.text.hasSuffix("… 40 more elements not shown (scroll, or switch to the window you need)"), "says what was cut")
check(Outline.render([OutlineNode(handle: 0, role: "AXGroup")]).text.contains("no usable controls"),
      "empty outline is explained")
let selectedRow = Outline.render([OutlineNode(handle: 0, role: "AXRow", selected: true, children: [
    OutlineNode(handle: 1, role: "AXStaticText", value: "Invoice 42")])])
check(selectedRow.text.contains("[e1] AXRow (selected)"), "selected rows are shown")

// MARK: - Gate

let send = outline.entry("e4")!
let to = outline.entry("e2")!
let password = outline.entry("e3")!
func action(_ tool: String, _ args: [String: JSONValue] = [:], approved: Bool = false, expect: String? = nil) -> DeviceAction {
    DeviceAction(id: "a", tool: tool, args: args, approved: approved, expectLabel: expect)
}

check(ActionGate.check(action("press", ["ref": .string("e4")], expect: "Send"), .init(target: send))
      == .refuse(.needsApproval, "“Send” looks like it can't be undone."), "unapproved Send is refused")
check(ActionGate.check(action("press", ["ref": .string("e4")], approved: true, expect: "Send"), .init(target: send))
      == .proceed(foreground: false), "approved Send goes ahead")
check(ActionGate.check(action("press", ["ref": .string("e2"), "irreversible": .bool(true)]), .init(target: to))
      == .refuse(.needsApproval, "Marked as not undoable."), "model flag needs approval too")
check(ActionGate.check(action("press", ["ref": .string("e4")], approved: true, expect: "Save"), .init(target: send))
      == .refuse(nil, "The screen changed: e4 is now “Send”, not “Save”. Look again."), "changed target refused even if approved")
check(ActionGate.check(action("press", ["ref": .string("e9")]), .init(target: nil))
      == .refuse(nil, "The element e9 is no longer on screen. Look again."), "missing target")
check(ActionGate.check(action("set_value", ["ref": .string("e3"), "text": .string("x")], approved: true), .init(target: password))
      == .refuse(.secureField, "That is a password field. Loom never fills those: ask the person."), "never fills passwords")
check(ActionGate.check(action("type_text", ["text": .string("x")]), .init(focusedIsSecure: true))
      == .refuse(.secureField, "That is a password field. Loom never fills those: ask the person."), "never types into a focused password")
check(ActionGate.check(action("type_text", ["text": .string("x")]), .init(secureInputActive: true))
      == .refuse(.secureField, "A password prompt has the keyboard. Loom won't type now: ask the person."), "secure input blocks typing")
check(ActionGate.check(action("set_value", ["ref": .string("e2"), "text": .string("x")], expect: "To"), .init(target: to))
      == .proceed(foreground: false), "set_value in the background")
check(ActionGate.check(action("key", ["combo": .string("return")]), .init(defaultButtonLabel: "Send"))
      == .refuse(.needsApproval, "Return presses “Send”."), "Return on a Send default button needs approval")
check(ActionGate.check(action("key", ["combo": .string("return")]), .init(defaultButtonLabel: "OK"))
      == .proceed(foreground: false), "Return on OK is fine")
check(ActionGate.check(action("key", ["combo": .string("cmd+shift+d")]), .init())
      == .refuse(.needsApproval, "cmd+shift+d usually sends or deletes."), "send shortcut needs approval")
check(ActionGate.check(action("key", ["combo": .string("cmd+n"), "foreground": .bool(true)]), .init())
      == .proceed(foreground: true), "foreground key asks first")
check(ActionGate.check(action("select_menu", ["path": .array([.string("File"), .string("Export…")])]), .init())
      == .proceed(foreground: true), "menus are foreground")
check(ActionGate.check(action("select_menu", ["path": .array([.string("Edit"), .string("Delete")])]), .init())
      == .refuse(.needsApproval, "“Delete” looks like it can't be undone."), "menu Delete needs approval")
check(ActionGate.check(action("press", ["ref": .string("e2")], expect: "To"), .init(target: to, appName: "Notes", appAllowed: false))
      == .refuse(.appNotAllowed, "Notes isn't one of the apps allowed for this automation."), "only allowed apps")
check(ActionGate.check(action("rm"), .init()) == .refuse(nil, "Loom doesn't know how to “rm”."), "unknown tools refused")

// MARK: - Wire format

let stepJSON = """
{"status":"running","message":null,
 "action":{"id":"call_1","tool":"key","args":{"combo":"cmd+s","foreground":true,"n":2},
           "description":"Press cmd+s","approved":false,"expect_label":null},
 "task":{"task_id":"t1","run_id":"r1","skill_id":"s","status":"running","title":"Save it",
         "allowed_apps":["TextEdit"],"log":[{"at":"x","kind":"claim","status":"ok","summary":"Started"}],
         "pending":null,"summary":null,"error":null,"steps":3,"created_at":"2026-11-01T10:00:00.5Z"}}
""".data(using: .utf8)!
do {
    let response = try JSONDecoder().decode(DeviceStepResponse.self, from: stepJSON)
    check(response.action?.driverTool == .key && response.action?.string("combo") == "cmd+s", "decodes the action")
    check(response.action?.flag("foreground") == true && response.action?.args["n"]?.string == "2", "decodes args")
    check(response.task?.allowedApps == ["TextEdit"] && response.task?.steps == 3, "decodes the task")
    check(response.task?.createdDate != nil, "parses created_at")
} catch {
    check(false, "step decode failed: \(error)")
}
do {
    let pendingJSON = #"{"task_id":"t","run_id":"r","status":"needs_person","pending":{"instruction":"Sign in"}}"#
    let task = try JSONDecoder().decode(DeviceTask.self, from: Data(pendingJSON.utf8))
    check(task.status.isWaitingForPerson && task.pending?.instruction == "Sign in", "decodes a waiting task")
} catch {
    check(false, "task decode failed: \(error)")
}
do {
    let body = try JSONEncoder().encode(DeviceActionResult(actionId: "a", ok: false, detail: "no", blocked: .needsApproval))
    let object = try JSONSerialization.jsonObject(with: body) as? [String: Any]
    check(object?["action_id"] as? String == "a" && object?["blocked"] as? String == "needs_approval", "encodes results")
    let observation = DeviceObservation(app: "Mail", bundleId: "com.apple.mail", text: "x", elements: [to.summary], runningApps: ["Mail"])
    let encoded = try JSONSerialization.jsonObject(with: JSONEncoder().encode(observation)) as? [String: Any]
    check(encoded?["bundle_id"] as? String == "com.apple.mail" && encoded?["running_apps"] != nil, "encodes observations")
} catch {
    check(false, "encode failed: \(error)")
}

if failures == 0 {
    print("DriverCoreChecks: all checks passed")
    exit(0)
}
print("DriverCoreChecks: \(failures) failure(s)")
exit(1)
