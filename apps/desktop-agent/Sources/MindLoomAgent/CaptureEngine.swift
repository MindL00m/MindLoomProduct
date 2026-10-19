import AppKit
import Foundation

final class CaptureEngine: NSObject, AXEventCaptureDelegate {
    private(set) var status: CaptureStatus = .idle
    private(set) var isPaused = false
    private(set) var sessionId: String?
    private(set) var sessionStartedAt: Date?
    /// Events ingested into the current session aggregator (not lifetime disk log).
    private(set) var sessionEventCount = 0
    /// True from End until the upload and Skill File draft finish.
    private(set) var isFinishing = false
    /// Last non-agent regular app the user activated (for allowlist when our panel is focused).
    private(set) var lastUserFacingApp: NSRunningApplication?

    var onStatusChange: ((CaptureStatus) -> Void)?
    /// Live events for an active guide. Never logged to disk or uploaded.
    var onGuideEvent: ((LocalInteractionEvent) -> Void)?
    /// An auto-advancing guide needs AX events even when no capture session is running.
    var guideObservationEnabled = false {
        didSet {
            guard guideObservationEnabled != oldValue else { return }
            refreshObserverAttachment()
        }
    }

    private(set) var config: AgentConfig
    private let axCapture = AXEventCapture()
    private var aggregator: TaskAggregator
    private var workspaceObserver: NSObjectProtocol?
    private var activationTracker: NSObjectProtocol?
    private var permissionTimer: Timer?

    init(config: AgentConfig) {
        self.config = config
        self.aggregator = TaskAggregator(idleGapSeconds: config.idleGapSeconds)
        super.init()
        axCapture.delegate = self
        beginActivationTracking()
        rememberFrontmostIfUserApp(NSWorkspace.shared.frontmostApplication)
    }

    deinit {
        if let activationTracker {
            NSWorkspace.shared.notificationCenter.removeObserver(activationTracker)
        }
    }

    func reloadConfig(_ config: AgentConfig) {
        self.config = config
        // Never wipe in-session captures when allowlist/sign-in updates config.
        if sessionId == nil {
            aggregator = TaskAggregator(idleGapSeconds: config.idleGapSeconds)
            sessionEventCount = 0
            refreshObserverAttachment()
        } else if !isPaused, status == .capturing || status == .needsPermission {
            attachToFrontmostAllowlistedApp()
            notifyStatus()
        }
    }

    var allowlistBundleIds: Set<String> {
        Set(config.allowlist.map(\.bundleId))
    }

    /// Best app to allowlist from the UI (avoids picking Loom Capture / wrong fallback).
    func preferredAllowlistCandidate() -> NSRunningApplication? {
        let selfPid = ProcessInfo.processInfo.processIdentifier
        if let front = NSWorkspace.shared.frontmostApplication,
           isUserFacingApp(front),
           front.processIdentifier != selfPid {
            return front
        }
        if let last = lastUserFacingApp,
           !last.isTerminated,
           last.processIdentifier != selfPid {
            return last
        }
        return nil
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
        sessionEventCount = 0
        aggregator.reset()
        beginWorkspaceObservation()
        attachToFrontmostAllowlistedApp()
        setStatus(.capturing)
    }

