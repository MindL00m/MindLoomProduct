import AppKit
import GuideCore

/// What the overlay says about the current step of a skill run.
enum RunStepPhase: Equatable {
    case performing
    case inBrowser
    case waiting(SkillRunWaitReason, detail: String?)
}

/// A button on an automation card.
enum AgentChoice: Int {
    case approve = 1, reject, done, cant, stop, resume, notNow, allow
}

/// Floating, non-activating panel in the top-right corner that offers and plays guides.
final class GuideOverlay: NSObject {
    private static let width: CGFloat = 340
    private static let padding: CGFloat = 16

    var onStart: ((Guide) -> Void)?
    var onSnooze: (() -> Void)?
    var onNext: (() -> Void)?
    var onBack: (() -> Void)?
    var onExit: (() -> Void)?
    var onRunNext: (() -> Void)?
    var onRunStop: (() -> Void)?
    var onRunClose: (() -> Void)?
    var onAgentChoice: ((AgentChoice) -> Void)?

    private let panel: NSPanel
    private var suggestionGuides: [Guide] = []

    override init() {
        panel = NSPanel(
            contentRect: NSRect(x: 0, y: 0, width: Self.width, height: 200),
            styleMask: [.titled, .nonactivatingPanel, .fullSizeContentView],
            backing: .buffered,
            defer: false
        )
        super.init()
        panel.title = "Loom Guide"
        panel.titleVisibility = .hidden
        panel.titlebarAppearsTransparent = true
        panel.isFloatingPanel = true
        panel.level = .floating
        panel.hidesOnDeactivate = false
        panel.isReleasedWhenClosed = false
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        panel.backgroundColor = LoomTheme.background
        panel.hasShadow = true
        panel.standardWindowButton(.closeButton)?.isHidden = true
        panel.standardWindowButton(.miniaturizeButton)?.isHidden = true
        panel.standardWindowButton(.zoomButton)?.isHidden = true
    }

    var isVisible: Bool { panel.isVisible }

    func hide() {
        panel.orderOut(nil)
    }

