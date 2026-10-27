import AppKit
import Foundation
import GuideCore

/// Walks skill runs for the signed-in user, one at a time. Polls for pending runs,
/// claims one, and follows its steps in order: native anchors are performed here and
/// confirmed by observing them; browser anchors are left to the Chrome extension;
/// anything else waits for the person's Next. A run never skips or substitutes a step.
@MainActor
final class SkillRunWalker {
    static let pendingPollSeconds = 3.0
    static let statusPollSeconds = 1.5
    /// Lets AXEventCapture attach to the app the step acts in.
    static let activationSettleSeconds = 0.5
    static let confirmTimeoutSeconds = 4.0
    /// Longer than the extension's idle poll plus a page load.
    static let browserTimeoutSeconds = 30.0
    /// A run nobody picked up for this long is not started (it may be long forgotten).
    static let maxPendingAgeSeconds = 10.0 * 60

    private var config: AgentConfig
    private let engine: CaptureEngine
    private let guides: GuideController
    private let performer = StepPerformer()
    private var pollTask: Task<Void, Never>?
    private var walkTask: Task<Void, Never>?
    private var napTask: Task<Void, Never>?

    private var activeRunId: String?
    private var stopRequested = false
    /// Step index the overlay's Next applies to (only while the run is waiting).
    private var waitingIndex: Int?
    private var shown: (index: Int, phase: RunStepPhase)?

    private var expected: StepAnchor?
    private var expectedSeen = false
    private var confirmationWaiter: CheckedContinuation<Bool, Never>?
    private var confirmationToken = UUID()