    func pause() {
        guard sessionId != nil else { return }
        isPaused = true
        refreshObserverAttachment()
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

    /// Claims the End action synchronously so repeat clicks are ignored before the upload task runs.
    func beginFinishing() -> Bool {
        guard sessionId != nil, !isFinishing else { return false }
        isFinishing = true
        notifyStatus()
        return true
    }

    func endSessionAndUpload(analyze: Bool, note: String = "") async throws {
        defer {
            DispatchQueue.main.async { [weak self] in
                self?.isFinishing = false
                self?.notifyStatus()
            }
        }
        axCapture.stop()
        endWorkspaceObservation()
        aggregator.endSession()
        let tasks = aggregator.tasks
        let eventCount = sessionEventCount
        guard let sessionId, let started = sessionStartedAt else {
            setStatus(.idle)
            return
        }
        defer {
            self.sessionId = nil
            self.sessionStartedAt = nil
            self.isPaused = false
            self.sessionEventCount = 0
            self.aggregator.reset()
            setStatus(.idle)
            DispatchQueue.main.async { [weak self] in self?.refreshObserverAttachment() }
        }
        guard !tasks.isEmpty else {
            let hint: String
            if config.allowlist.isEmpty {
                hint = "Allowlist is empty. Add the app you use, start a session, then work in that app."
            } else if eventCount == 0 {
                let names = config.allowlist.map(\.displayName).joined(separator: ", ")
                hint = "No events reached the session. Focus one of: \(names) while status is Capturing (Accessibility must be granted for Loom Capture)."
            } else {
                hint = "Events were cleared before upload. End the session without changing allowlist/sign-in mid-capture, then try again."
            }
            throw NSError(
                domain: "MindLoomAgent",
                code: 1,
                userInfo: [NSLocalizedDescriptionKey: "No allowlisted activity was captured in this session. \(hint)"]
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
        guard allowlistBundleIds.contains(event.bundleId) else { return }
        if guideObservationEnabled {
            onGuideEvent?(event)
        }
        guard sessionId != nil, !isPaused, status == .capturing else { return }
        AgentConfigStore.appendLocalEvent(event)
        aggregator.ingest(event)
        sessionEventCount += 1
        notifyStatus()
    }

    private func beginActivationTracking() {
        activationTracker = NSWorkspace.shared.notificationCenter.addObserver(
            forName: NSWorkspace.didActivateApplicationNotification,
            object: nil,
            queue: .main
        ) { [weak self] notification in
            guard let app = notification.userInfo?[NSWorkspace.applicationUserInfoKey] as? NSRunningApplication else {
                return
            }
            self?.rememberFrontmostIfUserApp(app)
            // Running capture sessions have their own activation observer.
            if self?.sessionId == nil || self?.isPaused == true {
                self?.refreshObserverAttachment()
            }
        }
    }

    /// Attach to the frontmost allowlisted app while capture or a guide needs events.
    private func refreshObserverAttachment() {
        let capturing = sessionId != nil && !isPaused
        guard AccessibilityPermission.isTrusted, capturing || guideObservationEnabled else {
            axCapture.stop()
            return
        }
        attachToFrontmostAllowlistedApp()
    }

    private func rememberFrontmostIfUserApp(_ app: NSRunningApplication?) {
        guard let app, isUserFacingApp(app) else { return }
        let selfPid = ProcessInfo.processInfo.processIdentifier
        guard app.processIdentifier != selfPid else { return }
        lastUserFacingApp = app
    }

    private func isUserFacingApp(_ app: NSRunningApplication) -> Bool {
        app.activationPolicy == .regular && app.bundleIdentifier != nil && !app.isTerminated
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
            notifyStatus()
            return
        }
        if allowlistBundleIds.contains(bundleId) {
            axCapture.attach(to: app)
            if status != .capturing { setStatus(.capturing) }
            else { notifyStatus() }
        } else {
            // Structurally invisible: tear down observers; do not emit events.
            axCapture.stop()
            notifyStatus()
        }
    }

    private func attachToFrontmostAllowlistedApp() {
        let selfPid = ProcessInfo.processInfo.processIdentifier
        let candidates: [NSRunningApplication] = [
            NSWorkspace.shared.frontmostApplication,
            lastUserFacingApp,
        ].compactMap { $0 }

        for app in candidates {
            guard app.processIdentifier != selfPid,
                  let bundleId = app.bundleIdentifier,
                  allowlistBundleIds.contains(bundleId),
                  !app.isTerminated else { continue }
            axCapture.attach(to: app)
            return
        }
        axCapture.stop()
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
        } else {
            notifyStatus()
        }
    }

    private func setStatus(_ status: CaptureStatus) {
        self.status = status
        onStatusChange?(status)
    }

    private func notifyStatus() {
        onStatusChange?(status)
    }
}
