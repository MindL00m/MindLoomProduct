import AppKit
import ApplicationServices
import Carbon.HIToolbox
import DriverCore
import Foundation

/// Reads an allowed app through Accessibility and performs one action on it.
///
/// Background first: pressing, setting values, focusing elements and raising windows go
/// through AX, and keys and typing are posted to the app's process (`postToPid`), so the
/// person keeps their cursor and the app they're in. Menu commands and actions the model
/// marks `foreground` need the app in front; the runner asks the person first and does
/// the activating. Callers run the gate (`ActionGate`) before `perform`.
final class LoomDriver: @unchecked Sendable {
    struct Snapshot {
        var observation: DeviceObservation
        var outline: RenderedOutline
        var elements: [AXUIElement]
        var appName: String
        var bundleId: String?
        var pid: pid_t?
        var defaultButtonLabel: String?
        var focusedIsSecure: Bool
    }

    static let maxNodes = 1500
    static let messagingTimeout: Float = 1.5
    static let typeChunk = 16

    private let lock = NSLock()
    private var manualAccessibility: Set<pid_t> = []

    // MARK: - Apps

    static func names(of app: NSRunningApplication) -> [String] {
        var names: [String] = []
        if let name = app.localizedName { names.append(name) }
        if let url = app.bundleURL {
            names.append(url.deletingPathExtension().lastPathComponent)
            if let bundle = Bundle(url: url), let name = bundle.object(forInfoDictionaryKey: "CFBundleName") as? String {
                names.append(name)
            }
        }
        return names
    }

    static func isAllowed(_ app: NSRunningApplication, allowed: [String]) -> Bool {
        ActionPolicy.isAllowed(names: names(of: app), bundleId: app.bundleIdentifier, allowed: allowed)
    }

    static func runningAllowedApps(_ allowed: [String]) -> [NSRunningApplication] {
        let me = ProcessInfo.processInfo.processIdentifier
        return NSWorkspace.shared.runningApplications.filter {
            !$0.isTerminated && $0.activationPolicy == .regular && $0.processIdentifier != me
                && isAllowed($0, allowed: allowed)
        }
    }

    /// The installed app for an allowed name (standard app folders only).
    static func appURL(named name: String) -> URL? {
        let wanted = ActionPolicy.normalizeApp(name)
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        let folders = ["/Applications", "/System/Applications", "/System/Applications/Utilities",
                       "/Applications/Utilities", "\(home)/Applications"]
        for folder in folders {
            guard let items = try? FileManager.default.contentsOfDirectory(atPath: folder) else { continue }
            if let match = items.first(where: { $0.hasSuffix(".app") && ActionPolicy.normalizeApp($0) == wanted }) {
                return URL(fileURLWithPath: folder).appendingPathComponent(match)
            }
        }
        return nil
    }

    /// Opens (or finds) an allowed app without bringing it forward.
    func openApp(_ name: String, allowed: [String]) async -> (NSRunningApplication?, String) {
        guard !ActionPolicy.isDenied(name: name, bundleId: nil),
              allowed.map(ActionPolicy.normalizeApp).contains(ActionPolicy.normalizeApp(name)) else {
            return (nil, "\(name) isn't one of the apps allowed for this automation.")
        }
        if let running = Self.runningAllowedApps([name]).first {
            return (running, "\(running.localizedName ?? name) is open.")
        }
        guard let url = Self.appURL(named: name) else {
            return (nil, "Couldn't find \(name) in Applications. Ask the person to open it.")
        }
        let configuration = NSWorkspace.OpenConfiguration()
        configuration.activates = false
        configuration.addsToRecentItems = false
        do {
            let app = try await NSWorkspace.shared.openApplication(at: url, configuration: configuration)
            for _ in 0..<40 where !app.isFinishedLaunching {
                try? await Task.sleep(nanoseconds: 250_000_000)
            }
            try? await Task.sleep(nanoseconds: 800_000_000)  // first window
            guard Self.isAllowed(app, allowed: allowed) else {
                return (nil, "\(name) opened as a different app, so Loom won't use it.")
            }
            return (app, "Opened \(app.localizedName ?? name) in the background.")
        } catch {
            return (nil, "Couldn't open \(name): \(error.localizedDescription)")
        }
    }

    // MARK: - Reading the screen

