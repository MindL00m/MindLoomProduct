import Foundation

final class TaskAggregator {
    private let idleGap: TimeInterval
    private var openEvents: [LocalInteractionEvent] = []
    private var completedTasks: [TaskSummary] = []
    private var fieldFocusStartedAt: Date?
    private var fieldFocusControl: ControlIdentity?
    private var fieldFocusApp: String = ""

    init(idleGapSeconds: TimeInterval) {
        self.idleGap = idleGapSeconds
    }

    var tasks: [TaskSummary] { completedTasks }

    func reset() {
        openEvents.removeAll()
        completedTasks.removeAll()
        fieldFocusStartedAt = nil
        fieldFocusControl = nil
        fieldFocusApp = ""
    }

    func ingest(_ event: LocalInteractionEvent) {
        if let last = openEvents.last,
           event.ts.timeIntervalSince(last.ts) > idleGap {
            closeCurrentTask(endedAt: last.ts)
        }

        if let last = openEvents.last,
           last.bundleId != event.bundleId,
           !openEvents.isEmpty {
            // App switch within allowlist still continues the same task segment
            // unless idle gap already closed it.
        }

        switch event.action {
        case .fieldFocus:
            flushFieldBlur(at: event.ts)
            fieldFocusStartedAt = event.ts
            fieldFocusControl = event.control
            fieldFocusApp = event.appName
        case .fieldBlur:
            flushFieldBlur(at: event.ts, control: event.control, appName: event.appName)
        default:
            break
        }

        openEvents.append(event)
    }

    func endSession(at date: Date = Date()) {
        flushFieldBlur(at: date)
        closeCurrentTask(endedAt: date)
    }

    private func flushFieldBlur(at date: Date, control: ControlIdentity? = nil, appName: String? = nil) {
        guard let started = fieldFocusStartedAt else { return }
        let ctrl = control ?? fieldFocusControl ?? ControlIdentity(role: "AXUnknown", identifier: nil, title: nil, path: [])
        let duration = max(0, Int(date.timeIntervalSince(started) * 1000))
        let blur = LocalInteractionEvent(
            ts: date,
            bundleId: openEvents.last?.bundleId ?? "",
            appName: appName ?? fieldFocusApp,
            windowTitle: openEvents.last?.windowTitle ?? "",
            action: .fieldBlur,
            control: ctrl,
            durationMs: duration
        )
        openEvents.append(blur)
        fieldFocusStartedAt = nil
        fieldFocusControl = nil
        fieldFocusApp = ""
    }

    private func closeCurrentTask(endedAt: Date) {
        guard let first = openEvents.first else { return }
        let events = openEvents
        openEvents.removeAll()

        var appCounts: [String: Int] = [:]
        var stepHints: [String] = []
        var fieldInteractions: [FieldInteractionSummary] = []
        var lastHint: String?

        for event in events {
            appCounts[event.appName, default: 0] += 1
            let hint: String
            switch event.action {
            case .focus:
                hint = "Focus \(event.appName)"
            case .navigate:
                let window = event.windowTitle.isEmpty ? "(untitled)" : event.windowTitle
                hint = "Window: \(window) [\(event.appName)]"
            case .click:
                let title = event.control.title ?? event.control.role
                hint = "Click \(title) [\(event.appName)]"
            case .menu:
                let title = event.control.title ?? "menu"
                hint = "Menu \(title) [\(event.appName)]"
            case .fieldFocus:
                let label = event.control.title ?? event.control.role
                hint = "Focus field \(label) [\(event.appName)]"
            case .fieldBlur:
                let label = event.control.title ?? event.control.role
                let ms = event.durationMs ?? 0
                fieldInteractions.append(
                    FieldInteractionSummary(role: event.control.role, label: label, durationMs: ms)
                )
                hint = "Leave field \(label) (\(ms)ms)"
            }
            if hint != lastHint {
                stepHints.append(hint)
                lastHint = hint
            }
        }

        // Cap step hints to keep uploads small.
        if stepHints.count > 40 {
            stepHints = Array(stepHints.prefix(20)) + ["…"] + Array(stepHints.suffix(19))
        }

        let primaryApp = appCounts.max(by: { $0.value < $1.value })?.key ?? first.appName
        let activeMs = max(0, Int(endedAt.timeIntervalSince(first.ts) * 1000))
        let summary = TaskSummary(
            taskId: UUID().uuidString,
            startedAt: first.ts,
            endedAt: endedAt,
            primaryApp: primaryApp,
            apps: appCounts.keys.sorted(),
            stepHints: stepHints,
            fieldInteractions: fieldInteractions,
            stats: TaskStats(eventCount: events.count, activeMs: activeMs)
        )
        completedTasks.append(summary)
    }
}
