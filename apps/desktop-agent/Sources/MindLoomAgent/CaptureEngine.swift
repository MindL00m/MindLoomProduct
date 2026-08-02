import AppKit
import Foundation

final class CaptureEngine: NSObject, AXEventCaptureDelegate {
    private(set) var status: CaptureStatus = .idle
    private(set) var isPaused = false
    private(set) var sessionId: String?
    private(set) var sessionStartedAt: Date?

    var onStatusChange: ((CaptureStatus) -> Void)?

    private var config: AgentConfig
    private let axCapture = AXEventCapture()
    private var aggregator: TaskAggregator
    private var workspaceObserver: NSObjectProtocol?
    private var permissionTimer: Timer?

    init(config: AgentConfig) {
        self.config = config
        self.aggregator = TaskAggregator(idleGapSeconds: config.idleGapSeconds)
        super.init()
        axCapture.delegate = self
    }

    func reloadConfig(_ config: AgentConfig) {
        self.config = config
        aggregator = TaskAggregator(idleGapSeconds: config.idleGapSeconds)
    }

    var allowlistBundleIds: Set<String> {
        Set(config.allowlist.map(\.bundleId))
    }

    func startSession() {
        guard AccessibilityPermission.isTrusted else {
            setStatus(.needsPermission)
            AccessibilityPermission.promptIfNeeded()
            return
        }
        isPaused = false
        sessionId = "session-\(UUID().uuidString)"
        sessionStartedAt = Date()
        aggregator.reset()
        beginWorkspaceObservation()
        attachToFrontmostAllowlistedApp()
        setStatus(.capturing)
    }

    func pause() {
        guard sessionId != nil else { return }
        isPaused = true
        axCapture.stop()
        setStatus(.paused)
    }

    func resume() {
        guard sessionId != nil else { return }
        guard AccessibilityPermission.isTrusted else {
            setStatus(.needsPermission)
            return
        }
        isPaused = false
        attachToFrontmostAllowlistedApp()
        setStatus(.capturing)
    }

    func endSessionAndUpload(analyze: Bool, note: String = "") async throws {
        axCapture.stop()
        endWorkspaceObservation()
        aggregator.endSession()
        let tasks = aggregator.tasks
        guard let sessionId, let started = sessionStartedAt else {
            setStatus(.idle)
            return
        }
        defer {
            self.sessionId = nil
            self.sessionStartedAt = nil
            self.isPaused = false
            self.aggregator.reset()
            setStatus(.idle)
        }
        guard !tasks.isEmpty else {
            throw NSError(
                domain: "MindLoomAgent",
                code: 1,
                userInfo: [NSLocalizedDescriptionKey: "No allowlisted activity was captured in this session."]
            )
        }
        let payload = ActivitySessionPayload(
            sessionId: sessionId,
            orgId: config.orgId,
            userId: config.userId,
            source: "desktop_ax",
            startedAt: started,
            endedAt: Date(),
            tasks: tasks,
            note: note
        )
        let client = APIClient(config: config)
        try await client.uploadActivitySession(payload)
        if analyze {
            _ = try await client.analyzeActivitySession(sessionId: sessionId)
        }
    }

    func axCaptureDidEmit(_ event: LocalInteractionEvent) {
        guard sessionId != nil, !isPaused, status == .capturing else { return }
        guard allowlistBundleIds.contains(event.bundleId) else { return }
        AgentConfigStore.appendLocalEvent(event)
        aggregator.ingest(event)
    }

    private func beginWorkspaceObservation() {
        endWorkspaceObservation()
        workspaceObserver = NSWorkspace.shared.notificationCenter.addObserver(
            forName: NSWorkspace.didActivateApplicationNotification,
            object: nil,
            queue: .main
        ) { [weak self] notification in
            self?.handleActivation(notification)
        }
        permissionTimer = Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { [weak self] _ in
            self?.refreshPermissionStatus()
        }
    }

    private func endWorkspaceObservation() {
        if let workspaceObserver {
            NSWorkspace.shared.notificationCenter.removeObserver(workspaceObserver)
            self.workspaceObserver = nil
        }
        permissionTimer?.invalidate()
        permissionTimer = nil
        axCapture.stop()
    }

    private func handleActivation(_ notification: Notification) {
        guard sessionId != nil, !isPaused else { return }
        guard AccessibilityPermission.isTrusted else {
            axCapture.stop()
            setStatus(.needsPermission)
            return
        }
        guard let app = notification.userInfo?[NSWorkspace.applicationUserInfoKey] as? NSRunningApplication,
              let bundleId = app.bundleIdentifier else {
            axCapture.stop()
            return
        }
        if allowlistBundleIds.contains(bundleId) {
            axCapture.attach(to: app)
            if status != .capturing { setStatus(.capturing) }
        } else {
            // Structurally invisible: tear down observers; do not emit events.
            axCapture.stop()
        }
    }

    private func attachToFrontmostAllowlistedApp() {
        guard let app = NSWorkspace.shared.frontmostApplication,
              let bundleId = app.bundleIdentifier,
              allowlistBundleIds.contains(bundleId) else {
            axCapture.stop()
            return
        }
        axCapture.attach(to: app)
    }

    private func refreshPermissionStatus() {
        guard sessionId != nil else { return }
        if !AccessibilityPermission.isTrusted {
            axCapture.stop()
            setStatus(.needsPermission)
        } else if isPaused {
            setStatus(.paused)
        } else if status == .needsPermission {
            attachToFrontmostAllowlistedApp()
            setStatus(.capturing)
        }
    }

    private func setStatus(_ status: CaptureStatus) {
        self.status = status
        onStatusChange?(status)
    }
}