    func snapshot(app: NSRunningApplication?, allowed: [String]) -> Snapshot {
        let running = Self.runningAllowedApps(allowed).compactMap(\.localizedName)
        guard let app, !app.isTerminated else {
            let text = "No allowed app is being used yet. Allowed apps: \(allowed.joined(separator: ", ")). "
                + (running.isEmpty ? "None is open: use open_app." : "Open: \(running.joined(separator: ", ")). Use open_app.")
            return Snapshot(
                observation: DeviceObservation(app: "(none)", bundleId: "", text: text, elements: [], runningApps: running),
                outline: RenderedOutline(text: text, entries: []), elements: [], appName: "(none)",
                bundleId: nil, pid: nil, defaultButtonLabel: nil, focusedIsSecure: false
            )
        }
        let pid = app.processIdentifier
        let appElement = AXUIElementCreateApplication(pid)
        AXUIElementSetMessagingTimeout(appElement, Self.messagingTimeout)
        enableManualAccessibility(appElement, pid: pid)

        let name = app.localizedName ?? app.bundleIdentifier ?? "App"
        let frontmost = NSWorkspace.shared.frontmostApplication?.processIdentifier == pid
        var header = ["App: \(name) (\(frontmost ? "in front" : "in the background"))"]
        let windows = Self.elements(appElement, kAXWindowsAttribute)
        let focusedWindow = Self.element(appElement, kAXFocusedWindowAttribute)
            ?? Self.element(appElement, kAXMainWindowAttribute) ?? windows.first
        if let focusedWindow {
            let others = windows.filter { !CFEqual($0, focusedWindow) }
                .compactMap { Self.string($0, kAXTitleAttribute) }.filter { !$0.isEmpty }
            if !others.isEmpty {
                header.append("Other windows: " + others.prefix(10).map { "\"\($0)\"" }.joined(separator: ", "))
            }
        } else {
            header.append("No open window.")
        }
        if let bar = Self.element(appElement, kAXMenuBarAttribute) {
            let menus = Self.elements(bar, kAXChildrenAttribute).compactMap { Self.string($0, kAXTitleAttribute) }
                .filter { !$0.isEmpty && $0 != "Apple" }
            if !menus.isEmpty { header.append("Menu bar: " + menus.joined(separator: ", ")) }
        }

        var table: [AXUIElement] = []
        var budget = Self.maxNodes
        let roots = focusedWindow.map { [node(for: $0, table: &table, budget: &budget, depth: 0)] } ?? []
        let outline = Outline.render(roots, header: header)

        let focused = Self.element(appElement, kAXFocusedUIElementAttribute)
        let focusedRole = focused.flatMap { Self.string($0, kAXRoleAttribute) } ?? ""
        let focusedSub = focused.flatMap { Self.string($0, kAXSubroleAttribute) }
        let defaultButton = focusedWindow.flatMap { Self.element($0, kAXDefaultButtonAttribute) }
        let observation = DeviceObservation(
            app: name, bundleId: app.bundleIdentifier ?? "", text: outline.text,
            elements: outline.entries.map(\.summary), runningApps: running
        )
        return Snapshot(
            observation: observation, outline: outline, elements: table, appName: name,
            bundleId: app.bundleIdentifier, pid: pid,
            defaultButtonLabel: defaultButton.flatMap { Self.label(of: $0) },
            focusedIsSecure: Outline.secureRoles.contains(focusedRole) || focusedSub == "AXSecureTextField"
        )
    }

    /// Electron and Chromium apps build their tree only when asked.
    private func enableManualAccessibility(_ appElement: AXUIElement, pid: pid_t) {
        lock.lock()
        let fresh = manualAccessibility.insert(pid).inserted
        lock.unlock()
        if fresh {
            AXUIElementSetAttributeValue(appElement, "AXManualAccessibility" as CFString, kCFBooleanTrue)
        }
    }

    private static let readAttributes = [
        kAXRoleAttribute, kAXSubroleAttribute, kAXTitleAttribute, kAXDescriptionAttribute,
        kAXValueAttribute, kAXEnabledAttribute, kAXFocusedAttribute, kAXSelectedAttribute,
        kAXPlaceholderValueAttribute, kAXTitleUIElementAttribute,
    ] as [CFString]

