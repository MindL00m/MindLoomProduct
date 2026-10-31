import Foundation

/// A key and its modifiers, parsed from text like "cmd+shift+s", "⌘↩" or "Return".
public struct KeyCombo: Equatable {
    public enum Modifier: String, CaseIterable, Comparable {
        case cmd, ctrl, option, shift

        var order: Int { Self.allCases.firstIndex(of: self) ?? 0 }
        public static func < (a: Modifier, b: Modifier) -> Bool { a.order < b.order }
    }

    public var modifiers: Set<Modifier>
    /// Lowercase key name, e.g. "s", "return", "left", "f5".
    public var key: String

    public var normalized: String {
        (modifiers.sorted().map(\.rawValue) + [key]).joined(separator: "+")
    }

    /// Virtual key code (Carbon kVK_*); nil for keys this app does not type.
    public var keyCode: UInt16? { Self.keyCodes[key] }

    private static let modifierAliases: [String: Modifier] = [
        "cmd": .cmd, "command": .cmd, "⌘": .cmd, "meta": .cmd, "super": .cmd,
        "ctrl": .ctrl, "control": .ctrl, "ctl": .ctrl, "⌃": .ctrl,
        "option": .option, "opt": .option, "alt": .option, "⌥": .option,
        "shift": .shift, "⇧": .shift,
    ]

    private static let keyAliases: [String: String] = [
        "enter": "return", "↩": "return", "⏎": "return", "esc": "escape", "⎋": "escape",
        "backspace": "delete", "⌫": "delete", "del": "forwarddelete", "⌦": "forwarddelete",
        "forward_delete": "forwarddelete", "arrowleft": "left", "arrowright": "right",
        "arrowup": "up", "arrowdown": "down", "←": "left", "→": "right", "↑": "up", "↓": "down",
        "pgup": "pageup", "pgdown": "pagedown", "page_up": "pageup", "page_down": "pagedown",
        "⇥": "tab", "spacebar": "space", " ": "space",
    ]

    public static let keyCodes: [String: UInt16] = {
        var codes: [String: UInt16] = [
            "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9,
            "b": 11, "q": 12, "w": 13, "e": 14, "r": 15, "y": 16, "t": 17, "1": 18, "2": 19,
            "3": 20, "4": 21, "6": 22, "5": 23, "=": 24, "9": 25, "7": 26, "-": 27, "8": 28,
            "0": 29, "]": 30, "o": 31, "u": 32, "[": 33, "i": 34, "p": 35, "l": 37, "j": 38,
            "'": 39, "k": 40, ";": 41, "\\": 42, ",": 43, "/": 44, "n": 45, "m": 46, ".": 47,
            "`": 50, "return": 36, "tab": 48, "space": 49, "delete": 51, "escape": 53,
            "forwarddelete": 117, "home": 115, "end": 119, "pageup": 116, "pagedown": 121,
            "left": 123, "right": 124, "down": 125, "up": 126, "keypadenter": 76,
        ]
        let functionKeys: [UInt16] = [122, 120, 99, 118, 96, 97, 98, 100, 101, 109, 103, 111]
        for (index, code) in functionKeys.enumerated() { codes["f\(index + 1)"] = code }
        return codes
    }()

    public init(modifiers: Set<Modifier>, key: String) {
        self.modifiers = modifiers
        self.key = key
    }

    /// nil unless there is exactly one non-modifier key.
    public init?(_ text: String) {
        var raw = text.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        guard !raw.isEmpty else { return nil }
        // Symbols written without separators: "⌘⇧s".
        for symbol in ["⌘", "⌃", "⌥", "⇧"] {
            raw = raw.replacingOccurrences(of: symbol, with: "\(symbol)+")
        }
        let parts: [String]
        if raw == "+" || raw == "-" {
            parts = [raw]
        } else {
            // A trailing "+" or "-" after a separator is the key itself: "cmd++", "cmd+-".
            var split = raw.split(whereSeparator: { $0 == "+" || $0 == " " }).map(String.init)
            if raw.hasSuffix("++") { split.append("+") }
            parts = split.flatMap { part -> [String] in
                part.count > 1 && part.contains("-") && part != "-"
                    ? part.split(separator: "-").map(String.init) : [part]
            }
        }
        var modifiers: Set<Modifier> = []
        var keys: [String] = []
        for part in parts where !part.isEmpty {
            if let modifier = Self.modifierAliases[part] {
                modifiers.insert(modifier)
            } else {
                keys.append(Self.keyAliases[part] ?? part)
            }
        }
        guard keys.count == 1, let key = keys.first else { return nil }
        self.modifiers = modifiers
        self.key = key
    }
}
