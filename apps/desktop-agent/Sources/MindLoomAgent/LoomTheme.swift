import AppKit

/// Visual tokens aligned with the Loom web app (`apps/web/src/index.css`).
enum LoomTheme {
    /// Primary rust `#b85c2c`
    static let primary = NSColor(srgbRed: 0.722, green: 0.361, blue: 0.173, alpha: 1)
    static let primaryHover = NSColor(srgbRed: 0.62, green: 0.30, blue: 0.14, alpha: 1)
    static let primaryForeground = NSColor.white

    static let foreground = NSColor(srgbRed: 0.153, green: 0.149, blue: 0.141, alpha: 1)
    static let mutedForeground = NSColor(srgbRed: 0.478, green: 0.475, blue: 0.439, alpha: 1)

    static let background = NSColor.white
    static let muted = NSColor(srgbRed: 0.961, green: 0.965, blue: 0.973, alpha: 1)
    static let accent = NSColor(srgbRed: 0.949, green: 0.902, blue: 0.812, alpha: 1)
    static let border = NSColor(srgbRed: 0.898, green: 0.894, blue: 0.875, alpha: 1)

    static let success = NSColor(srgbRed: 0.165, green: 0.604, blue: 0.306, alpha: 1)
    static let warning = NSColor(srgbRed: 0.722, green: 0.451, blue: 0.122, alpha: 1)
    static let destructive = NSColor(srgbRed: 0.863, green: 0.184, blue: 0.184, alpha: 1)

    static let cornerRadius: CGFloat = 12
    static let controlHeight: CGFloat = 38
    static let controlFont = NSFont.systemFont(ofSize: 13, weight: .semibold)
}

enum LoomButtonStyle {
    case primary
    case secondary
    case ghost
    case destructive
}

/// Custom-drawn button — AppKit's borderless NSButton hides attributed titles when disabled,
/// which produced the empty white rectangles.
final class LoomButton: NSControl {
    var style: LoomButtonStyle = .secondary {
        didSet { needsDisplay = true }
    }

    private var label: String
    private var hovered = false
    private var pressed = false
    private var tracking: NSTrackingArea?

    var loomTitle: String {
        get { label }
        set {
            label = newValue
            toolTip = newValue
            needsDisplay = true
        }
    }

    override var isEnabled: Bool {
        didSet { needsDisplay = true }
    }