    private func node(for element: AXUIElement, table: inout [AXUIElement], budget: inout Int, depth: Int) -> OutlineNode {
        let handle = table.count
        table.append(element)
        budget -= 1
        var raw: CFArray?
        AXUIElementCopyMultipleAttributeValues(
            element, Self.readAttributes as CFArray, AXCopyMultipleAttributeOptions(rawValue: 0), &raw
        )
        let values = (raw as? [AnyObject]) ?? []
        func value(_ index: Int) -> AnyObject? {
            guard index < values.count else { return nil }
            let item = values[index]
            if CFGetTypeID(item) == AXValueGetTypeID(), AXValueGetType(item as! AXValue) == .axError { return nil }
            return item
        }
        let role = (value(0) as? String) ?? "AXUnknown"
        let subrole = value(1) as? String
        let secure = Outline.secureRoles.contains(role) || subrole == "AXSecureTextField"
        var label = (value(2) as? String).flatMap { $0.isEmpty ? nil : $0 }
            ?? (value(3) as? String).flatMap { $0.isEmpty ? nil : $0 }
        if label == nil, let titleElement = value(9), CFGetTypeID(titleElement) == AXUIElementGetTypeID() {
            label = Self.label(of: titleElement as! AXUIElement)
        }
        if label == nil { label = (value(8) as? String).flatMap { $0.isEmpty ? nil : $0 } }
        let text = secure ? nil : Self.display(value(4), role: role)

        var children: [OutlineNode] = []
        if budget > 0, depth < 40 {
            for child in Self.children(of: element, role: role) {
                guard budget > 0 else { break }
                children.append(node(for: child, table: &table, budget: &budget, depth: depth + 1))
            }
        }
        return OutlineNode(
            handle: handle, role: role, subrole: subrole, label: label, value: text,
            enabled: (value(5) as? Bool) ?? true, focused: (value(6) as? Bool) ?? false,
            selected: (value(7) as? Bool) ?? false, children: children
        )
    }

    /// Long tables and lists contribute their visible rows only.
    private static func children(of element: AXUIElement, role: String) -> [AXUIElement] {
        if ["AXTable", "AXOutline"].contains(role) {
            let rows = elements(element, kAXVisibleRowsAttribute)
            if !rows.isEmpty { return rows }
        }
        if ["AXList", "AXBrowser", "AXGrid", "AXScrollArea"].contains(role) {
            let visible = elements(element, kAXVisibleChildrenAttribute)
            if !visible.isEmpty { return visible }
        }
        return elements(element, kAXChildrenAttribute)
    }

    private static func display(_ value: AnyObject?, role: String) -> String? {
        guard let value else { return nil }
        if let text = value as? String { return text.isEmpty ? nil : text }
        if let number = value as? NSNumber {
            if ["AXCheckBox", "AXRadioButton", "AXMenuItem"].contains(role) {
                return number.intValue == 0 ? "off" : (number.intValue == 1 ? "on" : "mixed")
            }
            return number.stringValue
        }
        return nil
    }

    // MARK: - Acting

    /// `keepGoing` is checked between typed chunks; false stops typing (the person took over).
    func perform(_ action: DeviceAction, on snapshot: Snapshot, keepGoing: @escaping @Sendable () -> Bool) -> DeviceActionResult {
        guard let tool = action.driverTool, let pid = snapshot.pid else {
            return result(action, false, "No app to act in. Use open_app first.")
        }
        let target = action.ref.flatMap { snapshot.outline.entry($0) }
        let element = target.flatMap { snapshot.elements.indices.contains($0.handle) ? snapshot.elements[$0.handle] : nil }
        if let element, Self.string(element, kAXRoleAttribute) == nil {
            return result(action, false, "That element has gone from the screen. Look again.")
        }
        let appElement = AXUIElementCreateApplication(pid)
        AXUIElementSetMessagingTimeout(appElement, Self.messagingTimeout)

        switch tool {
        case .press:
            guard let element else { return result(action, false, "No element to press.") }
            return press(element, action: action, label: target?.label ?? "")
        case .setValue:
            guard let element else { return result(action, false, "No field to fill.") }
            return setValue(element, text: action.string("text") ?? "", action: action)
        case .typeText:
            if let element {
                AXUIElementSetAttributeValue(element, kAXFocusedAttribute as CFString, kCFBooleanTrue)
                usleep(150_000)
            }
            return type(action.string("text") ?? "", pid: pid, appElement: appElement, action: action, keepGoing: keepGoing)
        case .key:
            guard let combo = KeyCombo(action.string("combo") ?? ""), let code = combo.keyCode else {
                return result(action, false, "Loom can't press “\(action.string("combo") ?? "")”.")
            }
            postKey(code, modifiers: combo.modifiers, pid: pid)
            return result(action, true, "Pressed \(combo.normalized).")
        case .selectMenu:
            return selectMenu(action.args["path"]?.strings ?? [], appElement: appElement, action: action)
        case .scroll:
            guard let element else { return result(action, false, "No element to scroll.") }
            return scroll(element, down: action.string("direction") != "up", pid: pid, action: action)
        case .focusWindow:
            return focusWindow(action.string("title") ?? "", appElement: appElement, action: action)
        case .openApp, .wait:
            return result(action, false, "The runner handles \(tool.rawValue).")
        }
    }

