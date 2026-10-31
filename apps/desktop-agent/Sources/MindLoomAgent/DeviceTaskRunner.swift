import AppKit
import DriverCore
import Foundation
import GuideCore

/// Performs device tasks: the parts of an automation run that happen in apps on this
/// Mac. Polls for pending tasks, claims one, then loops: read the app, send the screen
/// and the last result to the API, check the action it sends (ActionGate), perform it
/// in the background where macOS allows, and read the result back.
///
/// The person stays in control:
/// - the first task asks for consent; only the automation's allowed apps are touched;
/// - actions that can't be undone wait for Approve (here or on the web page);
/// - bringing an app forward is announced with a countdown and can be declined;
/// - typing or clicking in the app Loom is working in pauses the run until Resume;
/// - Stop on the overlay or ⌃⌥⌘. stops the task and its whole automation run.
@MainActor
final class DeviceTaskRunner {
    static let pendingPollSeconds = 3.0
    static let waitPollSeconds = 1.5
    static let settleSeconds = 0.6
    static let countdownSeconds = 3
    /// Later foreground moments in the same task get a shorter notice.
    static let repeatCountdownSeconds = 1
    static let maxPendingAgeSeconds = 30.0 * 60
    static let maxContactFailures = 10
    static let declinedSnoozeSeconds = 10.0 * 60
    static let consentKey = "deviceAutomationAllowed"

    private var config: AgentConfig
    private let guides: GuideController
    private let driver = LoomDriver()
    private var input = UserInputWatcher()
    private var pollTask: Task<Void, Never>?
    private var runTask: Task<Void, Never>?
    private var napTask: Task<Void, Never>?

    private(set) var activeTaskId: String?
    private var title = "Automation"
    private var stopRequested = false
    private var choice: AgentChoice?
    private var snoozed: [String: Date] = [:]
    private var noticeShown = false