    func showSuggestion(appName: String, guides: [Guide]) {
        suggestionGuides = Array(guides.prefix(3))
        var views: [NSView] = [
            caption("Guide available"),
            body(guides.count == 1
                ? "There's an approved workflow for \(appName):"
                : "There are \(guides.count) approved workflows for \(appName):"),
        ]
        for (index, guide) in suggestionGuides.enumerated() {
            let button = LoomButton(
                title: guide.title,
                style: index == 0 ? .primary : .secondary,
                target: self,
                action: #selector(startSuggested(_:))
            )
            button.tag = index
            views.append(button)
        }
        views.append(LoomButton(title: "Not now", style: .ghost, target: self, action: #selector(snooze)))
        render(views)
    }

    func showStep(_ session: GuideSession, manualHint: String?) {
        guard let step = session.currentStep else { return }
        var views: [NSView] = []

        var header: [NSView] = [caption("Step \(session.index + 1) of \(session.stepCount)")]
        if session.lastAdvanceWasAuto {
            let detected = caption("Previous step detected")
            detected.textColor = LoomTheme.success
            header.append(detected)
        }
        let headerRow = NSStackView(views: header)
        headerRow.orientation = .horizontal
        headerRow.spacing = 10
        views.append(headerRow)

        views.append(muted(session.guide.title))
        views.append(body(step.text, weight: .semibold, size: 14))

        if let manualHint {
            views.append(note(manualHint, color: LoomTheme.warning))
        } else if step.anchor != nil {
            views.append(note("Loom moves on when it sees this step.", color: LoomTheme.mutedForeground))
        } else {
            views.append(note("Click Next when you're done.", color: LoomTheme.mutedForeground))
        }

        views.append(contentsOf: hints(for: step, in: session.guide, index: session.index))

        let back = LoomButton(title: "Back", style: .ghost, target: self, action: #selector(back))
        back.isEnabled = session.index > 0
        let isLast = session.index == session.stepCount - 1
        let next = LoomButton(title: isLast ? "Finish" : "Next", style: .primary, target: self, action: #selector(next))
        views.append(LoomLayout.row(spacing: 8, views: [back, next]))
        views.append(LoomButton(title: "Exit guide", style: .ghost, target: self, action: #selector(exit)))
        render(views)
    }

    func showComplete(_ guide: Guide) {
        let done = caption("Guide complete")
        done.textColor = LoomTheme.success
        render([
            done,
            body(guide.title, weight: .semibold, size: 14),
            LoomButton(title: "Close", style: .primary, target: self, action: #selector(exit)),
        ])
    }

    // MARK: - Skill runs

    func showRunStep(_ guide: Guide, index: Int, phase: RunStepPhase) {
        guard guide.steps.indices.contains(index) else { return }
        let step = guide.steps[index]
        var views: [NSView] = [
            caption("Running · Step \(index + 1) of \(guide.steps.count)"),
            muted(guide.title),
            body(step.text, weight: .semibold, size: 14),
        ]
        switch phase {
        case .performing:
            views.append(note("Loom is doing this step.", color: LoomTheme.mutedForeground))
        case .inBrowser:
            views.append(note("The MindLoom Chrome extension is doing this step.", color: LoomTheme.mutedForeground))
        case .waiting(let reason, let detail):
            views.append(note(Self.waitText(reason, detail: detail), color: LoomTheme.warning))
        }
        views.append(contentsOf: hints(for: step, in: guide, index: index))

        let stop = LoomButton(title: "Stop run", style: .ghost, target: self, action: #selector(runStop))
        if case .waiting = phase {
            let isLast = index == guide.steps.count - 1
            let next = LoomButton(title: isLast ? "Finish" : "Next", style: .primary, target: self, action: #selector(runNext))
            views.append(LoomLayout.row(spacing: 8, views: [stop, next]))
        } else {
            views.append(stop)
        }
        render(views)
    }

    func showRunFinished(title: String, status: SkillRunStatus, message: String?) {
        let heading: NSTextField
        switch status {
        case .completed:
            heading = caption("Run complete")
            heading.textColor = LoomTheme.success
        case .failed:
            heading = caption("Run failed")
            heading.textColor = LoomTheme.warning
        default:
            heading = caption("Run stopped")
        }
        var views: [NSView] = [heading, body(title, weight: .semibold, size: 14)]
        if let message, !message.isEmpty {
            views.append(note(message, color: LoomTheme.mutedForeground))
        }
        views.append(LoomButton(title: "Close", style: .primary, target: self, action: #selector(runClose)))
        render(views)
    }

    // MARK: - Automation (device tasks)

    /// Shown on every automation card, so the person always knows how to stop it.
    static let stopHint = "Stop anytime: ⌃⌥⌘. (Control-Option-Command-Period)"

    func showAgentWorking(title: String, line: String, step: Int) {
        render([
            caption(step > 0 ? "MindLoom is working · Action \(step)" : "MindLoom is working"),
            muted(title),
            body(line, weight: .semibold, size: 14),
            note("Loom works in the background where it can. Keep using your Mac.", color: LoomTheme.mutedForeground),
            note(Self.stopHint, color: LoomTheme.mutedForeground),
            agentButton("Stop", .ghost, .stop),
        ])
    }

    func showAgentApproval(title: String, description: String, reason: String?) {
        let heading = caption("Approve this action?")
        heading.textColor = LoomTheme.warning
        var views: [NSView] = [heading, muted(title), body(description, weight: .semibold, size: 14)]
        if let reason, !reason.isEmpty {
            views.append(note(reason + " It may not be undoable.", color: LoomTheme.warning))
        }
        views.append(LoomLayout.row(spacing: 8, views: [
            agentButton("Reject", .secondary, .reject), agentButton("Approve", .primary, .approve),
        ]))
        views.append(agentButton("Stop run", .ghost, .stop))
        render(views)
    }

    func showAgentAsk(title: String, instruction: String) {
        render([
            caption("Your turn"),
            muted(title),
            body(instruction, weight: .semibold, size: 14),
            note("Do this on your Mac, then click Done. Loom carries on from there.", color: LoomTheme.mutedForeground),
            LoomLayout.row(spacing: 8, views: [
                agentButton("I can't", .secondary, .cant), agentButton("Done", .primary, .done),
            ]),
            agentButton("Stop run", .ghost, .stop),
        ])
    }

    func showAgentCountdown(title: String, appName: String, seconds: Int) {
        render([
            caption("Loom needs the screen"),
            muted(title),
            body("Bringing \(appName) forward in \(seconds)…", weight: .semibold, size: 14),
            note("For a few seconds only; Loom puts your app back afterwards. Don't type or click meanwhile.",
                 color: LoomTheme.mutedForeground),
            LoomLayout.row(spacing: 8, views: [agentButton("Not now", .secondary, .notNow), agentButton("Stop", .ghost, .stop)]),
        ])
    }

    func showAgentPaused(title: String, appName: String) {
        let heading = caption("Paused")
        heading.textColor = LoomTheme.warning
        render([
            heading,
            muted(title),
            body("You're using \(appName), so Loom stopped acting in it.", weight: .semibold, size: 14),
            note("Click Resume when you're done with \(appName).", color: LoomTheme.mutedForeground),
            LoomLayout.row(spacing: 8, views: [agentButton("Stop run", .ghost, .stop), agentButton("Resume", .primary, .resume)]),
        ])
    }

    func showAgentConsent(apps: [String]) {
        render([
            caption("Let MindLoom work on this Mac?"),
            body("An automation wants MindLoom to operate \(apps.joined(separator: ", ")) for you.", weight: .semibold, size: 14),
            body("• While a run is going, Loom sends what those apps show (not password fields) to MindLoom to decide each step."),
            body("• Sending, deleting, paying and anything else that can't be undone waits for your approval."),
            body("• Loom never types passwords and never touches other apps."),
            note(Self.stopHint, color: LoomTheme.mutedForeground),
            LoomLayout.row(spacing: 8, views: [agentButton("Not now", .secondary, .notNow), agentButton("Allow", .primary, .allow)]),
        ])
    }

    func showAgentNotice(title: String, message: String) {
        let heading = caption(title)
        heading.textColor = LoomTheme.warning
        render([heading, body(message), LoomButton(title: "Close", style: .ghost, target: self, action: #selector(runClose))])
    }

    private func agentButton(_ title: String, _ style: LoomButtonStyle, _ choice: AgentChoice) -> LoomButton {
        let button = LoomButton(title: title, style: style, target: self, action: #selector(agentChose(_:)))
        button.tag = choice.rawValue
        return button
    }

    @objc private func agentChose(_ sender: LoomButton) {
        if let choice = AgentChoice(rawValue: sender.tag) { onAgentChoice?(choice) }
    }

    static func waitText(_ reason: SkillRunWaitReason, detail: String?) -> String {
        switch reason {
        case .noAnchor:
            return "Do this step yourself, then click Next."
        case .needsValue:
            return "This step needs a value. Loom never types or picks values, so fill it in yourself, then click Next."
        case .actionFailed:
            let what = detail ?? "Loom couldn't do this step."
            return "\(what) Check the app first. If the step still needs doing, do it yourself, then click Next."
        case .agentOffline:
            return "The MindLoom Chrome extension didn't pick up this step. Check it's installed and signed in, or do the step yourself, then click Next."
        }
    }

    // MARK: - Layout

    private func hints(for step: GuideStep, in guide: Guide, index: Int) -> [NSView] {
        var views: [NSView] = []
        let fields = GuideHints.fields(guide.importantFields, for: step)
        if !fields.isEmpty {
            views.append(caption("Fields"))
            views.append(contentsOf: fields.map { body("• \($0)") })
        }
        var warnings = GuideHints.warnings(guide.warnings, for: step)
        if index == 0, warnings.isEmpty {
            warnings = guide.warnings
        }
        if !warnings.isEmpty {
            let heading = caption(index == 0 ? "Before you start" : "Watch out")
            heading.textColor = LoomTheme.warning
            views.append(heading)
            views.append(contentsOf: warnings.prefix(3).map { body("• \($0)") })
        }
        return views
    }

    private func render(_ views: [NSView]) {
        let content = NSView()
        content.wantsLayer = true
        content.layer?.backgroundColor = LoomTheme.background.cgColor

        let stack = LoomLayout.column(spacing: 8, views: views)
        content.addSubview(stack)
        let innerWidth = Self.width - Self.padding * 2
        NSLayoutConstraint.activate([
            stack.topAnchor.constraint(equalTo: content.topAnchor, constant: 28),
            stack.leadingAnchor.constraint(equalTo: content.leadingAnchor, constant: Self.padding),
            stack.widthAnchor.constraint(equalToConstant: innerWidth),
            stack.bottomAnchor.constraint(equalTo: content.bottomAnchor, constant: -Self.padding),
        ])
        for case let label as NSTextField in views {
            label.preferredMaxLayoutWidth = innerWidth
        }
        panel.contentView = content
        content.layoutSubtreeIfNeeded()
        let height = max(120, content.fittingSize.height)
        panel.setContentSize(NSSize(width: Self.width, height: height))
        positionTopRight()
        panel.orderFrontRegardless()
    }

    private func positionTopRight() {
        let screen = NSScreen.screens.first(where: { $0.frame.contains(NSEvent.mouseLocation) }) ?? NSScreen.main
        guard let visible = screen?.visibleFrame else { return }
        let size = panel.frame.size
        panel.setFrameOrigin(NSPoint(x: visible.maxX - size.width - 16, y: visible.maxY - size.height - 16))
    }

    private func caption(_ text: String) -> NSTextField {
        let label = NSTextField(labelWithString: text.uppercased())
        label.font = .systemFont(ofSize: 10, weight: .bold)
        label.textColor = LoomTheme.primary
        return label
    }

    private func muted(_ text: String) -> NSTextField {
        let label = NSTextField(wrappingLabelWithString: text)
        label.font = .systemFont(ofSize: 11)
        label.textColor = LoomTheme.mutedForeground
        return label
    }

    private func body(_ text: String, weight: NSFont.Weight = .regular, size: CGFloat = 12) -> NSTextField {
        let label = NSTextField(wrappingLabelWithString: text)
        label.font = .systemFont(ofSize: size, weight: weight)
        label.textColor = LoomTheme.foreground
        return label
    }

    private func note(_ text: String, color: NSColor) -> NSTextField {
        let label = NSTextField(wrappingLabelWithString: text)
        label.font = .systemFont(ofSize: 11)
        label.textColor = color
        return label
    }

    // MARK: - Actions

    @objc private func startSuggested(_ sender: LoomButton) {
        guard suggestionGuides.indices.contains(sender.tag) else { return }
        onStart?(suggestionGuides[sender.tag])
    }

    @objc private func snooze() { onSnooze?() }
    @objc private func next() { onNext?() }
    @objc private func back() { onBack?() }
    @objc private func exit() { onExit?() }
    @objc private func runNext() { onRunNext?() }
    @objc private func runStop() { onRunStop?() }
    @objc private func runClose() { onRunClose?() }
}