    private func press(_ element: AXUIElement, action: DeviceAction, label: String) -> DeviceActionResult {
        let name = label.isEmpty ? (action.ref ?? "the control") : "“\(label)”"
        var status = AXUIElementPerformAction(element, kAXPressAction as CFString)
        if status == .success { return result(action, true, "Pressed \(name).") }
        for fallback in [kAXConfirmAction, kAXPickAction] {
            if Self.actions(element).contains(fallback as String) {
                status = AXUIElementPerformAction(element, fallback as CFString)
                if status == .success { return result(action, true, "Pressed \(name).") }
            }
        }
        // Rows, cells and list items select rather than press.
        var settable: DarwinBoolean = false
        if AXUIElementIsAttributeSettable(element, kAXSelectedAttribute as CFString, &settable) == .success, settable.boolValue,
           AXUIElementSetAttributeValue(element, kAXSelectedAttribute as CFString, kCFBooleanTrue) == .success {
            return result(action, true, "Selected \(name).")
        }
        return result(action, false, "\(name) didn't accept a press (AX error \(status.rawValue)). Try another way or ask_person.")
    }

    private func setValue(_ element: AXUIElement, text: String, action: DeviceAction) -> DeviceActionResult {
        var settable: DarwinBoolean = false
        AXUIElementIsAttributeSettable(element, kAXValueAttribute as CFString, &settable)
        guard settable.boolValue else {
            return result(action, false, "This field can't be set directly. Use type_text with this ref.")
        }
        AXUIElementSetAttributeValue(element, kAXFocusedAttribute as CFString, kCFBooleanTrue)
        let status = AXUIElementSetAttributeValue(element, kAXValueAttribute as CFString, text as CFString)
        guard status == .success else {
            return result(action, false, "The field refused the value (AX error \(status.rawValue)). Try type_text.")
        }
        usleep(150_000)
        // Some apps (Electron, web content) report success without applying it: read it back.
        let now = Self.string(element, kAXValueAttribute) ?? ""
        if Self.same(now, text) { return result(action, true, "The field now shows the value.") }
        return result(action, false, "The field shows “\(String(now.prefix(120)))” instead. Try type_text with this ref.")
    }

    private func type(
        _ text: String, pid: pid_t, appElement: AXUIElement, action: DeviceAction, keepGoing: @escaping () -> Bool
    ) -> DeviceActionResult {
        guard !text.isEmpty else { return result(action, false, "Nothing to type.") }
        let source = CGEventSource(stateID: .hidSystemState)
        var buffer: [UniChar] = []
        func flush() {
            guard !buffer.isEmpty else { return }
            for down in [true, false] {
                guard let event = CGEvent(keyboardEventSource: source, virtualKey: 0, keyDown: down) else { continue }
                event.flags = []
                event.keyboardSetUnicodeString(stringLength: buffer.count, unicodeString: buffer)
                event.postToPid(pid)
            }
            buffer.removeAll()
            usleep(12_000)
        }
        for character in text {
            if !keepGoing() {
                flush()
                return result(action, false, "Stopped typing: the person started using the app.")
            }
            switch character {
            case "\n", "\r", "\r\n":
                flush()
                postKey(UInt16(kVK_Return), modifiers: [], pid: pid)
            case "\t":
                flush()
                postKey(UInt16(kVK_Tab), modifiers: [], pid: pid)
            default:
                buffer.append(contentsOf: Array(String(character).utf16))
                if buffer.count >= Self.typeChunk { flush() }
            }
        }
        flush()
        usleep(150_000)
        let focused = Self.element(appElement, kAXFocusedUIElementAttribute)
        if let value = focused.flatMap({ Self.string($0, kAXValueAttribute) }) {
            return result(action, true, "Typed. The field now shows “\(String(value.suffix(160)))”.")
        }
        return result(action, true, "Typed \(text.count) characters (the field doesn't report its text).")
    }

