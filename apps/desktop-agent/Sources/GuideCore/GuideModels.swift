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

/// Page control identity recorded by the Chrome extension. The extension performs these;
/// Accessibility anchors are for native controls only.
public struct BrowserAnchor: Codable, Equatable, Hashable {
    /// Origin and path only.
    public var url: String
    public var role: String
    /// Accessible name or link text; never an input's value.
    public var name: String
    public var href: String?
    public var selector: String
    /// The control takes typed or chosen input, which a run never provides.
    public var needsValue: Bool

    public init(
        url: String,
        role: String = "",
        name: String = "",
        href: String? = nil,
        selector: String = "",
        needsValue: Bool = false
    ) {
        self.url = url
        self.role = role
        self.name = name
        self.href = href
        self.selector = selector
        self.needsValue = needsValue
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        url = try container.decode(String.self, forKey: .url)
        role = try container.decodeIfPresent(String.self, forKey: .role) ?? ""
        name = try container.decodeIfPresent(String.self, forKey: .name) ?? ""
        href = try container.decodeIfPresent(String.self, forKey: .href)
        selector = try container.decodeIfPresent(String.self, forKey: .selector) ?? ""
        needsValue = try container.decodeIfPresent(Bool.self, forKey: .needsValue) ?? false
    }
}

/// One step: a native `anchor`, a page `browserAnchor`, or neither (manual Next).
public struct GuideStep: Codable, Equatable {
    public var text: String
    public var anchor: StepAnchor?
    public var browserAnchor: BrowserAnchor?

    enum CodingKeys: String, CodingKey {
        case text, anchor
        case browserAnchor = "browser_anchor"
    }

    public init(text: String, anchor: StepAnchor? = nil, browserAnchor: BrowserAnchor? = nil) {
        self.text = text
        self.anchor = anchor
        self.browserAnchor = browserAnchor
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
