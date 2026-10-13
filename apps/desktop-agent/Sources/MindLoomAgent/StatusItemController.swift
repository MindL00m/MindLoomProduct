import AppKit

final class StatusItemController: NSObject {
    private let statusItem: NSStatusItem
    private let engine: CaptureEngine
    private var config: AgentConfig

    init(engine: CaptureEngine, config: AgentConfig) {
        self.engine = engine
        self.config = config
        // Variable length + title text so the item is obvious in a crowded menu bar.
        self.statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        super.init()
        if let button = statusItem.button {
            button.title = "Loom"
            button.image = NSImage(
                systemSymbolName: "circle.fill",
                accessibilityDescription: "MindLoom Capture"
            )
            button.image?.isTemplate = true
            button.imagePosition = .imageLeading
            button.toolTip = "MindLoom Capture — Idle"
        }
        rebuildMenu()
        applyStatus(engine.status)
    }

    func reloadConfig(_ config: AgentConfig) {
        self.config = config
        engine.reloadConfig(config)
        rebuildMenu()
    }

    func applyExternalStatus(_ status: CaptureStatus) {
        applyStatus(status)
    }

    private func applyStatus(_ status: CaptureStatus) {
        let symbol: String
        let title: String
        switch status {
        case .idle:
            symbol = "circle"
            title = "Idle"
        case .capturing:
            symbol = "record.circle"
            title = "Capturing"
        case .paused:
            symbol = "pause.circle"
            title = "Paused"
        case .needsPermission:
            symbol = "exclamationmark.triangle"
            title = "Needs Accessibility"
        }
        if let button = statusItem.button {
            button.title = "Loom"
            button.image = NSImage(systemSymbolName: symbol, accessibilityDescription: title)
            button.image?.isTemplate = true
            button.imagePosition = .imageLeading
            button.toolTip = "MindLoom Capture — \(title)"
        }
        rebuildMenu()
    }

    private func rebuildMenu() {
        let menu = NSMenu()
        let statusLine = NSMenuItem(
            title: "Status: \(statusLabel(engine.status))",
            action: nil,
            keyEquivalent: ""
        )
        statusLine.isEnabled = false
        menu.addItem(statusLine)

        if let sessionId = engine.sessionId {
            let sessionLine = NSMenuItem(title: "Session: \(sessionId.prefix(20))…", action: nil, keyEquivalent: "")
            sessionLine.isEnabled = false
            menu.addItem(sessionLine)
        }

        menu.addItem(NSMenuItem.separator())

        if engine.sessionId == nil {
            menu.addItem(item("Start Session", #selector(startSession)))
        } else {
            if engine.isPaused {
                menu.addItem(item("Resume Capture", #selector(resumeSession)))
            } else {
                menu.addItem(item("Pause Capture", #selector(pauseSession)))
            }
            menu.addItem(item("End", #selector(endSession)))
        }

        menu.addItem(NSMenuItem.separator())

        let allowlistHeader = NSMenuItem(
            title: "Allowlist (\(config.allowlist.count) apps)",
            action: nil,
            keyEquivalent: ""
        )
        allowlistHeader.isEnabled = false
        menu.addItem(allowlistHeader)

        if config.allowlist.isEmpty {
            let empty = NSMenuItem(title: "  (empty — nothing is captured)", action: nil, keyEquivalent: "")
            empty.isEnabled = false
            menu.addItem(empty)
        } else {
            for app in config.allowlist.prefix(12) {
                let row = NSMenuItem(
                    title: "  \(app.displayName) — Remove",
                    action: #selector(removeAllowlisted(_:)),
                    keyEquivalent: ""
                )
                row.target = self
                row.representedObject = app.bundleId
                menu.addItem(row)
            }
        }

        menu.addItem(item("Add Frontmost App to Allowlist", #selector(addFrontmost)))
        menu.addItem(item("Open Config File…", #selector(openConfig)))
        menu.addItem(item("Reload Config", #selector(reloadConfigFromDisk)))

        menu.addItem(NSMenuItem.separator())
        if !AccessibilityPermission.isTrusted {
            menu.addItem(item("Grant Accessibility Permission…", #selector(requestPermission)))
        }
        menu.addItem(item("Quit MindLoom Agent", #selector(quit)))

        statusItem.menu = menu
    }

    private func statusLabel(_ status: CaptureStatus) -> String {
        switch status {
        case .idle: return "Idle"
        case .capturing: return "Capturing"
        case .paused: return "Paused"
        case .needsPermission: return "Needs Accessibility permission"
        }
    }

    private func item(_ title: String, _ selector: Selector) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: selector, keyEquivalent: "")
        item.target = self
        return item
    }

    @objc private func startSession() {
        engine.startSession()
        rebuildMenu()
    }

    @objc private func pauseSession() {
        engine.pause()
    }

    @objc private func resumeSession() {
        engine.resume()
    }

    @objc private func endSession() {
        Task { await finish(analyze: true) }
    }

    private func finish(analyze: Bool) async {
        do {
            try await engine.endSessionAndUpload(analyze: analyze)
            presentAlert(
                title: analyze ? "Skill File drafted" : "Session uploaded",
                message: analyze
                    ? "Uploaded and drafted a Skill File. Review it in Workflows."
                    : "Task summaries uploaded. A Skill File draft will appear in Workflows shortly — refresh that tab."
            )
        } catch {
            presentAlert(title: "Upload failed", message: error.localizedDescription)
        }
        await MainActor.run { rebuildMenu() }
    }

    @objc private func addFrontmost() {
        guard let app = engine.preferredAllowlistCandidate(),
              let bundleId = app.bundleIdentifier else {
            presentAlert(
                title: "No frontmost app",
                message: "Activate the app you want to allowlist (click its window), then try again."
            )
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
        rebuildMenu()
    }

    @objc private func removeAllowlisted(_ sender: NSMenuItem) {
        guard let bundleId = sender.representedObject as? String else { return }
        config.allowlist.removeAll { $0.bundleId == bundleId }
        AgentConfigStore.save(config)
        engine.reloadConfig(config)
        rebuildMenu()
    }

    @objc private func openConfig() {
        let url = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".mindloom/agent.json")
        AgentConfigStore.save(config)
        NSWorkspace.shared.activateFileViewerSelecting([url])
    }

    @objc private func reloadConfigFromDisk() {
        reloadConfig(AgentConfigStore.load())
    }

    @objc private func requestPermission() {
        AccessibilityPermission.promptIfNeeded()
        AccessibilityPermission.openSystemSettings()
    }

    @objc private func quit() {
        NSApp.terminate(nil)
    }

    private func presentAlert(title: String, message: String) {
        DispatchQueue.main.async {
            let alert = NSAlert()
            alert.messageText = title
            alert.informativeText = message
            alert.alertStyle = .informational
            alert.runModal()
        }
    }
}