    init(guides: GuideController, config: AgentConfig) {
        self.guides = guides
        self.config = config
        guides.onAgentChoice = { [weak self] choice in
            Task { @MainActor in self?.choose(choice) }
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
        runTask?.cancel()
    }

    var isActive: Bool { activeTaskId != nil }

    func reloadConfig(_ config: AgentConfig) {
        let accountChanged = config.accessToken != self.config.accessToken || config.apiBase != self.config.apiBase
        self.config = config
        if accountChanged, runTask != nil {
            stopRequested = true
            runTask?.cancel()
            wake()
        }
    }

    /// The stop hot key and the overlay's Stop.
    func stop() {
        guard activeTaskId != nil else { return }
        stopRequested = true
        wake()
    }

    private func choose(_ choice: AgentChoice) {
        if choice == .stop {
            stop()
            return
        }
        self.choice = choice
        wake()
    }

    private func takeChoice() -> AgentChoice? {
        defer { choice = nil }
        return choice
    }

    // MARK: - Picking up tasks

    private static var consented: Bool {
        get { UserDefaults.standard.bool(forKey: consentKey) }
        set { UserDefaults.standard.set(newValue, forKey: consentKey) }
    }

    private func pollPending() async {
        guard config.isSignedIn, activeTaskId == nil, !guides.isRunActive else { return }
        let client = APIClient(config: config)
        guard let pending = try? await client.listDeviceTasks(status: .pending) else { return }
        let cutoff = Date().addingTimeInterval(-Self.maxPendingAgeSeconds)
        // Oldest fresh task first; the list is newest first.
        guard activeTaskId == nil, !guides.isRunActive,
              let task = pending.last(where: {
                  ($0.createdDate ?? .distantPast) > cutoff && (snoozed[$0.taskId] ?? .distantPast) < Date()
              }) else { return }

        if !AccessibilityPermission.isTrusted {
            if !noticeShown {
                noticeShown = true
                guides.agentOverlay.showAgentNotice(
                    title: "Loom can't do this yet",
                    message: "An automation is waiting to use \(task.allowedApps.joined(separator: ", ")). Grant Accessibility to Loom Capture in Setup, then reopen it. Or do the steps yourself and click Done on the web page."
                )
            }
            return
        }
        activeTaskId = task.taskId  // hold the slot while asking for consent
        stopRequested = false
        if !Self.consented {
            guard await askConsent(apps: task.allowedApps) else {
                snoozed[task.taskId] = Date().addingTimeInterval(Self.declinedSnoozeSeconds)
                activeTaskId = nil
                guides.agentOverlay.hide()
                return
            }
            Self.consented = true
        }
        guard let claimed = try? await client.claimDeviceTask(task.taskId) else {
            activeTaskId = nil
            return
        }
        stopRequested = false
        choice = nil
        runTask = Task { [weak self] in
            await self?.run(claimed, client: client)
        }
    }

    private func askConsent(apps: [String]) async -> Bool {
        choice = nil
        guides.agentOverlay.showAgentConsent(apps: apps)
        let deadline = Date().addingTimeInterval(5 * 60)
        while Date() < deadline {
            switch takeChoice() {
            case .allow: return true
            case .notNow: return false
            default: break
            }
            if stopRequested { return false }
            await nap(1)
        }
        return false
    }

    // MARK: - The loop

    private func run(_ claimed: DeviceTask, client: APIClient) async {
        guides.beginRun()
        defer {
            activeTaskId = nil
            runTask = nil
            stopRequested = false
        }
        title = claimed.title.isEmpty ? "Automation" : claimed.title
        let taskId = claimed.taskId
        let allowed = claimed.allowedApps
        var app: NSRunningApplication? = preferredApp(allowed)
        var result: DeviceActionResult?
        var lastScreen: String?
        var contactFailures = 0
        var askedForeground = false
        var step = 0
        guides.agentOverlay.showAgentWorking(title: title, line: "Looking at \(app?.localizedName ?? "your apps")…", step: 0)

        while !Task.isCancelled {
            if stopRequested {
                _ = try? await client.stopDeviceTask(taskId)
                finish(.stopped, message: "Stopped. Nothing more was done.")
                return
            }
            if app?.isTerminated == true { app = nil }
            let snapshot = await read(app, allowed: allowed)
            let screen = Self.content(of: snapshot)
            if var last = result, let lastScreen {
                last.changed = screen != lastScreen
                result = last
            }

            let response: DeviceStepResponse
            do {
                response = try await client.stepDeviceTask(taskId, observation: snapshot.observation, result: result)
                contactFailures = 0
            } catch APIClientError.httpStatus(let code, _) where code == 404 {
                finish(.failed, message: "This task no longer exists.")
                return
            } catch {
                contactFailures += 1
                if contactFailures >= Self.maxContactFailures {
                    finish(.failed, message: "Lost contact with MindLoom. The run is still open on the web page.")
                    return
                }
                await nap(2)
                continue
            }
            result = nil
            lastScreen = screen

            switch response.status {
            case .completed, .failed, .stopped:
                let task = response.task
                finish(response.status, message: task?.summary ?? task?.error)
                return
            case .needsApproval, .needsPerson:
                if let task = response.task {
                    await waitForPerson(task, client: client)
                }
                continue
            case .pending:
                await nap(Self.waitPollSeconds)
                continue
            case .running:
                guard let action = response.action else {
                    await nap(1)
                    continue
                }
                step += 1
                guides.agentOverlay.showAgentWorking(title: title, line: action.description, step: step)
                result = await perform(action, snapshot: snapshot, app: &app, allowed: allowed, askedForeground: &askedForeground)
            }
        }
    }

    /// The outline without its header, which says whether the app is in front.
    private static func content(of snapshot: LoomDriver.Snapshot) -> String {
        snapshot.outline.entries.isEmpty ? snapshot.outline.text
            : snapshot.outline.text.split(separator: "\n", maxSplits: 1).dropFirst().joined()
    }

    /// An allowed app the person has in front, else any allowed app that's open.
    private func preferredApp(_ allowed: [String]) -> NSRunningApplication? {
        let running = LoomDriver.runningAllowedApps(allowed)
        let front = NSWorkspace.shared.frontmostApplication?.processIdentifier
        return running.first { $0.processIdentifier == front } ?? running.first
    }

    private func read(_ app: NSRunningApplication?, allowed: [String]) async -> LoomDriver.Snapshot {
        let driver = self.driver
        return await Task.detached { driver.snapshot(app: app, allowed: allowed) }.value
    }

    private func perform(
        _ action: DeviceAction, snapshot: LoomDriver.Snapshot, app: inout NSRunningApplication?,
        allowed: [String], askedForeground: inout Bool
    ) async -> DeviceActionResult {
        func fail(_ detail: String, _ blocked: ActionBlock? = nil) -> DeviceActionResult {
            DeviceActionResult(actionId: action.id, ok: false, detail: detail, blocked: blocked)
        }
        switch action.driverTool {
        case .openApp:
            let name = action.string("app") ?? ""
            let (opened, detail) = await driver.openApp(name, allowed: allowed)
            if let opened { app = opened }
            input.reset()
            await nap(Self.settleSeconds, wakeable: false)
            return DeviceActionResult(actionId: action.id, ok: opened != nil, detail: detail,
                                      blocked: opened == nil && ActionPolicy.isDenied(name: name, bundleId: nil) ? .appNotAllowed : nil)
        case .wait:
            await nap(min(10, max(0, action.args["seconds"]?.number ?? 1)))
            return DeviceActionResult(actionId: action.id, ok: true, detail: "Waited.")
        case nil:
            return fail("Loom doesn't know how to “\(action.tool)”.")
        default:
            break
        }
        guard let target = app, snapshot.pid == target.processIdentifier else {
            return fail("No allowed app is being used. Use open_app first.")
        }
        let context = ActionGate.Context(
            target: action.ref.flatMap { snapshot.outline.entry($0) },
            defaultButtonLabel: snapshot.defaultButtonLabel,
            focusedIsSecure: snapshot.focusedIsSecure,
            secureInputActive: LoomDriver.secureInputActive,
            appName: snapshot.appName,
            appAllowed: LoomDriver.isAllowed(target, allowed: allowed)
        )
        let foreground: Bool
        switch ActionGate.check(action, context) {
        case .refuse(let blocked, let detail):
            return fail(detail, blocked)
        case .proceed(let wantsFront):
            foreground = wantsFront
        }

        // Don't act in an app the person is using right now.
        guard await waitWhilePersonUses(target) else { return fail("Stopped.") }

        var previous: NSRunningApplication?
        if foreground, NSWorkspace.shared.frontmostApplication?.processIdentifier != target.processIdentifier {
            let seconds = askedForeground ? Self.repeatCountdownSeconds : Self.countdownSeconds
            guard await countdown(appName: snapshot.appName, seconds: seconds) else {
                return fail("The person said not now to bringing \(snapshot.appName) forward. Ask them, or try a background way.",
                            .declinedForeground)
            }
            askedForeground = true
            previous = NSWorkspace.shared.frontmostApplication
            target.activate()
            await nap(0.5, wakeable: false)
        }

        // Typing into the app in front stops if the person starts typing or clicking too.
        let driver = self.driver
        let started = Date().addingTimeInterval(0.2)
        let watchPerson = NSWorkspace.shared.frontmostApplication?.processIdentifier == target.processIdentifier
        let outcome = await Task.detached {
            driver.perform(action, on: snapshot, keepGoing: {
                !(watchPerson && UserInputWatcher.inputSince(started))
            })
        }.value
        input.reset()
        await nap(Self.settleSeconds, wakeable: false)
        if let previous, previous.processIdentifier != target.processIdentifier, !previous.isTerminated {
            previous.activate()
        }
        input.reset()
        return outcome
    }

    /// Returns false if the run was stopped while paused.
    private func waitWhilePersonUses(_ app: NSRunningApplication) async -> Bool {
        func usingIt() -> Bool {
            input.personIsActive() && NSWorkspace.shared.frontmostApplication?.processIdentifier == app.processIdentifier
        }
        guard usingIt() else { return !stopRequested }
        choice = nil
        guides.agentOverlay.showAgentPaused(title: title, appName: app.localizedName ?? "this app")
        while !stopRequested {
            if takeChoice() == .resume {
                input.reset()
                return true
            }
            await nap(0.5)
        }
        return false
    }

    private func countdown(appName: String, seconds: Int) async -> Bool {
        choice = nil
        for remaining in stride(from: seconds, to: 0, by: -1) {
            guides.agentOverlay.showAgentCountdown(title: title, appName: appName, seconds: remaining)
            await nap(1)
            if stopRequested { return false }
            if takeChoice() == .notNow { return false }
        }
        return true
    }

    /// The task waits for an approval or for the person to do something. Answers can
    /// come from the overlay or the web page; either way the next poll sees it.
    private func waitForPerson(_ first: DeviceTask, client: APIClient) async {
        var task = first
        var shown: DeviceTaskPending?
        choice = nil
        while !stopRequested, task.status.isWaitingForPerson {
            if shown != task.pending || shown == nil {
                shown = task.pending ?? DeviceTaskPending()
                switch task.status {
                case .needsApproval:
                    guides.agentOverlay.showAgentApproval(
                        title: title, description: task.pending?.description ?? "An action", reason: task.pending?.reason
                    )
                default:
                    guides.agentOverlay.showAgentAsk(title: title, instruction: task.pending?.instruction ?? "Check the app.")
                }
            }
            if let answer = takeChoice() {
                let reply: String? = switch (task.status, answer) {
                case (.needsApproval, .approve): "approve"
                case (.needsApproval, .reject): "reject"
                case (.needsPerson, .done): "done"
                case (.needsPerson, .cant): "cant"
                default: nil
                }
                if let reply {
                    input.reset()
                    if let updated = try? await client.replyDeviceTask(task.taskId, action: reply) {
                        task = updated
                        continue
                    }
                }
            }
            await nap(Self.waitPollSeconds)
            if let updated = try? await client.getDeviceTask(task.taskId) { task = updated }
        }
        input.reset()
        guides.agentOverlay.showAgentWorking(title: title, line: "Carrying on…", step: 0)
    }

    private func finish(_ status: DeviceTaskStatus, message: String?) {
        let shown: SkillRunStatus = switch status {
        case .completed: .completed
        case .failed: .failed
        default: .stopped
        }
        guides.showRunFinished(title: title, status: shown, message: message)
        guides.endRun(hideOverlay: false)
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
