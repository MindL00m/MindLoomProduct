import Foundation

/// The Mac's own check of an action before it touches an app. The API already checked
/// it; this repeats the checks against what the app shows right now, so a missed flag,
/// a changed screen or a bad server reply can't send, delete or type a password.
public enum ActionGate {
    public enum Verdict: Equatable {
        /// Go ahead. `foreground` asks the person to let Loom bring the app forward.
        case proceed(foreground: Bool)
        case refuse(ActionBlock?, String)
    }

    /// What the app shows right now for this action's target.
    public struct Context: Equatable {
        public var target: OutlineEntry?
        /// The label of the focused window's default button (what Return presses).
        public var defaultButtonLabel: String?
        /// The focused element is a password field.
        public var focusedIsSecure: Bool
        /// Secure Event Input is on (a password prompt somewhere has the keyboard).
        public var secureInputActive: Bool
        public var appName: String
        public var appAllowed: Bool

        public init(
            target: OutlineEntry? = nil, defaultButtonLabel: String? = nil, focusedIsSecure: Bool = false,
            secureInputActive: Bool = false, appName: String = "", appAllowed: Bool = true
        ) {
            self.target = target
            self.defaultButtonLabel = defaultButtonLabel
            self.focusedIsSecure = focusedIsSecure
            self.secureInputActive = secureInputActive
            self.appName = appName
            self.appAllowed = appAllowed
        }
    }

    public static func check(_ action: DeviceAction, _ context: Context) -> Verdict {
        guard let tool = action.driverTool else {
            return .refuse(nil, "Loom doesn't know how to “\(action.tool)”.")
        }
        if tool != .openApp, tool != .wait, !context.appAllowed {
            return .refuse(.appNotAllowed, "\(context.appName) isn't one of the apps allowed for this automation.")
        }

        // The target must still be what the action was chosen against.
        if [.press, .setValue, .scroll].contains(tool) || (tool == .typeText && action.ref != nil) {
            guard let target = context.target else {
                return .refuse(nil, "The element \(action.ref ?? "?") is no longer on screen. Look again.")
            }
            if let expected = action.expectLabel, normalized(expected) != normalized(target.label) {
                return .refuse(nil, "The screen changed: \(action.ref ?? "") is now “\(target.label)”, not “\(expected)”. Look again.")
            }
        }

        // Passwords are the person's.
        if tool == .setValue || tool == .typeText {
            if context.target?.secure == true || (action.ref == nil && context.focusedIsSecure) {
                return .refuse(.secureField, "That is a password field. Loom never fills those: ask the person.")
            }
            if context.secureInputActive {
                return .refuse(.secureField, "A password prompt has the keyboard. Loom won't type now: ask the person.")
            }
        }
        if tool == .key, context.secureInputActive {
            return .refuse(.secureField, "A password prompt has the keyboard. Loom won't press keys now: ask the person.")
        }

        // Anything that can't be undone needs the person's approval, whatever the API said.
        if !action.approved, let reason = irreversibleReason(action, tool: tool, context: context) {
            return .refuse(.needsApproval, reason)
        }

        switch tool {
        case .selectMenu:
            return .proceed(foreground: true)
        case .key, .typeText:
            return .proceed(foreground: action.flag("foreground"))
        default:
            return .proceed(foreground: false)
        }
    }

    public static func irreversibleReason(_ action: DeviceAction, tool: DriverTool, context: Context) -> String? {
        switch tool {
        case .press:
            if action.flag("irreversible") { return "Marked as not undoable." }
            let label = context.target?.label
            return ActionPolicy.looksIrreversible(label) ? "“\(label ?? "")” looks like it can't be undone." : nil
        case .selectMenu:
            if action.flag("irreversible") { return "Marked as not undoable." }
            let item = action.args["path"]?.strings.last
            return ActionPolicy.looksIrreversible(item) ? "“\(item ?? "")” looks like it can't be undone." : nil
        case .key:
            if action.flag("irreversible") { return "Marked as not undoable." }
            guard let combo = KeyCombo(action.string("combo") ?? "") else { return nil }
            if ActionPolicy.isIrreversibleKey(combo) { return "\(combo.normalized) usually sends or deletes." }
            if ActionPolicy.pressesDefaultButton(combo), ActionPolicy.looksIrreversible(context.defaultButtonLabel) {
                return "Return presses “\(context.defaultButtonLabel ?? "")”."
            }
            return nil
        default:
            return nil
        }
    }

    static func normalized(_ text: String) -> String {
        text.lowercased().split(whereSeparator: \.isWhitespace).joined(separator: " ")
    }
}
