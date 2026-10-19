import Foundation

public enum GuideMatcher {
    /// Whether a live action satisfies a step's anchor. Identifiers win when both sides
    /// have one; otherwise fall back to normalized titles (or window titles for navigation).
    public static func matches(expected: StepAnchor, observed: StepAnchor) -> Bool {
        guard expected.bundleId == observed.bundleId else { return false }
        guard actionsCompatible(expected.action, observed.action) else { return false }

        if expected.action == .navigate {
            let want = normalize(expected.windowTitle ?? expected.title)
            let got = normalize(observed.windowTitle ?? observed.title)
            return !want.isEmpty && !got.isEmpty && got.contains(want)
        }

        if !expected.role.isEmpty, !observed.role.isEmpty, expected.role != observed.role {
            return false
        }
        let wantId = normalize(expected.identifier)
        let gotId = normalize(observed.identifier)
        if !wantId.isEmpty, !gotId.isEmpty {
            return wantId == gotId
        }
        let wantTitle = normalize(expected.title)
        return !wantTitle.isEmpty && wantTitle == normalize(observed.title)
    }

    static func actionsCompatible(_ expected: InteractionAction, _ observed: InteractionAction) -> Bool {
        switch expected {
        case .menu: return observed == .menu || observed == .click
        default: return expected == observed
        }
    }

    static func normalize(_ value: String?) -> String {
        guard let value else { return "" }
        return value
            .lowercased()
            .split(whereSeparator: { $0.isWhitespace })
            .joined(separator: " ")
            .trimmingCharacters(in: CharacterSet(charactersIn: "…:."))
    }
}

/// Picks the skill-level warnings and fields worth showing next to one step.
public enum GuideHints {
    public static func fields(_ fields: [String], for step: GuideStep) -> [String] {
        let haystack = searchText(step)
        return fields.filter { field in
            let label = normalizedLabel(field)
            return !label.isEmpty && haystack.contains(label)
        }
    }

    public static func warnings(_ warnings: [String], for step: GuideStep) -> [String] {
        let stepWords = significantWords(searchText(step))
        guard !stepWords.isEmpty else { return [] }
        return warnings.filter { !significantWords($0.lowercased()).isDisjoint(with: stepWords) }
    }

    static func searchText(_ step: GuideStep) -> String {
        [step.text, step.anchor?.title, step.anchor?.windowTitle]
            .compactMap { $0?.lowercased() }
            .joined(separator: " ")
    }

    /// Heuristic drafts store fields as `Label (AXRole)`; match on the label only.
    static func normalizedLabel(_ field: String) -> String {
        let base = field.split(separator: "(", maxSplits: 1).first.map(String.init) ?? field
        return GuideMatcher.normalize(base)
    }

    static func significantWords(_ text: String) -> Set<String> {
        Set(
            text.split(whereSeparator: { !$0.isLetter && !$0.isNumber })
                .map(String.init)
                .filter { $0.count >= 5 }
        )
    }
}

/// Step state for one walkthrough. Pure value type so it can be checked without AppKit.
public struct GuideSession: Equatable {
    public static let lookAhead = 2

    public let guide: Guide
    /// When false (app not allowlisted), observed actions are ignored and only Next/Back move.
    public let autoAdvance: Bool
    public private(set) var index: Int = 0
    public private(set) var lastAdvanceWasAuto = false

    public init(guide: Guide, autoAdvance: Bool) {
        self.guide = guide
        self.autoAdvance = autoAdvance
    }

    public var isFinished: Bool { index >= guide.steps.count }
    public var currentStep: GuideStep? { isFinished ? nil : guide.steps[index] }
    public var stepCount: Int { guide.steps.count }

    /// Returns true when the observed action completed the current step or one up to
    /// `lookAhead` steps further (the user skipped or reordered steps).
    @discardableResult
    public mutating func observe(_ observed: StepAnchor) -> Bool {
        guard autoAdvance, !isFinished else { return false }
        let last = min(guide.steps.count - 1, index + Self.lookAhead)
        for position in index...last {
            guard let anchor = guide.steps[position].anchor else { continue }
            if GuideMatcher.matches(expected: anchor, observed: observed) {
                index = position + 1
                lastAdvanceWasAuto = true
                return true
            }
        }
        return false
    }

    public mutating func next() {
        guard !isFinished else { return }
        index += 1
        lastAdvanceWasAuto = false
    }

    public mutating func back() {
        guard index > 0 else { return }
        index -= 1
        lastAdvanceWasAuto = false
    }
}
