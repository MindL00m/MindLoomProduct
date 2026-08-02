import AppKit

/// Always-visible control window — menu-bar items often get squeezed off on crowded bars.
final class ControlPanel: NSObject {
    private let engine: CaptureEngine
    private var config: AgentConfig
    private let window: NSPanel
    private let statusLabel = NSTextField(labelWithString: "Status: Idle")
    private let allowlistLabel = NSTextField(labelWithString: "Allowlist: 0 apps")
    private let permissionLabel = NSTextField(labelWithString: "")
    private var actionButtons: [NSButton] = []

    var onConfigChange: ((AgentConfig) -> Void)?

    init(engine: CaptureEngine, config: AgentConfig) {
        self.engine = engine
        self.config = config
        self.window = NSPanel(
            contentRect: NSRect(x: 0, y: 0, width: 360, height: 280),
            styleMask: [.titled, .closable, .nonactivatingPanel, .utilityWindow],
            backing: .buffered,
            defer: false
        )
        super.init()
        window.title = "Loom Capture"
        window.isFloatingPanel = true
        window.level = .floating
        window.hidesOnDeactivate = false
        window.isReleasedWhenClosed = false
        window.center()
        buildUI()
        refresh()
    }

    func show() {
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func reloadConfig(_ config: AgentConfig) {
        self.config = config
        refresh()
    }

    func refreshFromEngine() {
        refresh()
    }

    private func buildUI() {
        let root = NSStackView()
        root.orientation = .vertical
        root.alignment = .leading
        root.spacing = 10
        root.edgeInsets = NSEdgeInsets(top: 16, left: 16, bottom: 16, right: 16)
        root.translatesAutoresizingMaskIntoConstraints = false

        let title = NSTextField(labelWithString: "Loom Capture Agent")
        title.font = .boldSystemFont(ofSize: 15)

        statusLabel.font = .systemFont(ofSize: 13)
        allowlistLabel.font = .systemFont(ofSize: 12)
        allowlistLabel.textColor = .secondaryLabelColor
        permissionLabel.font = .systemFont(ofSize: 12)
        permissionLabel.textColor = .systemOrange
        permissionLabel.lineBreakMode = .byWordWrapping
        permissionLabel.maximumNumberOfLines = 3

        let hint = NSTextField(wrappingLabelWithString:
            "This window is the capture control surface. The menu-bar item may be hidden when the bar is full."
        )
        hint.font = .systemFont(ofSize: 11)
        hint.textColor = .secondaryLabelColor

        root.addArrangedSubview(title)
        root.addArrangedSubview(statusLabel)
        root.addArrangedSubview(allowlistLabel)
        root.addArrangedSubview(permissionLabel)
        root.addArrangedSubview(hint)

        let buttons = NSStackView()
        buttons.orientation = .vertical
        buttons.alignment = .width
        buttons.spacing = 6

        let specs: [(String, Selector)] = [
            ("Start Session", #selector(startSession)),
            ("Pause / Resume", #selector(togglePause)),
            ("End & Upload Summary", #selector(endUpload)),
            ("End, Upload & Create Skill File", #selector(endUploadAnalyze)),
            ("Add Frontmost App to Allowlist", #selector(addFrontmost)),
            ("Grant Accessibility Permission…", #selector(requestPermission)),
            ("Quit", #selector(quit)),
        ]
        for (title, selector) in specs {
            let button = NSButton(title: title, target: self, action: selector)
            button.bezelStyle = .rounded
            button.setButtonType(.momentaryPushIn)
            buttons.addArrangedSubview(button)
            actionButtons.append(button)
        }
        root.addArrangedSubview(buttons)

        window.contentView = NSView(frame: NSRect(x: 0, y: 0, width: 360, height: 280))
        guard let content = window.contentView else { return }
        content.addSubview(root)
        NSLayoutConstraint.activate([
            root.topAnchor.constraint(equalTo: content.topAnchor),
            root.leadingAnchor.constraint(equalTo: content.leadingAnchor),
            root.trailingAnchor.constraint(equalTo: content.trailingAnchor),
            root.bottomAnchor.constraint(equalTo: content.bottomAnchor),
        ])
    }

    private func refresh() {
        let status: String
        switch engine.status {
        case .idle: status = "Idle"
        case .capturing: status = "Capturing"
        case .paused: status = "Paused"
        case .needsPermission: status = "Needs Accessibility permission"
        }
        statusLabel.stringValue = "Status: \(status)"
        if let sessionId = engine.sessionId {
            statusLabel.stringValue += " · \(sessionId.prefix(18))…"
        }
        allowlistLabel.stringValue = "Allowlist: \(config.allowlist.count) app(s)"
            + (config.allowlist.isEmpty ? " — nothing is captured until you add apps" : "")
        permissionLabel.stringValue = AccessibilityPermission.isTrusted
            ? ""
            : "Accessibility is not granted. Click “Grant Accessibility Permission…”."
        permissionLabel.isHidden = AccessibilityPermission.isTrusted
    }

    @objc private func startSession() {
        engine.startSession()
        refresh()
    }

    @objc private func togglePause() {
        guard engine.sessionId != nil else {
            presentAlert(title: "No active session", message: "Start a session first.")
            return
        }
        if engine.isPaused {
            engine.resume()
        } else {
            engine.pause()
        }
        refresh()
    }

    @objc private func endUpload() {
        Task { await finish(analyze: false) }
    }

    @objc private func endUploadAnalyze() {
        Task { await finish(analyze: true) }
    }

    private func finish(analyze: Bool) async {
        do {
            try await engine.endSessionAndUpload(analyze: analyze)
            presentAlert(
                title: analyze ? "Skill File drafted" : "Session uploaded",
                message: analyze
                    ? "Uploaded and drafted a Skill File. Review it in Workflows."
                    : "Task summaries were uploaded to the API."
            )
        } catch {
            presentAlert(title: "Upload failed", message: error.localizedDescription)
        }
        refresh()
    }

    @objc private func addFrontmost() {
        // Prefer the app behind this panel, not the agent itself.
        let apps = NSWorkspace.shared.runningApplications.filter {
            $0.activationPolicy == .regular && !$0.isTerminated
        }
        // Frontmost among regular apps excluding our process.
        let front = NSWorkspace.shared.frontmostApplication
        let candidate: NSRunningApplication?
        if let front, front.processIdentifier != ProcessInfo.processInfo.processIdentifier,
           front.activationPolicy == .regular {
            candidate = front
        } else {
            candidate = apps.first { $0.processIdentifier != ProcessInfo.processInfo.processIdentifier }
        }
        guard let app = candidate, let bundleId = app.bundleIdentifier else {
            presentAlert(title: "No app found", message: "Activate the app you want to allowlist, then try again.")
            return
        }
        let name = app.localizedName ?? bundleId
        if config.allowlist.contains(where: { $0.bundleId == bundleId }) {
            presentAlert(title: "Already allowlisted", message: "\(name) is already on the allowlist.")
            return
        }
        config.allowlist.append(AllowlistedApp(bundleId: bundleId, displayName: name))
        AgentConfigStore.save(config)
        engine.reloadConfig(config)
        onConfigChange?(config)
        refresh()
        presentAlert(title: "Allowlisted", message: "Added \(name) (\(bundleId)).")
    }

    @objc private func requestPermission() {
        AccessibilityPermission.promptIfNeeded()
        AccessibilityPermission.openSystemSettings()
        refresh()
    }

    @objc private func quit() {
        NSApp.terminate(nil)
    }

    private func presentAlert(title: String, message: String) {
        let alert = NSAlert()
        alert.messageText = title
        alert.informativeText = message
        alert.alertStyle = .informational
        alert.beginSheetModal(for: window) { _ in }
    }
}
