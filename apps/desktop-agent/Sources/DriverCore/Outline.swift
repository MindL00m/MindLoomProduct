import Foundation

/// One Accessibility element as the driver read it. `handle` indexes the driver's own
/// element table, so a ref in the outline maps back to the live element.
public struct OutlineNode: Equatable {
    public var handle: Int
    public var role: String
    public var subrole: String?
    public var label: String?
    public var value: String?
    public var enabled: Bool
    public var focused: Bool
    public var selected: Bool
    public var children: [OutlineNode]

    public init(
        handle: Int, role: String, subrole: String? = nil, label: String? = nil, value: String? = nil,
        enabled: Bool = true, focused: Bool = false, selected: Bool = false, children: [OutlineNode] = []
    ) {
        self.handle = handle
        self.role = role
        self.subrole = subrole
        self.label = label
        self.value = value
        self.enabled = enabled
        self.focused = focused
        self.selected = selected
        self.children = children
    }

    public var isSecure: Bool {
        Outline.secureRoles.contains(role) || subrole == "AXSecureTextField"
    }
}

/// One numbered line of the outline.
public struct OutlineEntry: Equatable {
    public var ref: String
    public var handle: Int
    public var role: String
    public var label: String
    public var secure: Bool
    public var focused: Bool

    public init(ref: String, handle: Int, role: String, label: String, secure: Bool = false, focused: Bool = false) {
        self.ref = ref
        self.handle = handle
        self.role = role
        self.label = label
        self.secure = secure
        self.focused = focused
    }

    public var summary: ElementSummary {
        ElementSummary(ref: ref, role: role, label: label, secure: secure, focused: focused)
    }
}

public struct RenderedOutline: Equatable {
    public var text: String
    public var entries: [OutlineEntry]

    public init(text: String, entries: [OutlineEntry]) {
        self.text = text
        self.entries = entries
    }

    public func entry(_ ref: String) -> OutlineEntry? { entries.first { $0.ref == ref } }
}

/// Turns an element tree into the compact, numbered text the model reads:
///
///     [e1] AXTextField "To" value="ada@example.com" (focused)
///     [e2] AXButton "Send"
///
/// Unlabelled layout containers are left out (their children move up), password fields
/// never show a value, long values are cut, and the outline stops at a size budget.
public enum Outline {
    public static let secureRoles: Set<String> = ["AXSecureTextField", "AXSecureTextArea"]

    /// Containers that only lay things out; shown only when they carry a label.
    static let layoutRoles: Set<String> = [
        "AXGroup", "AXScrollArea", "AXSplitGroup", "AXLayoutArea", "AXLayoutItem", "AXUnknown",
        "AXList", "AXOutline", "AXTable", "AXRow", "AXCell", "AXColumn", "AXBrowser",
        "AXGrid", "AXWebArea", "AXSplitter", "AXMatte", "AXGrowArea", "AXRuler", "AXRulerMarker",
    ]

    /// Roles always listed (they can be acted on), labelled or not.
    static let actionableRoles: Set<String> = [
        "AXButton", "AXCheckBox", "AXRadioButton", "AXPopUpButton", "AXMenuButton", "AXTextField",
        "AXTextArea", "AXSearchField", "AXComboBox", "AXSecureTextField", "AXSlider", "AXIncrementor",
        "AXLink", "AXTab", "AXTabGroup", "AXDisclosureTriangle", "AXColorWell", "AXDateField",
        "AXTimeField", "AXSegmentedControl", "AXStepper", "AXToolbarButton", "AXMenuItem",
    ]

    static let valueRoles: Set<String> = [
        "AXTextField", "AXTextArea", "AXSearchField", "AXComboBox", "AXStaticText", "AXCheckBox",
        "AXRadioButton", "AXPopUpButton", "AXSlider", "AXDateField", "AXTimeField", "AXHeading",
        "AXValueIndicator", "AXIncrementor", "AXLevelIndicator", "AXProgressIndicator",
    ]

