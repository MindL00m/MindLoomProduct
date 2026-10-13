import AppKit

/// Always-visible control window with a three-page flow:
/// 1) Sign in  2) Capture  3) Setup (from the capture page).
final class ControlPanel: NSObject {
    private enum Page {
        case signIn
        case capture
        case setup
    }

    private let engine: CaptureEngine
    private var config: AgentConfig
    private let window: NSPanel

    private let pageContainer = NSView()
    private let signInPage = NSView()
    private let capturePage = NSView()
    private let setupPage = NSView()

    private let statusDot = LoomStatusDot()
    private let statusLabel = NSTextField(labelWithString: "Idle")
    private let statusDetailLabel = NSTextField(wrappingLabelWithString: "")
    private let accountLabel = NSTextField(wrappingLabelWithString: "")
    private let allowlistLabel = NSTextField(wrappingLabelWithString: "No apps allowlisted")
    private let permissionLabel = NSTextField(wrappingLabelWithString: "")
    private let signInStatusLabel = NSTextField(wrappingLabelWithString: "")

    private var signInButton: LoomButton!
    private var cancelSignInButton: LoomButton!
    private var signOutButton: LoomButton!
    private var startButton: LoomButton!
    private var pauseButton: LoomButton!
    private var endButton: LoomButton!
    private var setupNavButton: LoomButton!
    private var backFromSetupButton: LoomButton!
    private var allowlistButton: LoomButton!
    private var grantPermissionButton: LoomButton!
    private var recheckPermissionButton: LoomButton!
    private var quitFromSignInButton: LoomButton!
    private var quitFromCaptureButton: LoomButton!

    private var currentPage: Page = .signIn
    private var signingIn = false
    private var activeAuthSession: DesktopAuthSession?
    private var permissionPollTimer: Timer?

    var onConfigChange: ((AgentConfig) -> Void)?

    init(engine: CaptureEngine, config: AgentConfig) {
        self.engine = engine
        self.config = config
        self.window = NSPanel(
            contentRect: NSRect(x: 0, y: 0, width: 520, height: 500),
            styleMask: [.titled, .closable, .nonactivatingPanel, .fullSizeContentView],
            backing: .buffered,
            defer: false
        )
        super.init()
        configureWindow()
        buildUI()
        showPage(config.isSignedIn ? .capture : .signIn, animated: false)
        refresh()
        startPermissionPolling()
    }

