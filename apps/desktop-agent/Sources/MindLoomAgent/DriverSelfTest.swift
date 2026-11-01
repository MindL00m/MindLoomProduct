import AppKit
import DriverCore
import Foundation

/// `Loom Capture --driver-selftest <Loom Fixture.app> <log file>` drives the fixture app
/// through LoomDriver and ActionGate, the same way a device task does, and checks each
/// result by reading the app back. Launch it with `open` so macOS checks Loom Capture's
/// own Accessibility permission (see scripts/driver-selftest.sh).
@MainActor
enum DriverSelfTest {
    static let fixtureName = "Loom Fixture"

    static func runAndExit(_ arguments: [String]) {
        let app = NSApplication.shared
        app.setActivationPolicy(.accessory)
        Task { @MainActor in
            let code = await run(arguments)
            exit(code)
        }
        app.run()
    }

    private static var log: FileHandle?
    private static var failures = 0

    private static func say(_ line: String) {
        print(line)
        log?.write(Data((line + "\n").utf8))
    }

    private static func check(_ ok: Bool, _ name: String, _ detail: String = "") {
        if !ok { failures += 1 }
        say("\(ok ? "PASS" : "FAIL") \(name)\(detail.isEmpty ? "" : " — \(detail)")")
    }

    private static func note(_ name: String, _ detail: String) {
        say("INFO \(name) — \(detail)")
    }

    private static func pause(_ seconds: Double) async {
        try? await Task.sleep(nanoseconds: UInt64(seconds * 1_000_000_000))
    }