    init(title: String, style: LoomButtonStyle, target: AnyObject?, action: Selector?) {
        self.label = title
        self.style = style
        super.init(frame: .zero)
        self.target = target
        self.action = action
        wantsLayer = false
        isEnabled = true
        toolTip = title
        translatesAutoresizingMaskIntoConstraints = false
        setContentHuggingPriority(.defaultLow, for: .horizontal)
        setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        heightAnchor.constraint(equalToConstant: LoomTheme.controlHeight).isActive = true
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    func setLoomTitle(_ text: String) {
        loomTitle = text
    }

    override var intrinsicContentSize: NSSize {
        NSSize(width: NSView.noIntrinsicMetric, height: LoomTheme.controlHeight)
    }

    override func updateTrackingAreas() {
        super.updateTrackingAreas()
        if let tracking { removeTrackingArea(tracking) }
        let area = NSTrackingArea(
            rect: bounds,
            options: [.activeInKeyWindow, .mouseEnteredAndExited, .inVisibleRect],
            owner: self,
            userInfo: nil
        )
        addTrackingArea(area)
        tracking = area
    }

    override func mouseEntered(with event: NSEvent) {
        hovered = true
        needsDisplay = true
    }

    override func mouseExited(with event: NSEvent) {
        hovered = false
        pressed = false
        needsDisplay = true
    }

    override func mouseDown(with event: NSEvent) {
        guard isEnabled else { return }
        pressed = true
        needsDisplay = true
    }

    override func mouseUp(with event: NSEvent) {
        let wasPressed = pressed
        pressed = false
        needsDisplay = true
        guard isEnabled, wasPressed, bounds.contains(convert(event.locationInWindow, from: nil)) else { return }
        if let action {
            NSApp.sendAction(action, to: target, from: self)
        }
    }

    override func draw(_ dirtyRect: NSRect) {
        let rect = bounds.insetBy(dx: 0.5, dy: 0.5)
        let path = NSBezierPath(roundedRect: rect, xRadius: LoomTheme.cornerRadius, yRadius: LoomTheme.cornerRadius)

        let fill: NSColor
        let stroke: NSColor
        let titleColor: NSColor
        let active = isEnabled && (hovered || pressed)

        switch style {
        case .primary:
            fill = active ? LoomTheme.primaryHover : LoomTheme.primary
            stroke = .clear
            titleColor = LoomTheme.primaryForeground
        case .secondary:
            fill = active ? LoomTheme.accent : LoomTheme.muted
            stroke = LoomTheme.border
            titleColor = LoomTheme.foreground
        case .ghost:
            fill = active ? LoomTheme.muted : LoomTheme.background
            stroke = LoomTheme.border
            titleColor = LoomTheme.foreground
        case .destructive:
            fill = active
                ? LoomTheme.destructive
                : LoomTheme.destructive.withAlphaComponent(0.10)
            stroke = LoomTheme.destructive.withAlphaComponent(0.45)
            titleColor = active ? .white : LoomTheme.destructive
        }

        fill.setFill()
        path.fill()
        if stroke != .clear {
            stroke.setStroke()
            path.lineWidth = 1
            path.stroke()
        }

        let attrs: [NSAttributedString.Key: Any] = [
            .font: LoomTheme.controlFont,
            .foregroundColor: titleColor,
            .paragraphStyle: {
                let p = NSMutableParagraphStyle()
                p.alignment = .center
                p.lineBreakMode = .byTruncatingTail
                return p
            }(),
        ]
        let text = NSAttributedString(string: label, attributes: attrs)
        let textSize = text.size()
        let textRect = NSRect(
            x: bounds.minX + 10,
            y: bounds.midY - textSize.height / 2,
            width: bounds.width - 20,
            height: textSize.height
        )
        text.draw(in: textRect)

        if !isEnabled {
            NSColor.white.withAlphaComponent(0.35).setFill()
            path.fill()
        }
    }
}

final class LoomCardView: NSView {
    override init(frame frameRect: NSRect) {
        super.init(frame: frameRect)
        wantsLayer = true
        layer?.backgroundColor = LoomTheme.background.cgColor
        layer?.cornerRadius = LoomTheme.cornerRadius
        layer?.borderWidth = 1
        layer?.borderColor = LoomTheme.border.cgColor
        translatesAutoresizingMaskIntoConstraints = false
        setContentHuggingPriority(.defaultLow, for: .horizontal)
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }
}

final class LoomStatusDot: NSView {
    var fillColor: NSColor = LoomTheme.mutedForeground {
        didSet { needsDisplay = true }
    }

    override init(frame frameRect: NSRect) {
        super.init(frame: frameRect)
        translatesAutoresizingMaskIntoConstraints = false
        widthAnchor.constraint(equalToConstant: 10).isActive = true
        heightAnchor.constraint(equalToConstant: 10).isActive = true
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    override func draw(_ dirtyRect: NSRect) {
        let path = NSBezierPath(ovalIn: bounds.insetBy(dx: 0.5, dy: 0.5))
        fillColor.setFill()
        path.fill()
    }
}

enum LoomLayout {
    /// Vertical stack of full-width controls.
    static func column(spacing: CGFloat = 10, views: [NSView]) -> NSStackView {
        let stack = NSStackView(views: views)
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.spacing = spacing
        stack.distribution = .fill
        stack.translatesAutoresizingMaskIntoConstraints = false
        for view in views {
            view.translatesAutoresizingMaskIntoConstraints = false
            view.widthAnchor.constraint(equalTo: stack.widthAnchor).isActive = true
        }
        return stack
    }

    /// Horizontal equal-width columns.
    static func row(spacing: CGFloat = 12, views: [NSView]) -> NSStackView {
        let stack = NSStackView(views: views)
        stack.orientation = .horizontal
        stack.alignment = .top
        stack.spacing = spacing
        stack.distribution = .fillEqually
        stack.translatesAutoresizingMaskIntoConstraints = false
        for view in views {
            view.translatesAutoresizingMaskIntoConstraints = false
            view.setContentHuggingPriority(.defaultLow, for: .horizontal)
        }
        return stack
    }
}