    init(engine: CaptureEngine, guides: GuideController, config: AgentConfig) {
        self.engine = engine
        self.guides = guides
        self.config = config
        engine.onRunEvent = { [weak self] event in
            Task { @MainActor in self?.observe(event) }
        }
        guides.onRunNext = { [weak self] in
            Task { @MainActor in self?.next() }
        }
        guides.onRunStop = { [weak self] in
            Task { @MainActor in self?.stop() }
        }
        guides.onRunClose = { [weak guides] in
            guides?.endRun(hideOverlay: true)
        }
        pollTask = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                await self.pollPending()
                try? await Task.sleep(nanoseconds: UInt64(Self.pendingPollSeconds * 1_000_000_000))
            }
        }
    }

    deinit {
        pollTask?.cancel()
        walkTask?.cancel()
    }

    func reloadConfig(_ config: AgentConfig) {
        let accountChanged = config.accessToken != self.config.accessToken || config.apiBase != self.config.apiBase
        self.config = config
        if accountChanged, walkTask != nil {
            walkTask?.cancel()
            resolveConfirmation(false)
            wake()
        }
    }

    // MARK: - Picking up runs

    private func pollPending() async {
        guard config.isSignedIn, activeRunId == nil else { return }
        let client = APIClient(config: config)
        guard let pending = try? await client.listSkillRuns(status: .pending) else { return }
        let cutoff = Date().addingTimeInterval(-Self.maxPendingAgeSeconds)
        // Oldest fresh run first; the list is newest first.
        guard activeRunId == nil,
              let run = pending.last(where: { ($0.createdDate ?? .distantPast) > cutoff }),
              let claimed = try? await client.claimSkillRun(run.runId) else { return }
        activeRunId = claimed.runId
        stopRequested = false
        shown = nil
        walkTask = Task { [weak self] in
            await self?.walk(claimed.runId, client: client)
        }
    }

    // MARK: - The walk

    private func walk(_ runId: String, client: APIClient) async {
        guides.beginRun()
        engine.runObservationEnabled = true
        defer {
            engine.runObservationEnabled = false
            engine.runTargetApp = nil
            activeRunId = nil
            waitingIndex = nil
            walkTask = nil
        }

        var title = "Skill run"
        var performed: [Int: APIClient.RunOutcome] = [:]
        var browserStep: (index: Int, since: Date)?
        var fetchFailures = 0

        while !Task.isCancelled {
            let run: SkillRun
            do {
                run = try await client.getSkillRun(runId)
                fetchFailures = 0
            } catch {
                fetchFailures += 1
                if fetchFailures >= 20 {
                    finish(title: title, status: .failed, message: "Lost contact with MindLoom. The run is still open on the web page.")
                    return
                }
                await nap(Self.statusPollSeconds)
                continue
            }
            if run.status.isFinished {
                finish(title: run.guide?.title ?? title, status: run.status, message: run.error)
                return
            }
            if stopRequested {
                // The overlay's stop didn't reach the server yet; keep asking, act on nothing.
                _ = try? await client.stopSkillRun(runId)
                await nap(Self.statusPollSeconds)
                continue
            }
            guard let guide = run.guide else {
                // The skill can no longer run; the server records why on this report.
                _ = try? await client.advanceSkillRun(
                    runId, stepIndex: run.stepIndex, outcome: .failed("This Skill File can no longer run.")
                )
                await nap(Self.statusPollSeconds)
                continue
            }
            title = guide.title
            let index = run.stepIndex
            guard run.status == .running || run.status == .waiting, guide.steps.indices.contains(index) else {
                await nap(Self.statusPollSeconds)
                continue
            }

            if run.status == .waiting {
                waitingIndex = index
                show(guide, index, .waiting(run.waitReason ?? .noAnchor, detail: run.error))
                await nap(Self.statusPollSeconds)
                continue
            }
            waitingIndex = nil

            let report: APIClient.RunOutcome
            switch RunDecision.decide(guide.steps[index]) {
            case .performAccessibility(let anchor):
                if let previous = performed[index] {
                    report = previous  // never perform a step twice; only re-send its report
                } else {
                    show(guide, index, .performing)
                    guard let outcome = await perform(anchor, index: index, runId: runId, client: client) else {
                        await nap(Self.statusPollSeconds)  // stopped or moved before acting
                        continue
                    }
                    performed[index] = outcome
                    report = outcome
                }
            case .performBrowser:
                show(guide, index, .inBrowser)
                if browserStep?.index != index {
                    browserStep = (index, Date())
                }
                guard let since = browserStep?.since,
                      Date().timeIntervalSince(since) > Self.browserTimeoutSeconds else {
                    await nap(Self.statusPollSeconds)
                    continue
                }
                report = .waiting(.agentOffline, detail: nil)
            case .wait(let reason):
                report = .waiting(reason, detail: nil)
            }

            if (try? await client.advanceSkillRun(runId, stepIndex: index, outcome: report)) == nil {
                // Stale step (409) or offline: refetch after a pause, never re-perform.
                await nap(Self.statusPollSeconds)
            }
        }
        guides.endRun(hideOverlay: true)
    }

    /// Performs one Accessibility step. Returns nil when the run stopped or moved before
    /// anything was pressed.
    private func perform(
        _ anchor: StepAnchor, index: Int, runId: String, client: APIClient
    ) async -> APIClient.RunOutcome? {
        guard AccessibilityPermission.isTrusted else {
            return .waiting(.actionFailed, detail: "Loom Capture needs Accessibility permission to do this step.")
        }
        guard engine.allowlistBundleIds.contains(anchor.bundleId) else {
            return .waiting(.actionFailed, detail: "\(anchor.bundleId) isn't on Loom Capture's allowlist, so Loom won't act in it.")
        }
        guard let app = StepPerformer.runningApp(for: anchor.bundleId) else {
            return .waiting(.actionFailed, detail: "\(anchor.bundleId) isn't running.")
        }

        // Navigation is confirmed by the window coming forward, which activation can do.
        if anchor.action == .navigate { expect(anchor) }
        engine.runTargetApp = app
        app.activate(options: [])
        await nap(Self.activationSettleSeconds, wakeable: false)

        let performer = self.performer
        let pid = app.processIdentifier
        let appName = app.localizedName ?? anchor.bundleId
        let located = await Task.detached { performer.locate(anchor, pid: pid, appName: appName) }.value
        let element: AXUIElement
        switch located {
        case .element(let found):
            element = found
        case .needsValue:
            clearExpectation()
            return .waiting(.needsValue, detail: nil)
        case .failed(let message):
            clearExpectation()
            return .waiting(.actionFailed, detail: message)
        }

        // The search can take a moment; re-check the run before pressing anything.
        let current = try? await client.getSkillRun(runId)
        guard !stopRequested, current?.status == .running, current?.stepIndex == index else {
            clearExpectation()
            return nil
        }
        if anchor.action != .navigate { expect(anchor) }
        // AXPress can block while the app reacts (a modal sheet, say); keep the overlay live.
        let acted = await Task.detached { performer.act(anchor, on: element, appName: appName) }.value
        if case .failed(let message) = acted {
            clearExpectation()
            return .waiting(.actionFailed, detail: message)
        }
        if await awaitConfirmation() {
            return .done
        }
        let name = anchor.title ?? anchor.windowTitle ?? anchor.identifier ?? "this control"
        return .waiting(
            .actionFailed,
            detail: "Loom acted on “\(name)” but didn't see \(appName) respond within \(Int(Self.confirmTimeoutSeconds)) seconds."
        )
    }

    // MARK: - Confirmation

    private func expect(_ anchor: StepAnchor) {
        expected = anchor
        expectedSeen = false
    }

    private func clearExpectation() {
        expected = nil
        expectedSeen = false
    }

    private func observe(_ event: LocalInteractionEvent) {
        guard let expected, let observed = Self.observedAnchor(event),
              GuideMatcher.matches(expected: expected, observed: observed) else { return }
        expectedSeen = true
        resolveConfirmation(true)
    }

    /// Guide matching skips app/control focus events; a run's focus step needs them.
    private static func observedAnchor(_ event: LocalInteractionEvent) -> StepAnchor? {
        if let anchor = TaskAggregator.anchor(for: event) { return anchor }
        guard event.action == .focus else { return nil }
        return StepAnchor(
            bundleId: event.bundleId,
            action: .focus,
            role: event.control.role,
            identifier: event.control.identifier,
            title: event.control.title
        )
    }

    private func awaitConfirmation() async -> Bool {
        defer { clearExpectation() }
        if expectedSeen { return true }
        let token = UUID()
        confirmationToken = token
        return await withCheckedContinuation { continuation in
            confirmationWaiter = continuation
            Task { [weak self] in
                try? await Task.sleep(nanoseconds: UInt64(Self.confirmTimeoutSeconds * 1_000_000_000))
                guard let self, self.confirmationToken == token else { return }
                self.resolveConfirmation(false)
            }
        }
    }

    private func resolveConfirmation(_ confirmed: Bool) {
        guard let waiter = confirmationWaiter else { return }
        confirmationWaiter = nil
        confirmationToken = UUID()
        waiter.resume(returning: confirmed)
    }

    // MARK: - Overlay

    private func show(_ guide: Guide, _ index: Int, _ phase: RunStepPhase) {
        if let shown, shown.index == index, shown.phase == phase { return }
        shown = (index, phase)
        guides.showRunStep(guide, index: index, phase: phase)
    }

    private func finish(title: String, status: SkillRunStatus, message: String?) {
        shown = nil
        guides.showRunFinished(title: title, status: status, message: message)
        guides.endRun(hideOverlay: false)
    }

    private func next() {
        guard let runId = activeRunId, let index = waitingIndex else { return }
        waitingIndex = nil
        let client = APIClient(config: config)
        Task { [weak self] in
            _ = try? await client.advanceSkillRun(runId, stepIndex: index, outcome: .done)
            self?.wake()
        }
    }

    private func stop() {
        guard let runId = activeRunId else { return }
        stopRequested = true
        waitingIndex = nil
        resolveConfirmation(false)
        let client = APIClient(config: config)
        Task { [weak self] in
            _ = try? await client.stopSkillRun(runId)
            self?.wake()
        }
    }

    // MARK: - Pacing

    private func nap(_ seconds: Double, wakeable: Bool = true) async {
        let task = Task<Void, Never> { try? await Task.sleep(nanoseconds: UInt64(seconds * 1_000_000_000)) }
        if wakeable { napTask = task }
        await task.value
        if wakeable { napTask = nil }
    }

    private func wake() {
        napTask?.cancel()
    }
}