    func postKey(_ code: UInt16, modifiers: Set<KeyCombo.Modifier>, pid: pid_t) {
        let source = CGEventSource(stateID: .hidSystemState)
        var flags: CGEventFlags = []
        if modifiers.contains(.cmd) { flags.insert(.maskCommand) }
        if modifiers.contains(.ctrl) { flags.insert(.maskControl) }
        if modifiers.contains(.option) { flags.insert(.maskAlternate) }
        if modifiers.contains(.shift) { flags.insert(.maskShift) }
        for down in [true, false] {
            guard let event = CGEvent(keyboardEventSource: source, virtualKey: code, keyDown: down) else { continue }
            event.flags = flags
            event.postToPid(pid)
            usleep(8_000)
        }
    }

    private func selectMenu(_ path: [String], appElement: AXUIElement, action: DeviceAction) -> DeviceActionResult {
        let wanted = path.map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
        guard !wanted.isEmpty, let bar = Self.element(appElement, kAXMenuBarAttribute) else {
            return result(action, false, "This app has no menu bar Loom can use.")
        }
        var items = Self.elements(bar, kAXChildrenAttribute)
        var current: AXUIElement?
        for (depth, title) in wanted.enumerated() {
            guard let match = items.first(where: { Self.menuTitleMatches(Self.string($0, kAXTitleAttribute), title) }) else {
                let options = items.compactMap { Self.string($0, kAXTitleAttribute) }.filter { !$0.isEmpty }
                return result(action, false, "No “\(title)” in the menu. It has: \(options.prefix(30).joined(separator: ", ")).")
            }
            current = match
            if depth < wanted.count - 1 {
                let menu = Self.elements(match, kAXChildrenAttribute).first
                items = menu.map { Self.elements($0, kAXChildrenAttribute) } ?? []
            }
        }
        guard let item = current else { return result(action, false, "Menu path is empty.") }
        if let enabled = Self.bool(item, kAXEnabledAttribute), !enabled {
            return result(action, false, "“\(wanted.joined(separator: " › "))” is greyed out right now.")
        }
        let status = AXUIElementPerformAction(item, kAXPressAction as CFString)
        guard status == .success else {
            return result(action, false, "The menu item didn't respond (AX error \(status.rawValue)).")
        }
        return result(action, true, "Chose \(wanted.joined(separator: " › ")).")
    }

    private func scroll(_ element: AXUIElement, down: Bool, pid: pid_t, action: DeviceAction) -> DeviceActionResult {
        var node: AXUIElement? = element
        for _ in 0..<8 {
            guard let current = node else { break }
            if let bar = Self.element(current, kAXVerticalScrollBarAttribute),
               let value = Self.number(bar, kAXValueAttribute) {
                let next = max(0, min(1, value + (down ? 0.25 : -0.25)))
                if AXUIElementSetAttributeValue(bar, kAXValueAttribute as CFString, NSNumber(value: next)) == .success {
                    return result(action, true, next == value ? "Already at the \(down ? "bottom" : "top")." : "Scrolled \(down ? "down" : "up").")
                }
            }
            node = Self.element(current, kAXParentAttribute)
        }
        // No scroll bar to set: a scroll-wheel event at the element, sent to the app.
        guard let frame = Self.frame(element),
              let event = CGEvent(scrollWheelEvent2Source: nil, units: .line, wheelCount: 1,
                                  wheel1: down ? -8 : 8, wheel2: 0, wheel3: 0) else {
            return result(action, false, "Couldn't scroll this element.")
        }
        event.location = CGPoint(x: frame.midX, y: frame.midY)
        event.postToPid(pid)
        return result(action, true, "Scrolled \(down ? "down" : "up").")
    }

