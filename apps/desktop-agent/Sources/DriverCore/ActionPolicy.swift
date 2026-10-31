import Foundation

/// What Loom may do on this Mac, whatever the API asks. The lists match
/// `apps/api/shuttle/device_tasks.py`; keep them in step.
public enum ActionPolicy {
    /// Apps Loom never operates: they hold passwords, keys or system and security settings.
    public static let deniedBundleIds: Set<String> = [
        "com.apple.systempreferences", "com.apple.Settings", "com.apple.keychainaccess",
        "com.apple.Passwords", "com.apple.Terminal", "com.googlecode.iterm2",
        "com.1password.1password", "com.agilebits.onepassword7", "com.bitwarden.desktop",
        "com.lastpass.LastPass", "com.dashlane.dashlanephonefinal", "com.apple.DiskUtility",
        "com.apple.ScriptEditor2", "com.apple.Automator", "com.apple.ActivityMonitor",
        "com.apple.Console", "com.apple.SecurityAgent", "com.apple.loginwindow",
        "com.mindloom.capture-agent",
    ]

    public static let deniedNames: Set<String> = [
        "system settings", "system preferences", "keychain access", "passwords", "terminal",
        "iterm", "iterm2", "1password", "1password 7", "bitwarden", "lastpass", "dashlane",
        "keeper password manager", "disk utility", "script editor", "automator",
        "activity monitor", "console", "loom capture", "mindloom",
    ]

    static let irreversibleWords = [
        "send", "submit", "post", "publish", "share", "delete", "remove", "erase", "trash",
        "discard", "don't save", "dont save", "replace", "overwrite", "pay", "purchase", "buy",
        "order", "checkout", "check out", "transfer", "book", "reserve", "confirm", "approve",
        "accept", "decline", "sign", "unsubscribe", "cancel subscription", "empty",
    ]

    static let irreversibleKeys: Set<String> = [
        "cmd+delete", "cmd+option+delete", "cmd+shift+delete", "cmd+shift+d", "cmd+return",
        "cmd+shift+return",
    ]

    private static let irreversiblePattern: NSRegularExpression = {
        let words = irreversibleWords.map { NSRegularExpression.escapedPattern(for: $0) }.joined(separator: "|")
        // A word on its own or with a common prefix/suffix ("Resend", "Sending"), not
        // inside another word ("Sendai", "Signature").
        let pattern = "(?<![a-z])(?:re)?(\(words))(?:s|es|d|ed|ing|al)?(?![a-z])"
        return try! NSRegularExpression(pattern: pattern, options: [.caseInsensitive])
    }()

    public static func normalizeApp(_ name: String) -> String {
        var value = name.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        if value.hasSuffix(".app") { value = String(value.dropLast(4)) }
        return value.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    public static func isDenied(name: String, bundleId: String?) -> Bool {
        if let bundleId, deniedBundleIds.contains(bundleId) { return true }
        return deniedNames.contains(normalizeApp(name))
    }

    /// The app is one the person allowed, by any of its names, and not a denied app.
    public static func isAllowed(names: [String], bundleId: String?, allowed: [String]) -> Bool {
        let wanted = Set(allowed.map(normalizeApp))
        let candidates = names.map(normalizeApp).filter { !$0.isEmpty }
        guard !candidates.isEmpty else { return false }
        if candidates.contains(where: { deniedNames.contains($0) }) { return false }
        if let bundleId, deniedBundleIds.contains(bundleId) { return false }
        return candidates.contains(where: wanted.contains)
            || (bundleId.map { wanted.contains($0.lowercased()) } ?? false)
    }

    public static func looksIrreversible(_ label: String?) -> Bool {
        guard let label, !label.isEmpty else { return false }
        let range = NSRange(label.startIndex..., in: label)
        return irreversiblePattern.firstMatch(in: label, range: range) != nil
    }

    public static func isIrreversibleKey(_ combo: KeyCombo) -> Bool {
        irreversibleKeys.contains(combo.normalized)
    }

    /// Return and Enter press a window's default button.
    public static func pressesDefaultButton(_ combo: KeyCombo) -> Bool {
        combo.modifiers.isEmpty && (combo.key == "return" || combo.key == "enter" || combo.key == "keypadenter")
    }
}
