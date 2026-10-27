import AppKit
import ApplicationServices
import Foundation
import GuideCore

/// Performs one desktop step anchor through Accessibility. The control is found with
/// `GuideMatcher` against the identity AXEventCapture records, so "this anchor is that
/// control" has one definition. Never sets AXValue. Callers check the allowlist first.
///
/// Finding is split from acting so a caller can confirm the run is still going after a
/// slow search and before anything is pressed.
final class StepPerformer {
    enum Located {
        case element(AXUIElement)
        case needsValue
        case failed(String)
    }

    enum Outcome: Equatable {
        case performed
        case failed(String)
    }

    /// Bounds one search so a huge tree (web views, long tables) cannot stall a run.
    static let maxNodes = 4000

    static func runningApp(for bundleId: String) -> NSRunningApplication? {
        NSRunningApplication.runningApplications(withBundleIdentifier: bundleId)
            .first { !$0.isTerminated }
    }

    /// Windows match by title containment (navigate); menus are searched in the menu
    /// bar; other controls in the focused window first, then the rest. A second match
    /// in the same scope fails rather than picking one.
    func locate(_ anchor: StepAnchor, pid: pid_t, appName: String) -> Located {
        let appElement = AXUIElementCreateApplication(pid)
        AXUIElementSetMessagingTimeout(appElement, 1)

        let match: Match
        switch anchor.action {
        case .fieldFocus, .fieldBlur:
            return .needsValue
        case .navigate:
            match = find(anchor, roots: elements(appElement, kAXWindowsAttribute))
        case .menu:
            guard let bar = element(appElement, kAXMenuBarAttribute) else {
                return .failed("\(appName) has no menu bar to search.")
            }
            match = find(anchor, roots: [bar])
        case .click, .focus:
            match = findInWindows(anchor, appElement: appElement)
        }

        let what = anchor.action == .navigate ? "window" : "control"
        switch match {
        case .found(let element):
            return .element(element)
        case .missing:
            return .failed("Couldn't find the \(what) “\(label(anchor))” in \(appName).")
        case .ambiguous:
            return .failed("More than one \(what) in \(appName) matches “\(label(anchor))”, so Loom didn't pick one.")
        }
    }

    /// click and menu press; focus sets focus; navigate raises the window (the caller
    /// has already activated the app).
    func act(_ anchor: StepAnchor, on element: AXUIElement, appName: String) -> Outcome {
        let status: AXError
        switch anchor.action {
        case .click, .menu:
            status = AXUIElementPerformAction(element, kAXPressAction as CFString)
        case .focus:
            status = AXUIElementSetAttributeValue(element, kAXFocusedAttribute as CFString, kCFBooleanTrue)
        case .navigate:
            status = AXUIElementPerformAction(element, kAXRaiseAction as CFString)
        case .fieldFocus, .fieldBlur:
            return .failed("This step needs a value.")
        }
        guard status == .success else {
            return .failed("\(appName) did not accept the action on “\(label(anchor))” (AX error \(status.rawValue)).")
        }
        return .performed
    }

    // MARK: - Finding the control

    private enum Match {
        case found(AXUIElement)
        case missing
        case ambiguous
    }

    private func findInWindows(_ anchor: StepAnchor, appElement: AXUIElement) -> Match {
        let windows = elements(appElement, kAXWindowsAttribute)
        guard let focused = element(appElement, kAXFocusedWindowAttribute) else {
            return find(anchor, roots: windows)
        }
        let match = find(anchor, roots: [focused])
        guard case .missing = match else { return match }
        return find(anchor, roots: windows.filter { !CFEqual($0, focused) })
    }

    private func find(_ anchor: StepAnchor, roots: [AXUIElement]) -> Match {
        var queue = roots
        var head = 0
        var found: AXUIElement?
        while head < queue.count, head < Self.maxNodes {
            let node = queue[head]
            head += 1
            if let observed = identity(of: node, like: anchor),
               GuideMatcher.matches(expected: anchor, observed: observed) {
                if found != nil { return .ambiguous }
                found = node
            }
            if anchor.action != .navigate, queue.count < Self.maxNodes {
                queue.append(contentsOf: elements(node, kAXChildrenAttribute))
            }
        }
        return found.map(Match.found) ?? .missing
    }

    /// The anchor AXEventCapture would have recorded for this element.
    private func identity(of element: AXUIElement, like anchor: StepAnchor) -> StepAnchor? {
        let role = AXEventCapture.stringAttribute(element, kAXRoleAttribute as String) ?? "AXUnknown"
        if Redaction.isSensitiveRole(role) { return nil }
        if anchor.action == .navigate {
            let title = Redaction.sanitizeWindowTitle(
                AXEventCapture.stringAttribute(element, kAXTitleAttribute as String) ?? ""
            )
            return StepAnchor(bundleId: anchor.bundleId, action: .navigate, role: role, windowTitle: title)
        }
        return StepAnchor(
            bundleId: anchor.bundleId,
            action: anchor.action,
            role: role,
            identifier: AXEventCapture.stringAttribute(element, kAXIdentifierAttribute as String),
            title: anchor.action == .menu ? AXEventCapture.menuTitle(element) : AXEventCapture.controlTitle(element)
        )
    }

    private func element(_ element: AXUIElement, _ attribute: String) -> AXUIElement? {
        var value: AnyObject?
        guard AXUIElementCopyAttributeValue(element, attribute as CFString, &value) == .success,
              let value, CFGetTypeID(value) == AXUIElementGetTypeID() else { return nil }
        return (value as! AXUIElement)
    }

    private func elements(_ element: AXUIElement, _ attribute: String) -> [AXUIElement] {
        var value: AnyObject?
        guard AXUIElementCopyAttributeValue(element, attribute as CFString, &value) == .success,
              let items = value as? [AnyObject] else { return [] }
        return items.compactMap { item in
            CFGetTypeID(item) == AXUIElementGetTypeID() ? (item as! AXUIElement) : nil
        }
    }

    private func label(_ anchor: StepAnchor) -> String {
        anchor.title ?? anchor.windowTitle ?? anchor.identifier ?? anchor.role
    }
}