    private func focusWindow(_ title: String, appElement: AXUIElement, action: DeviceAction) -> DeviceActionResult {
        let wanted = title.lowercased()
        guard !wanted.isEmpty,
              let window = Self.elements(appElement, kAXWindowsAttribute).first(where: {
                  (Self.string($0, kAXTitleAttribute) ?? "").lowercased().contains(wanted)
              }) else {
            return result(action, false, "No window titled “\(title)”.")
        }
        AXUIElementSetAttributeValue(window, kAXMainAttribute as CFString, kCFBooleanTrue)
        AXUIElementPerformAction(window, kAXRaiseAction as CFString)
        AXUIElementSetAttributeValue(appElement, kAXFocusedWindowAttribute as CFString, window)
        return result(action, true, "Switched to “\(Self.string(window, kAXTitleAttribute) ?? title)”.")
    }

    private func result(_ action: DeviceAction, _ ok: Bool, _ detail: String) -> DeviceActionResult {
        DeviceActionResult(actionId: action.id, ok: ok, detail: detail)
    }

    // MARK: - AX helpers

    static func element(_ element: AXUIElement, _ attribute: String) -> AXUIElement? {
        var value: AnyObject?
        guard AXUIElementCopyAttributeValue(element, attribute as CFString, &value) == .success,
              let value, CFGetTypeID(value) == AXUIElementGetTypeID() else { return nil }
        return (value as! AXUIElement)
    }

    static func elements(_ element: AXUIElement, _ attribute: String) -> [AXUIElement] {
        var value: AnyObject?
        guard AXUIElementCopyAttributeValue(element, attribute as CFString, &value) == .success,
              let items = value as? [AnyObject] else { return [] }
        return items.compactMap { CFGetTypeID($0) == AXUIElementGetTypeID() ? ($0 as! AXUIElement) : nil }
    }

    static func string(_ element: AXUIElement, _ attribute: String) -> String? {
        var value: AnyObject?
        guard AXUIElementCopyAttributeValue(element, attribute as CFString, &value) == .success else { return nil }
        if let text = value as? String { return text }
        if let number = value as? NSNumber { return number.stringValue }
        return nil
    }

    static func bool(_ element: AXUIElement, _ attribute: String) -> Bool? {
        var value: AnyObject?
        guard AXUIElementCopyAttributeValue(element, attribute as CFString, &value) == .success else { return nil }
        return value as? Bool
    }

    static func number(_ element: AXUIElement, _ attribute: String) -> Double? {
        var value: AnyObject?
        guard AXUIElementCopyAttributeValue(element, attribute as CFString, &value) == .success else { return nil }
        return (value as? NSNumber)?.doubleValue
    }

    static func actions(_ element: AXUIElement) -> [String] {
        var names: CFArray?
        guard AXUIElementCopyActionNames(element, &names) == .success else { return [] }
        return (names as? [String]) ?? []
    }

    static func label(of element: AXUIElement) -> String? {
        for attribute in [kAXTitleAttribute, kAXDescriptionAttribute, kAXValueAttribute] {
            if let text = string(element, attribute as String), !text.isEmpty { return text }
        }
        return nil
    }

    static func frame(_ element: AXUIElement) -> CGRect? {
        var position: AnyObject?
        var size: AnyObject?
        guard AXUIElementCopyAttributeValue(element, kAXPositionAttribute as CFString, &position) == .success,
              AXUIElementCopyAttributeValue(element, kAXSizeAttribute as CFString, &size) == .success,
              let position, let size else { return nil }
        var point = CGPoint.zero
        var extent = CGSize.zero
        AXValueGetValue(position as! AXValue, .cgPoint, &point)
        AXValueGetValue(size as! AXValue, .cgSize, &extent)
        return CGRect(origin: point, size: extent)
    }

    static func menuTitleMatches(_ title: String?, _ wanted: String) -> Bool {
        func clean(_ text: String) -> String {
            text.lowercased().replacingOccurrences(of: "…", with: "").replacingOccurrences(of: "...", with: "")
                .trimmingCharacters(in: .whitespaces)
        }
        guard let title else { return false }
        return clean(title) == clean(wanted)
    }

    static func same(_ a: String, _ b: String) -> Bool {
        func clean(_ text: String) -> String {
            text.replacingOccurrences(of: "\r\n", with: "\n").replacingOccurrences(of: "\r", with: "\n")
                .trimmingCharacters(in: .whitespacesAndNewlines)
        }
        return clean(a) == clean(b)
    }

    static var secureInputActive: Bool { IsSecureEventInputEnabled() }
}
