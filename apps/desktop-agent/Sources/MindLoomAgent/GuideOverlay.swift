import AppKit
import GuideCore

/// Floating, non-activating panel in the top-right corner that offers and plays guides.
final class GuideOverlay: NSObject {
    private static let width: CGFloat = 340
    private static let padding: CGFloat = 16

    var onStart: ((Guide) -> Void)?
    var onSnooze: (() -> Void)?
    var onNext: (() -> Void)?
    var onBack: (() -> Void)?
    var onExit: (() -> Void)?

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

        let fields = GuideHints.fields(session.guide.importantFields, for: step)
        if !fields.isEmpty {
            views.append(caption("Fields"))
            views.append(contentsOf: fields.map { body("• \($0)") })
        }
        var warnings = GuideHints.warnings(session.guide.warnings, for: step)
        if session.index == 0, warnings.isEmpty {
            warnings = session.guide.warnings
        }
        if !warnings.isEmpty {
            let heading = caption(session.index == 0 ? "Before you start" : "Watch out")
            heading.textColor = LoomTheme.warning
            views.append(heading)
            views.append(contentsOf: warnings.prefix(3).map { body("• \($0)") })
        }

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

    // MARK: - Layout

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
}
