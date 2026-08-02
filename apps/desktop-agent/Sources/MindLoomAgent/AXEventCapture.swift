import AppKit
import ApplicationServices
import Foundation

protocol AXEventCaptureDelegate: AnyObject {
    func axCaptureDidEmit(_ event: LocalInteractionEvent)
}

private let axSelectedMenuItemChangedNotification = "AXSelectedMenuItemChanged" as CFString

/// Observes Accessibility notifications for the focused allowlisted app only.
final class AXEventCapture {
    weak var delegate: AXEventCaptureDelegate?

    private var observer: AXObserver?
    private var observedElement: AXUIElement?
    private var observedPid: pid_t = 0
    private var lastWindowTitle: String = ""
    private var lastFocusedSignature: String = ""

    func stop() {
        if let observer, let element = observedElement {
            AXObserverRemoveNotification(observer, element, kAXFocusedUIElementChangedNotification as CFString)
            AXObserverRemoveNotification(observer, element, kAXTitleChangedNotification as CFString)
            AXObserverRemoveNotification(observer, element, axSelectedMenuItemChangedNotification)
        }
        if let observer {
            CFRunLoopRemoveSource(
                CFRunLoopGetMain(),
                AXObserverGetRunLoopSource(observer),
                .defaultMode
            )
        }
        observer = nil
        observedElement = nil
        observedPid = 0
        lastWindowTitle = ""
        lastFocusedSignature = ""
    }

    func attach(to app: NSRunningApplication) {
        guard let pid = Optional(app.processIdentifier), pid > 0 else { return }
        if pid == observedPid { return }
        stop()

        var newObserver: AXObserver?
        let selfPtr = Unmanaged.passUnretained(self).toOpaque()
        let callback: AXObserverCallback = { _, element, notification, refcon in
            guard let refcon else { return }
            let capture = Unmanaged<AXEventCapture>.fromOpaque(refcon).takeUnretainedValue()
            capture.handle(notification: notification as String, element: element)
        }
        let createStatus = AXObserverCreate(pid, callback, &newObserver)
        guard createStatus == .success, let newObserver else {
            NSLog("MindLoomAgent: AXObserverCreate failed: %d", createStatus.rawValue)
            return
        }

        let appElement = AXUIElementCreateApplication(pid)
        AXObserverAddNotification(newObserver, appElement, kAXFocusedUIElementChangedNotification as CFString, selfPtr)
        AXObserverAddNotification(newObserver, appElement, kAXTitleChangedNotification as CFString, selfPtr)
        AXObserverAddNotification(newObserver, appElement, axSelectedMenuItemChangedNotification, selfPtr)
        CFRunLoopAddSource(CFRunLoopGetMain(), AXObserverGetRunLoopSource(newObserver), .defaultMode)

        observer = newObserver
        observedElement = appElement
        observedPid = pid

        emitFocus(app: app)
        if let windowTitle = copyWindowTitle(appElement: appElement) {
            lastWindowTitle = Redaction.sanitizeWindowTitle(windowTitle)
            emitNavigate(app: app, windowTitle: lastWindowTitle)
        }
        if let focused = copyFocusedElement(appElement: appElement) {
            emitFocusedElement(app: app, element: focused)
        }
    }

    private func handle(notification: String, element: AXUIElement) {
        guard let app = NSRunningApplication(processIdentifier: observedPid) else { return }
        let focusedChanged = kAXFocusedUIElementChangedNotification as String
        let titleChanged = kAXTitleChangedNotification as String
        let menuChanged = axSelectedMenuItemChangedNotification as String
        if notification == focusedChanged {
            emitFocusedElement(app: app, element: element)
            if let focused = copyFocusedElement(appElement: AXUIElementCreateApplication(observedPid)) {
                emitFocusedElement(app: app, element: focused)
            }
        } else if notification == titleChanged {
            let title = Redaction.sanitizeWindowTitle(copyStringAttribute(element, kAXTitleAttribute as String) ?? "")
            if !title.isEmpty, title != lastWindowTitle {
                lastWindowTitle = title
                emitNavigate(app: app, windowTitle: title)
            }
        } else if notification == menuChanged {
            emitMenu(app: app, element: element)
        }
    }

    private func emitFocus(app: NSRunningApplication) {
        let event = LocalInteractionEvent(
            ts: Date(),
            bundleId: app.bundleIdentifier ?? "unknown",
            appName: app.localizedName ?? app.bundleIdentifier ?? "App",
            windowTitle: lastWindowTitle,
            action: .focus,
            control: ControlIdentity(role: "AXApplication", identifier: app.bundleIdentifier, title: app.localizedName, path: []),
            durationMs: nil
        )
        delegate?.axCaptureDidEmit(event)
    }

    private func emitNavigate(app: NSRunningApplication, windowTitle: String) {
        let event = LocalInteractionEvent(
            ts: Date(),
            bundleId: app.bundleIdentifier ?? "unknown",
            appName: app.localizedName ?? app.bundleIdentifier ?? "App",
            windowTitle: windowTitle,
            action: .navigate,
            control: ControlIdentity(role: "AXWindow", identifier: nil, title: windowTitle, path: [windowTitle]),
            durationMs: nil
        )
        delegate?.axCaptureDidEmit(event)
    }