    static func run(_ arguments: [String]) async -> Int32 {
        guard arguments.count >= 2 else {
            print("usage: --driver-selftest <Loom Fixture.app> <log file>")
            return 64
        }
        FileManager.default.createFile(atPath: arguments[1], contents: nil)
        log = FileHandle(forWritingAtPath: arguments[1])
        defer { try? log?.close() }

        let trusted = AccessibilityPermission.isTrusted
        check(trusted, "Accessibility is granted to \(AccessibilityPermission.currentAppPath)")
        guard trusted else { return 2 }

        let allowed = [fixtureName]
        let driver = LoomDriver()
        let front = NSWorkspace.shared.frontmostApplication

        // Open the fixture without bringing it forward, then make sure the person's app is in front.
        NSRunningApplication.runningApplications(withBundleIdentifier: "com.mindloom.driver-fixture").forEach { $0.terminate() }
        await pause(0.5)
        let configuration = NSWorkspace.OpenConfiguration()
        configuration.activates = false
        guard let fixture = try? await NSWorkspace.shared.openApplication(
            at: URL(fileURLWithPath: arguments[0]), configuration: configuration
        ) else {
            check(false, "open the fixture at \(arguments[0])")
            return 1
        }
        await pause(2)
        front?.activate()
        await pause(0.8)
        defer { fixture.terminate() }
        let frontName = NSWorkspace.shared.frontmostApplication?.localizedName ?? "?"
        note("frontmost app during background checks", frontName)

        func snap() -> LoomDriver.Snapshot { driver.snapshot(app: fixture, allowed: allowed) }
        func entry(_ s: LoomDriver.Snapshot, _ label: String, role: String? = nil) -> OutlineEntry? {
            s.outline.entries.first { $0.label == label && (role == nil || $0.role == role) }
        }
        func act(_ tool: String, _ args: [String: JSONValue], on s: LoomDriver.Snapshot, approved: Bool = false,
                 label: String? = nil) -> (ActionGate.Verdict, DeviceActionResult?) {
            let action = DeviceAction(id: tool, tool: tool, args: args, approved: approved, expectLabel: label)
            let context = ActionGate.Context(
                target: action.ref.flatMap { s.outline.entry($0) }, defaultButtonLabel: s.defaultButtonLabel,
                focusedIsSecure: s.focusedIsSecure, secureInputActive: LoomDriver.secureInputActive,
                appName: s.appName, appAllowed: LoomDriver.isAllowed(fixture, allowed: allowed)
            )
            let verdict = ActionGate.check(action, context)
            guard case .proceed = verdict else { return (verdict, nil) }
            return (verdict, driver.perform(action, on: s, keepGoing: { true }))
        }
        func status(_ s: LoomDriver.Snapshot) -> String {
            s.outline.text.split(separator: "\n").first { $0.contains("\"Status\"") }.map(String.init) ?? "(no status)"
        }
        func inFront() -> Bool { NSWorkspace.shared.frontmostApplication?.processIdentifier == fixture.processIdentifier }

        // 1. Reading the window.
        var s = snap()
        say("---- outline ----\n\(s.outline.text)\n-----------------")
        for (label, role) in [("Name", "AXTextField"), ("Notes", "AXTextField"), ("Urgent", "AXCheckBox"),
                              ("Add Item", "AXButton"), ("Send Report", "AXButton")] {
            check(entry(s, label, role: role) != nil, "outline lists \(role) “\(label)”")
        }
        let password = s.outline.entries.first { $0.secure }
        check(password != nil, "outline marks the password field secure")
        check(s.outline.text.contains("Menu bar:") && s.outline.text.contains("List"), "outline lists the menu bar")
        check(s.observation.elements.count == s.outline.entries.count, "observation carries every element")
        check(s.defaultButtonLabel == "Send Report", "reads the default button", s.defaultButtonLabel ?? "nil")
        check(LoomDriver.isAllowed(fixture, allowed: allowed), "fixture counts as an allowed app")
        check(!LoomDriver.secureInputActive, "secure input is off")

        // 2. Background value, press and checkbox.
        guard let name = entry(s, "Name"), let add = entry(s, "Add Item"), let urgent = entry(s, "Urgent"),
              let notes = entry(s, "Notes"), let send = entry(s, "Send Report") else { return 1 }
        var (verdict, result) = act("set_value", ["ref": .string(name.ref), "text": .string("Ada Lovelace")], on: s, label: "Name")
        check(result?.ok == true, "set_value fills Name in the background", result?.detail ?? "\(verdict)")
        (verdict, result) = act("press", ["ref": .string(add.ref)], on: s, label: "Add Item")
        check(result?.ok == true, "press Add Item", result?.detail ?? "\(verdict)")
        await pause(0.4)
        s = snap()
        check(s.outline.text.contains("Ada Lovelace") && status(s).contains("added Ada Lovelace"),
              "the app shows the added item", status(s))
        check(!inFront(), "the fixture stayed in the background", NSWorkspace.shared.frontmostApplication?.localizedName ?? "?")

        (verdict, result) = act("press", ["ref": .string(urgent.ref)], on: s, label: "Urgent")
        await pause(0.3)
        s = snap()
        check(result?.ok == true && status(s).contains("urgent on"), "press toggles the Urgent checkbox", status(s))

        // 3. The gate.
        (verdict, _) = act("press", ["ref": .string(send.ref)], on: s, label: "Send Report")
        check(verdict == .refuse(.needsApproval, "“Send Report” looks like it can't be undone."),
              "unapproved Send Report is refused", "\(verdict)")
        (verdict, _) = act("key", ["combo": .string("return")], on: s)
        check(verdict == .refuse(.needsApproval, "Return presses “Send Report”."), "Return on the default Send button is refused", "\(verdict)")
        if let password {
            (verdict, _) = act("set_value", ["ref": .string(password.ref), "text": .string("hunter2")], on: s, approved: true)
            check(verdict == .refuse(.secureField, "That is a password field. Loom never fills those: ask the person."),
                  "password field is refused even when approved", "\(verdict)")
        }
        (verdict, _) = act("press", ["ref": .string(send.ref)], on: s, label: "Add Item")
        check({ if case .refuse(nil, _) = verdict { return true } else { return false } }(), "a changed target is refused", "\(verdict)")
        let (terminal, terminalDetail) = await driver.openApp("Terminal", allowed: ["Terminal"])
        check(terminal == nil, "open_app refuses Terminal", terminalDetail)
        let (opened, openedDetail) = await driver.openApp(fixtureName, allowed: allowed)
        check(opened?.processIdentifier == fixture.processIdentifier, "open_app finds the running fixture", openedDetail)

        // 4. Typing and keys posted to the background process (reported; apps differ).
        (verdict, result) = act("type_text", ["ref": .string(notes.ref), "text": .string("hello world")], on: s, label: "Notes")
        await pause(0.4)
        s = snap()
        let typed = s.outline.text.contains("hello world")
        note("type_text in the background", typed ? "worked" : "did not land (\(result?.detail ?? "\(verdict)"))")
        check(!inFront(), "typing kept the fixture in the background")
        (verdict, result) = act("key", ["combo": .string("cmd+k")], on: s)
        await pause(0.4)
        s = snap()
        note("cmd+k (Clear List) in the background", status(s).contains("cleared") ? "worked" : "did not land: \(status(s))")

        // 5. Approved Send.
        s = snap()
        if let send = entry(s, "Send Report") {
            (verdict, result) = act("press", ["ref": .string(send.ref)], on: s, approved: true, label: "Send Report")
            await pause(0.4)
            s = snap()
            check(status(s).contains("report sent"), "approved Send Report goes through", status(s))
        }

        // 6. A menu command in front, then the person's app comes back.
        let previous = NSWorkspace.shared.frontmostApplication
        fixture.activate()
        await pause(0.6)
        s = snap()
        (verdict, result) = act("select_menu", ["path": .array([.string("List"), .string("Clear List")])], on: s)
        await pause(0.4)
        s = snap()
        check(result?.ok == true && status(s).contains("cleared"), "select_menu List › Clear List", result?.detail ?? "\(verdict)")
        (verdict, _) = act("select_menu", ["path": .array([.string("List"), .string("Delete Everything")])], on: s)
        check(verdict == .refuse(.needsApproval, "“Delete Everything” looks like it can't be undone."), "menu Delete needs approval")
        (verdict, result) = act("select_menu", ["path": .array([.string("List"), .string("Nope")])], on: s)
        check(result?.ok == false && (result?.detail.contains("Clear List") ?? false), "a missing menu item lists the real ones",
              result?.detail ?? "")
        previous?.activate()
        await pause(0.5)
        check(!inFront(), "the person's app is back in front")

        // 7. Timing: how long one read takes.
        let started = Date()
        _ = snap()
        note("snapshot time", String(format: "%.0f ms", Date().timeIntervalSince(started) * 1000))

        say(failures == 0 ? "SELFTEST PASSED" : "SELFTEST FAILED (\(failures))")
        return failures == 0 ? 0 : 1
    }
}
