import Foundation

public enum InteractionAction: String, Codable, Hashable {
    case focus
    case click
    case menu
    case fieldFocus = "field_focus"
    case fieldBlur = "field_blur"
    case navigate
}

/// Structured control identity uploaded with task summaries so approved skills can
/// auto-advance desktop guides. Carries the same labels already present in `stepHints`.
public struct StepAnchor: Codable, Equatable, Hashable {
    public var bundleId: String
    public var action: InteractionAction
    public var role: String
    public var identifier: String?
    public var title: String?
    public var windowTitle: String?

    public init(
        bundleId: String,
        action: InteractionAction,
        role: String,
        identifier: String? = nil,
        title: String? = nil,
        windowTitle: String? = nil
    ) {
        self.bundleId = bundleId
        self.action = action
        self.role = role
        self.identifier = identifier
        self.title = title
        self.windowTitle = windowTitle
    }
}

public struct GuideStep: Codable, Equatable {
    public var text: String
    public var anchor: StepAnchor?

    public init(text: String, anchor: StepAnchor? = nil) {
        self.text = text
        self.anchor = anchor
    }
}

/// Payload from `GET /captures/guides` (top-level keys are snake_case; anchors are camelCase).
public struct Guide: Codable, Equatable, Identifiable {
    public var skillId: String
    public var title: String
    public var purpose: String
    public var application: String
    public var bundleIds: [String]
    public var steps: [GuideStep]
    public var warnings: [String]
    public var importantFields: [String]

    public var id: String { skillId }
    public var autoStepCount: Int { steps.filter { $0.anchor != nil }.count }

    enum CodingKeys: String, CodingKey {
        case skillId = "skill_id"
        case title, purpose, application
        case bundleIds = "bundle_ids"
        case steps, warnings
        case importantFields = "important_fields"
    }

    public init(
        skillId: String,
        title: String,
        purpose: String = "",
        application: String = "",
        bundleIds: [String] = [],
        steps: [GuideStep],
        warnings: [String] = [],
        importantFields: [String] = []
    ) {
        self.skillId = skillId
        self.title = title
        self.purpose = purpose
        self.application = application
        self.bundleIds = bundleIds
        self.steps = steps
        self.warnings = warnings
        self.importantFields = importantFields
    }
}