    private func emitMenu(app: NSRunningApplication, element: AXUIElement) {
        let role = copyStringAttribute(element, kAXRoleAttribute as String) ?? "AXMenuItem"
        let title = Redaction.sanitizeTitle(copyStringAttribute(element, kAXTitleAttribute as String))
        if Redaction.isSensitiveRole(role) { return }
        let event = LocalInteractionEvent(
            ts: Date(),
            bundleId: app.bundleIdentifier ?? "unknown",
            appName: app.localizedName ?? app.bundleIdentifier ?? "App",
            windowTitle: lastWindowTitle,
            action: .menu,
            control: ControlIdentity(role: role, identifier: copyStringAttribute(element, kAXIdentifierAttribute as String), title: title, path: buildPath(element)),
            durationMs: nil
        )
        delegate?.axCaptureDidEmit(event)
    }

    private func emitFocusedElement(app: NSRunningApplication, element: AXUIElement) {
        let role = copyStringAttribute(element, kAXRoleAttribute as String) ?? "AXUnknown"
        // Never read AXValue — content stays on device / never captured.
        let rawTitle = copyStringAttribute(element, kAXTitleAttribute as String)
            ?? copyStringAttribute(element, kAXDescriptionAttribute as String)
            ?? copyStringAttribute(element, kAXHelpAttribute as String)
        let title = Redaction.sanitizeTitle(rawTitle)
        if Redaction.isSensitiveRole(role) {
            // Record that a secure field was focused, without any label/value.
            let event = LocalInteractionEvent(
                ts: Date(),
                bundleId: app.bundleIdentifier ?? "unknown",
                appName: app.localizedName ?? app.bundleIdentifier ?? "App",
                windowTitle: lastWindowTitle,
                action: .fieldFocus,
                control: ControlIdentity(role: role, identifier: nil, title: "[redacted]", path: []),
                durationMs: nil
            )
            emitIfNew(event)
            return
        }

        let action: InteractionAction
        if Redaction.isTextRole(role) {
            action = .fieldFocus
        } else if role == "AXButton" || role == "AXCheckBox" || role == "AXRadioButton" || role == "AXLink" {
            action = .click
        } else {
            action = .focus
        }

        let control = ControlIdentity(
            role: role,
            identifier: copyStringAttribute(element, kAXIdentifierAttribute as String),
            title: title,
            path: buildPath(element)
        )
        let event = LocalInteractionEvent(
            ts: Date(),
            bundleId: app.bundleIdentifier ?? "unknown",
            appName: app.localizedName ?? app.bundleIdentifier ?? "App",
            windowTitle: lastWindowTitle,
            action: action,
            control: control,
            durationMs: nil
        )
        emitIfNew(event)
    }

    private func emitIfNew(_ event: LocalInteractionEvent) {
        let signature = "\(event.action.rawValue)|\(event.control.role)|\(event.control.title ?? "")|\(event.control.identifier ?? "")|\(event.windowTitle)"
        guard signature != lastFocusedSignature else { return }
        lastFocusedSignature = signature
        delegate?.axCaptureDidEmit(event)
    }

    private func copyFocusedElement(appElement: AXUIElement) -> AXUIElement? {
        var value: AnyObject?
        let status = AXUIElementCopyAttributeValue(appElement, kAXFocusedUIElementAttribute as CFString, &value)
        guard status == .success, let value else { return nil }
        return (value as! AXUIElement)
    }

    private func copyWindowTitle(appElement: AXUIElement) -> String? {
        var value: AnyObject?
        let status = AXUIElementCopyAttributeValue(appElement, kAXFocusedWindowAttribute as CFString, &value)
        guard status == .success, let value else { return nil }
        let window = value as! AXUIElement
        return copyStringAttribute(window, kAXTitleAttribute as String)
    }

    private func copyStringAttribute(_ element: AXUIElement, _ attribute: String) -> String? {
        var value: AnyObject?
        let status = AXUIElementCopyAttributeValue(element, attribute as CFString, &value)
        guard status == .success, let value else { return nil }
        return value as? String
    }

    private func buildPath(_ element: AXUIElement) -> [String] {
        var path: [String] = []
        var current: AXUIElement? = element
        for _ in 0..<5 {
            guard let node = current else { break }
            let role = copyStringAttribute(node, kAXRoleAttribute as String) ?? "AXUnknown"
            let title = Redaction.sanitizeTitle(
                copyStringAttribute(node, kAXTitleAttribute as String)
                    ?? copyStringAttribute(node, kAXDescriptionAttribute as String)
            )
            path.insert(title ?? role, at: 0)
            var parent: AnyObject?
            let status = AXUIElementCopyAttributeValue(node, kAXParentAttribute as CFString, &parent)
            guard status == .success, let parent else { break }
            current = (parent as! AXUIElement)
        }
        return path
    }
}
