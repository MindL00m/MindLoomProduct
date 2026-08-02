import Foundation

enum Redaction {
    private static let sensitiveRoles: Set<String> = [
        "AXSecureTextField",
        "AXSecureTextArea",
    ]

    private static let textRoles: Set<String> = [
        "AXTextField",
        "AXTextArea",
        "AXSearchField",
        "AXComboBox",
    ]

    private static let identifierPatterns: [NSRegularExpression] = {
        let patterns = [
            #"(?i)password"#,
            #"(?i)passcode"#,
            #"(?i)secret"#,
            #"(?i)\bssn\b"#,
            #"(?i)social.?security"#,
            #"(?i)account.?number"#,
            #"(?i)routing.?number"#,
            #"(?i)credit.?card"#,
            #"(?i)\bcvv\b"#,
            #"(?i)email"#,
            #"(?i)phone"#,
            #"(?i)api.?key"#,
            #"(?i)access.?token"#,
            #"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}"#,
            #"\b\d{3}-\d{2}-\d{4}\b"#,
            #"\b(?:\d[ -]*?){13,19}\b"#,
        ]
        return patterns.compactMap { try? NSRegularExpression(pattern: $0) }
    }()

    static func isSensitiveRole(_ role: String) -> Bool {
        sensitiveRoles.contains(role)
    }

    static func isTextRole(_ role: String) -> Bool {
        textRoles.contains(role) || isSensitiveRole(role)
    }

    /// Never reads AXValue; only sanitizes titles/labels for local event records.
    static func sanitizeTitle(_ title: String?) -> String? {
        guard let title, !title.isEmpty else { return title }
        let truncated = String(title.prefix(80))
        for regex in identifierPatterns {
            let range = NSRange(truncated.startIndex..., in: truncated)
            if regex.firstMatch(in: truncated, options: [], range: range) != nil {
                return "[redacted]"
            }
        }
        return truncated
    }

    static func sanitizeWindowTitle(_ title: String) -> String {
        sanitizeTitle(title) ?? ""
    }

    static func shouldDropControl(role: String, title: String?) -> Bool {
        if isSensitiveRole(role) {
            return true
        }
        if let title, sanitizeTitle(title) == "[redacted]", isTextRole(role) {
            // Still record interaction metadata with redacted label — do not drop entirely.
            return false
        }
        return false
    }
}