    func show() {
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func reloadConfig(_ config: AgentConfig) {
        self.config = config
        if !config.isSignedIn {
            showPage(.signIn, animated: false)
        } else if currentPage == .signIn {
            showPage(.capture, animated: false)
        }
        refresh()
    }

    func refreshFromEngine() {
        refresh()
    }

    private func configureWindow() {
        window.title = "Loom Capture"
        window.titleVisibility = .hidden
        window.titlebarAppearsTransparent = true
        window.isFloatingPanel = true
        window.level = .floating
        window.hidesOnDeactivate = false
        window.isReleasedWhenClosed = false
        window.backgroundColor = LoomTheme.background
        window.isOpaque = true
        window.hasShadow = true
        window.center()
    }

    private func buildUI() {
        let content = NSView(frame: NSRect(x: 0, y: 0, width: 520, height: 500))
        content.wantsLayer = true
        content.layer?.backgroundColor = LoomTheme.background.cgColor
        window.contentView = content

        pageContainer.translatesAutoresizingMaskIntoConstraints = false
        content.addSubview(pageContainer)
        NSLayoutConstraint.activate([
            pageContainer.topAnchor.constraint(equalTo: content.topAnchor, constant: 40),
            pageContainer.leadingAnchor.constraint(equalTo: content.leadingAnchor, constant: 16),
            pageContainer.trailingAnchor.constraint(equalTo: content.trailingAnchor, constant: -16),
            pageContainer.bottomAnchor.constraint(equalTo: content.bottomAnchor, constant: -16),
        ])

        buildSignInPage()
        buildCapturePage()
        buildSetupPage()

        for page in [signInPage, capturePage, setupPage] {
            page.translatesAutoresizingMaskIntoConstraints = false
            page.isHidden = true
            pageContainer.addSubview(page)
            NSLayoutConstraint.activate([
                page.topAnchor.constraint(equalTo: pageContainer.topAnchor),
                page.leadingAnchor.constraint(equalTo: pageContainer.leadingAnchor),
                page.trailingAnchor.constraint(equalTo: pageContainer.trailingAnchor),
                page.bottomAnchor.constraint(equalTo: pageContainer.bottomAnchor),
            ])
        }
    }

    private func buildSignInPage() {
        signInStatusLabel.font = .systemFont(ofSize: 12)
        signInStatusLabel.textColor = LoomTheme.mutedForeground
        signInStatusLabel.alignment = .center
        signInStatusLabel.stringValue = "Use the same Google account as the Loom web app."

        signInButton = LoomButton(
            title: "Sign in with Google",
            style: .primary,
            target: self,
            action: #selector(signIn)
        )
        cancelSignInButton = LoomButton(
            title: "Cancel",
            style: .ghost,
            target: self,
            action: #selector(cancelSignIn)
        )
        cancelSignInButton.isHidden = true
        quitFromSignInButton = LoomButton(title: "Quit", style: .ghost, target: self, action: #selector(quit))

        let card = paddedCard(views: [signInStatusLabel, signInButton, cancelSignInButton], spacing: 10)
        let stack = LoomLayout.column(
            spacing: 12,
            views: [
                makeBrandHeader(subtitle: "Sign in to capture workflows for your organization."),
                card,
                flexibleSpacer(),
                quitFromSignInButton,
            ]
        )
        pinStack(stack, to: signInPage)
    }

    private func buildCapturePage() {
        startButton = LoomButton(title: "Start Session", style: .primary, target: self, action: #selector(startSession))
        pauseButton = LoomButton(title: "Pause / Resume", style: .secondary, target: self, action: #selector(togglePause))
        endButton = LoomButton(
            title: "End",
            style: .primary,
            target: self,
            action: #selector(endSession)
        )
        setupNavButton = LoomButton(
            title: "Setup",
            style: .secondary,
            target: self,
            action: #selector(openSetup)
        )
        signOutButton = LoomButton(title: "Sign out", style: .destructive, target: self, action: #selector(signOut))

        let sessionColumn = LoomLayout.column(
            spacing: 8,
            views: [
                makeSectionHeading("Session"),
                startButton,
                pauseButton,
                endButton,
            ]
        )

        let stack = LoomLayout.column(
            spacing: 10,
            views: [
                makeBrandHeader(subtitle: nil),
                makeStatusCard(),
                makeAccountAllowlistCard(),
                sessionColumn,
                flexibleSpacer(),
                setupNavButton,
                signOutButton,
            ]
        )
        pinStack(stack, to: capturePage)
    }

    private func buildSetupPage() {
        allowlistButton = LoomButton(
            title: "Allowlist Frontmost App",
            style: .secondary,
            target: self,
            action: #selector(addFrontmost)
        )
        grantPermissionButton = LoomButton(
            title: "Grant Accessibility…",
            style: .secondary,
            target: self,
            action: #selector(requestPermission)
        )
        recheckPermissionButton = LoomButton(
            title: "Recheck Accessibility",
            style: .ghost,
            target: self,
            action: #selector(recheckPermission)
        )
        quitFromCaptureButton = LoomButton(title: "Quit", style: .ghost, target: self, action: #selector(quit))
        backFromSetupButton = LoomButton(
            title: "Back",
            style: .secondary,
            target: self,
            action: #selector(closeSetup)
        )

        let stack = LoomLayout.column(
            spacing: 10,
            views: [
                makeBrandHeader(subtitle: "Allowlist apps and grant Accessibility."),
                makeSectionHeading("Setup"),
                allowlistButton,
                grantPermissionButton,
                recheckPermissionButton,
                quitFromCaptureButton,
                flexibleSpacer(),
                backFromSetupButton,
            ]
        )
        pinStack(stack, to: setupPage)
    }

    private func pinStack(_ stack: NSStackView, to page: NSView) {
        page.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.topAnchor.constraint(equalTo: page.topAnchor),
            stack.leadingAnchor.constraint(equalTo: page.leadingAnchor),
            stack.trailingAnchor.constraint(equalTo: page.trailingAnchor),
            stack.bottomAnchor.constraint(equalTo: page.bottomAnchor),
        ])
    }

    private func flexibleSpacer() -> NSView {
        let spacer = NSView()
        spacer.translatesAutoresizingMaskIntoConstraints = false
        spacer.setContentHuggingPriority(.defaultLow, for: .vertical)
        spacer.setContentCompressionResistancePriority(.defaultLow, for: .vertical)
        spacer.heightAnchor.constraint(greaterThanOrEqualToConstant: 4).isActive = true
        return spacer
    }

    private func paddedCard(views: [NSView], spacing: CGFloat) -> NSView {
        let wrap = LoomCardView()
        wrap.layer?.backgroundColor = LoomTheme.muted.cgColor
        let inner = LoomLayout.column(spacing: spacing, views: views)
        wrap.addSubview(inner)
        NSLayoutConstraint.activate([
            inner.topAnchor.constraint(equalTo: wrap.topAnchor, constant: 10),
            inner.leadingAnchor.constraint(equalTo: wrap.leadingAnchor, constant: 12),
            inner.trailingAnchor.constraint(equalTo: wrap.trailingAnchor, constant: -12),
            inner.bottomAnchor.constraint(equalTo: wrap.bottomAnchor, constant: -10),
        ])
        return wrap
    }

    private func makeBrandHeader(subtitle: String?) -> NSView {
        let wrap = LoomCardView()
        wrap.layer?.backgroundColor = LoomTheme.accent.cgColor

        let brand = NSTextField(labelWithString: "LOOM Capture")
        brand.font = .systemFont(ofSize: 18, weight: .heavy)
        brand.textColor = LoomTheme.primary
        brand.alignment = .center

        var views: [NSView] = [brand]
        if let subtitle, !subtitle.isEmpty {
            let line = NSTextField(wrappingLabelWithString: subtitle)
            line.font = .systemFont(ofSize: 11)
            line.textColor = LoomTheme.mutedForeground
            line.alignment = .center
            views.append(line)
        }

        let stack = LoomLayout.column(spacing: 2, views: views)
        wrap.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.topAnchor.constraint(equalTo: wrap.topAnchor, constant: 10),
            stack.leadingAnchor.constraint(equalTo: wrap.leadingAnchor, constant: 12),
            stack.trailingAnchor.constraint(equalTo: wrap.trailingAnchor, constant: -12),
            stack.bottomAnchor.constraint(equalTo: wrap.bottomAnchor, constant: -10),
        ])
        return wrap
    }

    private func makeStatusCard() -> NSView {
        statusLabel.font = .systemFont(ofSize: 13, weight: .semibold)
        statusLabel.textColor = LoomTheme.foreground
        statusLabel.alignment = .left

        statusDetailLabel.font = .systemFont(ofSize: 11)
        statusDetailLabel.textColor = LoomTheme.mutedForeground
        statusDetailLabel.alignment = .left

        let row = NSStackView(views: [statusDot, statusLabel])
        row.orientation = .horizontal
        row.alignment = .centerY
        row.spacing = 8
        row.translatesAutoresizingMaskIntoConstraints = false

        return paddedCard(views: [row, statusDetailLabel], spacing: 4)
    }

    private func makeAccountAllowlistCard() -> NSView {
        accountLabel.font = .systemFont(ofSize: 12, weight: .medium)
        accountLabel.textColor = LoomTheme.foreground
        accountLabel.alignment = .left

        allowlistLabel.font = .systemFont(ofSize: 12)
        allowlistLabel.textColor = LoomTheme.mutedForeground
        allowlistLabel.alignment = .left

        permissionLabel.font = .systemFont(ofSize: 11)
        permissionLabel.textColor = LoomTheme.warning
        permissionLabel.alignment = .left
        permissionLabel.maximumNumberOfLines = 2

        let accountTitle = makeInlineCaption("Signed in")
        let allowTitle = makeInlineCaption("Allowlist")

        let left = LoomLayout.column(spacing: 2, views: [accountTitle, accountLabel])
        let right = LoomLayout.column(spacing: 2, views: [allowTitle, allowlistLabel])
        let row = LoomLayout.row(spacing: 12, views: [left, right])

        return paddedCard(views: [row, permissionLabel], spacing: 6)
    }

    private func makeInlineCaption(_ text: String) -> NSTextField {
        let label = NSTextField(labelWithString: text.uppercased())
        label.font = .systemFont(ofSize: 10, weight: .bold)
        label.textColor = LoomTheme.mutedForeground
        label.alignment = .left
        return label
    }

    private func makeSectionHeading(_ text: String) -> NSTextField {
        let label = NSTextField(labelWithString: text)
        label.font = .systemFont(ofSize: 13, weight: .bold)
        label.textColor = LoomTheme.foreground
        label.alignment = .center
        return label
    }

    private func showPage(_ page: Page, animated: Bool) {
        currentPage = page
        let updates = {
            self.signInPage.isHidden = page != .signIn
            self.capturePage.isHidden = page != .capture
            self.setupPage.isHidden = page != .setup
        }
        if animated {
            NSAnimationContext.runAnimationGroup { ctx in
                ctx.duration = 0.18
                updates()
            }
        } else {
            updates()
        }
        switch page {
        case .signIn:
            window.title = "Loom Capture — Sign in"
        case .setup:
            window.title = "Loom Capture — Setup"
        case .capture:
            window.title = "Loom Capture"
        }
    }

    private func refresh() {
        if config.isSignedIn, currentPage == .signIn, !signingIn {
            showPage(.capture, animated: false)
        } else if !config.isSignedIn, currentPage != .signIn {
            showPage(.signIn, animated: false)
        }

        if signingIn {
            signInStatusLabel.stringValue = "Waiting for browser… finish Google sign-in, then return here."
            signInStatusLabel.textColor = LoomTheme.warning
            signInButton.setLoomTitle("Waiting for browser…")
            cancelSignInButton.isHidden = false
        } else {
            signInStatusLabel.stringValue = "Use the same Google account as the Loom web app."
            signInStatusLabel.textColor = LoomTheme.mutedForeground
            signInButton.setLoomTitle("Sign in with Google")
            cancelSignInButton.isHidden = true
        }
        signInButton.isEnabled = !signingIn
        cancelSignInButton.isEnabled = signingIn

        let detail: String
        switch engine.status {
        case .idle:
            statusLabel.stringValue = "Idle"
            statusDot.fillColor = LoomTheme.mutedForeground
            detail = "Allowlist apps, then start a session."
        case .capturing:
            statusDot.fillColor = LoomTheme.success
            if engine.sessionEventCount == 0 {
                statusLabel.stringValue = "Capturing"
                let waiting = config.allowlist.isEmpty
                    ? "Add an app to the allowlist."
                    : "Focus an allowlisted app and click or type."
                detail = "0 events · \(waiting)"
            } else {
                statusLabel.stringValue = "Capturing · \(engine.sessionEventCount) event(s)"
                detail = engine.sessionId.map { "Session \($0.prefix(18))…" } ?? "Session active"
            }
        case .paused:
            statusLabel.stringValue = "Paused · \(engine.sessionEventCount) event(s)"
            statusDot.fillColor = LoomTheme.warning
            detail = "Capture is paused. Resume when ready."
        case .needsPermission:
            statusLabel.stringValue = "Needs Accessibility"
            statusDot.fillColor = LoomTheme.destructive
            detail = "Grant permission, quit, and reopen the app."
        }
        statusDetailLabel.stringValue = detail

        accountLabel.stringValue = {
            let who = config.email.isEmpty ? "Not signed in" : config.email
            return "\(who) · \(config.apiBase)"
        }()

        if config.allowlist.isEmpty {
            allowlistLabel.stringValue = "Nothing is captured until you add apps."
            allowlistLabel.textColor = LoomTheme.warning
        } else {
            allowlistLabel.stringValue = config.allowlist.map(\.displayName).joined(separator: ", ")
            allowlistLabel.textColor = LoomTheme.foreground
        }

        if AccessibilityPermission.isTrusted {
            permissionLabel.stringValue = ""
            permissionLabel.isHidden = true
        } else {
            let bundleHint = AccessibilityPermission.isRunningFromAppBundle
                ? "Enable Loom Capture in System Settings → Accessibility, then Quit and reopen."
                : "Run the packaged .app (dist/Loom Capture.app), not a raw swift binary."
            permissionLabel.stringValue = "Accessibility not granted. \(bundleHint)"
            permissionLabel.isHidden = false
        }

        let sessionLive = engine.sessionId != nil
        pauseButton?.isEnabled = sessionLive
        endButton?.isEnabled = sessionLive && !signingIn
        startButton?.isEnabled = !sessionLive || engine.status == .idle || engine.status == .needsPermission
    }

    private func startPermissionPolling() {
        permissionPollTimer?.invalidate()
        permissionPollTimer = Timer.scheduledTimer(withTimeInterval: 1.5, repeats: true) { [weak self] _ in
            self?.refresh()
        }
    }

    @objc private func signIn() {
        guard !signingIn else { return }
        signingIn = true
        refresh()
        let session = DesktopAuthSession()
        activeAuthSession = session
        Task {
            do {
                let result = try await session.signIn(
                    webBase: config.webBase,
                    apiBase: config.apiBase
                )
                await MainActor.run {
                    self.activeAuthSession = nil
                    self.config.accessToken = result.accessToken
                    self.config.orgId = result.orgId
                    self.config.userId = result.userId
                    self.config.email = result.email
                    AgentConfigStore.save(self.config)
                    self.engine.reloadConfig(self.config)
                    self.onConfigChange?(self.config)
                    self.signingIn = false
                    self.showPage(.capture, animated: true)
                    self.refresh()
                    self.presentAlert(
                        title: "Signed in",
                        message: result.email.isEmpty
                            ? "Loom Capture is ready."
                            : "Signed in as \(result.email)."
                    )
                }
            } catch {
                await MainActor.run {
                    self.activeAuthSession = nil
                    self.signingIn = false
                    self.refresh()
                    let message = (error as? LocalizedError)?.errorDescription ?? error.localizedDescription
                    if (error as? DesktopAuthError) != .cancelled {
                        self.presentAlert(title: "Sign-in failed", message: message)
                    }
                }
            }
        }
    }

    @objc private func cancelSignIn() {
        activeAuthSession?.cancel()
        activeAuthSession = nil
        signingIn = false
        refresh()
    }

    @objc private func signOut() {
        if engine.sessionId != nil {
            presentAlert(
                title: "Session still active",
                message: "End or finish the current capture session before signing out."
            )
            return
        }
        config.accessToken = ""
        config.email = ""
        AgentConfigStore.save(config)
        engine.reloadConfig(config)
        onConfigChange?(config)
        showPage(.signIn, animated: true)
        refresh()
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

    @objc private func openSetup() {
        showPage(.setup, animated: true)
    }

    @objc private func closeSetup() {
        showPage(.capture, animated: true)
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
        await MainActor.run { refresh() }
    }

    @objc private func addFrontmost() {
        guard let app = engine.preferredAllowlistCandidate(),
              let bundleId = app.bundleIdentifier else {
            presentAlert(
                title: "No app found",
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
        onConfigChange?(config)
        refresh()
        presentAlert(
            title: "Allowlisted",
            message: "Added \(name) (\(bundleId)). Start a session, then keep that app focused while working."
        )
    }

    @objc private func requestPermission() {
        _ = AccessibilityPermission.promptIfNeeded()
        AccessibilityPermission.revealAppInFinder()
        AccessibilityPermission.openSystemSettings()
        presentAlert(
            title: "Enable Accessibility",
            message: """
            In System Settings → Privacy & Security → Accessibility:

            1. Remove every MindLoomAgent / Loom Capture / Loom Capture 2 row (minus).
            2. Click + and add this exact app (Finder will highlight it):
            \(AccessibilityPermission.currentAppPath)
            3. Turn the toggle ON (off then on if it was already on).
            4. Click Quit in Loom Capture, then open the same app again (prefer ~/Applications).

            Granting a copy under dist/ does not stick: packaging deletes dist/ on every rebuild.
            macOS does not apply Accessibility to a process that is already running.
            """
        )
        refresh()
    }

    @objc private func recheckPermission() {
        let trusted = AccessibilityPermission.isTrusted
        presentAlert(
            title: trusted ? "Accessibility granted" : "Still not granted",
            message: trusted
                ? "This process is trusted. You can start a capture session."
                : """
                Accessibility is still false for:
                \(AccessibilityPermission.currentAppPath)

                Remove old Loom Capture rows, add this file with +, toggle OFF then ON,
                Quit, and reopen this same app (not a leftover dist/ or “Loom Capture 2”).
                """
        )
        refresh()
    }

    @objc private func quit() {
        NSApp.terminate(nil)
    }

    private func presentAlert(title: String, message: String) {
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            let alert = NSAlert()
            alert.messageText = title
            alert.informativeText = message
            alert.alertStyle = .informational
            alert.beginSheetModal(for: self.window) { _ in }
        }
    }
}
