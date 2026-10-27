import AppKit
import GuideCore

/// Offers guides when an allowlisted app with approved skills comes forward, and runs
/// one walkthrough at a time. Auto-advance only applies to allowlisted apps. While a
/// skill run is active the overlay belongs to the run.
final class GuideController {
    static let snoozeInterval: TimeInterval = 30 * 60

    let library: GuideLibrary
    private let engine: CaptureEngine
    private let overlay = GuideOverlay()
    private(set) var session: GuideSession?
    private var snoozedUntil: [String: Date] = [:]
    private var suggestedBundleId: String?
    private var activationObserver: NSObjectProtocol?
    private(set) var isRunActive = false

    var onChange: (() -> Void)?
    var onRunNext: (() -> Void)?
    var onRunStop: (() -> Void)?
    var onRunClose: (() -> Void)?

    init(engine: CaptureEngine, library: GuideLibrary) {
        self.engine = engine
        self.library = library

        overlay.onStart = { [weak self] guide in self?.start(guide) }
        overlay.onSnooze = { [weak self] in self?.snoozeSuggestion() }
        overlay.onNext = { [weak self] in self?.next() }
        overlay.onBack = { [weak self] in self?.back() }
        overlay.onExit = { [weak self] in self?.exit() }
        overlay.onRunNext = { [weak self] in self?.onRunNext?() }
        overlay.onRunStop = { [weak self] in self?.onRunStop?() }
        overlay.onRunClose = { [weak self] in self?.onRunClose?() }

        engine.onGuideEvent = { [weak self] event in self?.handle(event) }
        activationObserver = NSWorkspace.shared.notificationCenter.addObserver(
            forName: NSWorkspace.didActivateApplicationNotification,
            object: nil,
            queue: .main
        ) { [weak self] notification in
            guard let app = notification.userInfo?[NSWorkspace.applicationUserInfoKey] as? NSRunningApplication else {
                return
            }
            self?.appActivated(app)
        }
    }

    deinit {
        if let activationObserver {
            NSWorkspace.shared.notificationCenter.removeObserver(activationObserver)
        }
    }

    var isRunning: Bool { session != nil }

    func canAutoAdvance(_ guide: Guide) -> Bool {
        !engine.allowlistBundleIds.isDisjoint(with: guide.bundleIds) && guide.autoStepCount > 0
    }

    func start(_ guide: Guide) {
        guard !guide.steps.isEmpty, !isRunActive else { return }
        let auto = canAutoAdvance(guide)
        session = GuideSession(guide: guide, autoAdvance: auto)
        suggestedBundleId = nil
        engine.guideObservationEnabled = auto && AccessibilityPermission.isTrusted
        render()
        onChange?()
    }

    func exit() {
        session = nil
        engine.guideObservationEnabled = false
        overlay.hide()
        onChange?()
    }

    func signedOut() {
        exit()
        snoozedUntil.removeAll()
    }

    // MARK: - Skill runs

    func beginRun() {
        if session != nil { exit() }
        isRunActive = true
        suggestedBundleId = nil
    }

    func showRunStep(_ guide: Guide, index: Int, phase: RunStepPhase) {
        overlay.showRunStep(guide, index: index, phase: phase)
    }

    func showRunFinished(title: String, status: SkillRunStatus, message: String?) {
        overlay.showRunFinished(title: title, status: status, message: message)
    }

    func endRun(hideOverlay: Bool) {
        isRunActive = false
        if hideOverlay { overlay.hide() }
    }

    private func next() {
        guard var current = session else { return }
        current.next()
        session = current
        render()
    }

    private func back() {
        guard var current = session else { return }
        current.back()
        session = current
        render()
    }

    private func handle(_ event: LocalInteractionEvent) {
        guard var current = session, let observed = TaskAggregator.anchor(for: event) else { return }
        if current.observe(observed) {
            session = current
            render()
        }
    }

    private func render() {
        guard let session else { return }
        if session.isFinished {
            engine.guideObservationEnabled = false
            overlay.showComplete(session.guide)
            return
        }
        overlay.showStep(session, manualHint: manualHint(for: session))
    }

    private func manualHint(for session: GuideSession) -> String? {
        guard !session.autoAdvance else { return nil }
        if session.guide.autoStepCount == 0 { return nil }
        if !AccessibilityPermission.isTrusted {
            return "Grant Accessibility to Loom Capture for auto-advance."
        }
        let app = session.guide.application.isEmpty ? "this app" : session.guide.application
        return "Allowlist \(app) in Setup for auto-advance."
    }

    // MARK: - Suggestions

    private func appActivated(_ app: NSRunningApplication) {
        guard session == nil, !isRunActive, engine.config.isSignedIn else { return }
        guard let bundleId = app.bundleIdentifier,
              app.processIdentifier != ProcessInfo.processInfo.processIdentifier else { return }
        guard engine.allowlistBundleIds.contains(bundleId) else {
            hideSuggestionIfShowing()
            return
        }
        let guides = library.guides(for: bundleId)
        guard !guides.isEmpty else {
            hideSuggestionIfShowing()
            return
        }
        if let until = snoozedUntil[bundleId], until > Date() { return }
        suggestedBundleId = bundleId
        overlay.showSuggestion(appName: app.localizedName ?? bundleId, guides: guides)
    }

    private func snoozeSuggestion() {
        if let bundleId = suggestedBundleId {
            snoozedUntil[bundleId] = Date().addingTimeInterval(Self.snoozeInterval)
        }
        suggestedBundleId = nil
        overlay.hide()
    }

    private func hideSuggestionIfShowing() {
        guard session == nil, !isRunActive, suggestedBundleId != nil else { return }
        suggestedBundleId = nil
        overlay.hide()
    }
}