    public static let maxValueLength = 200
    public static let maxTextLength = 300

    /// `header` lines (app, windows, menu bar) come first and take no refs.
    public static func render(
        _ roots: [OutlineNode], header: [String] = [], maxLines: Int = 400, maxChars: Int = 14_000
    ) -> RenderedOutline {
        var lines = header
        var entries: [OutlineEntry] = []
        var chars = header.reduce(0) { $0 + $1.count + 1 }
        var omitted = 0

        func visit(_ node: OutlineNode, depth: Int) {
            let shown = shouldShow(node)
            if shown {
                let line = describe(node, ref: "e\(entries.count + 1)", depth: depth)
                if entries.count >= maxLines || chars + line.count + 1 > maxChars {
                    omitted += 1
                } else {
                    entries.append(OutlineEntry(
                        ref: "e\(entries.count + 1)", handle: node.handle, role: node.role,
                        label: label(node), secure: node.isSecure, focused: node.focused
                    ))
                    lines.append(line)
                    chars += line.count + 1
                }
            }
            for child in node.children {
                visit(child, depth: shown ? depth + 1 : depth)
            }
        }
        for root in roots { visit(root, depth: 0) }
        if omitted > 0 {
            lines.append("… \(omitted) more elements not shown (scroll, or switch to the window you need)")
        }
        if entries.isEmpty {
            lines.append("(This window reports no usable controls through Accessibility.)")
        }
        return RenderedOutline(text: lines.joined(separator: "\n"), entries: entries)
    }

    static func shouldShow(_ node: OutlineNode) -> Bool {
        if actionableRoles.contains(node.role) || node.isSecure { return true }
        let hasLabel = !(node.label ?? "").isEmpty
        if node.role == "AXStaticText" || node.role == "AXHeading" {
            return hasLabel || !(node.value ?? "").isEmpty
        }
        if layoutRoles.contains(node.role) {
            // A row or cell is worth a line when it's selected (that's state the model needs).
            return hasLabel || (node.selected && (node.role == "AXRow" || node.role == "AXCell"))
        }
        return hasLabel || !(node.value ?? "").isEmpty
    }

    public static func label(_ node: OutlineNode) -> String {
        let label = (node.label ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        if !label.isEmpty { return String(label.prefix(maxTextLength)) }
        if node.role == "AXStaticText" || node.role == "AXHeading" {
            return clip(node.value ?? "", maxTextLength)
        }
        return ""
    }

    static func describe(_ node: OutlineNode, ref: String, depth: Int) -> String {
        var line = String(repeating: "  ", count: min(depth, 6)) + "[\(ref)] \(node.role)"
        let text = label(node)
        if !text.isEmpty { line += " \(quote(text))" }
        if node.isSecure {
            line += " (secure)"
        } else if valueRoles.contains(node.role), node.role != "AXStaticText", node.role != "AXHeading",
                  let value = node.value, !value.isEmpty {
            line += " value=\(quote(clip(value, maxValueLength)))"
        } else if (node.role == "AXStaticText" || node.role == "AXHeading"), !(node.label ?? "").isEmpty,
                  let value = node.value, !value.isEmpty, value != node.label {
            line += " value=\(quote(clip(value, maxValueLength)))"
        }
        var flags: [String] = []
        if node.focused { flags.append("focused") }
        if node.selected { flags.append("selected") }
        if !node.enabled { flags.append("disabled") }
        if !flags.isEmpty { line += " (\(flags.joined(separator: ", ")))" }
        return line
    }

    static func clip(_ text: String, _ limit: Int) -> String {
        let flat = text.replacingOccurrences(of: "\n", with: " ⏎ ").trimmingCharacters(in: .whitespaces)
        return flat.count > limit ? String(flat.prefix(limit)) + "…" : flat
    }

    static func quote(_ text: String) -> String {
        "\"" + text.replacingOccurrences(of: "\"", with: "'") + "\""
    }
}
