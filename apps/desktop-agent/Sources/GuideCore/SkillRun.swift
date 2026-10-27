import Foundation

public enum SkillRunStatus: String, Codable {
    case pending
    case running
    case waiting
    case completed
    case stopped
    case failed

    public var isFinished: Bool { self == .completed || self == .stopped || self == .failed }
}

public enum SkillRunWaitReason: String, Codable, Equatable {
    case noAnchor = "no_anchor"
    case needsValue = "needs_value"
    case actionFailed = "action_failed"
    case agentOffline = "agent_offline"
}

/// Payload from `/captures/skill-runs` (snake_case keys).
public struct SkillRun: Codable, Equatable {
    public var runId: String
    public var skillId: String
    public var status: SkillRunStatus
    public var stepIndex: Int
    public var waitReason: SkillRunWaitReason?
    public var error: String?
    /// ISO 8601 as the API sends it (fractional seconds included).
    public var createdAt: String?
    /// Present on `GET /captures/skill-runs/{id}`; nil when the skill can no longer run.
    public var guide: Guide?

    enum CodingKeys: String, CodingKey {
        case runId = "run_id"
        case skillId = "skill_id"
        case status
        case stepIndex = "step_index"
        case waitReason = "wait_reason"
        case error
        case createdAt = "created_at"
        case guide
    }

    public var createdDate: Date? {
        guard let createdAt else { return nil }
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = formatter.date(from: createdAt) { return date }
        formatter.formatOptions = [.withInternetDateTime]
        return formatter.date(from: createdAt)
    }

    public init(
        runId: String,
        skillId: String,
        status: SkillRunStatus,
        stepIndex: Int = 0,
        waitReason: SkillRunWaitReason? = nil,
        error: String? = nil,
        createdAt: String? = nil,
        guide: Guide? = nil
    ) {
        self.runId = runId
        self.skillId = skillId
        self.status = status
        self.stepIndex = stepIndex
        self.waitReason = waitReason
        self.error = error
        self.createdAt = createdAt
        self.guide = guide
    }
}

/// What a run does with one step. The run follows the stored anchor and never picks
/// a different control; anything it cannot perform waits for the person.
public enum RunDecision: Equatable {
    case performAccessibility(StepAnchor)
    case performBrowser(BrowserAnchor)
    case wait(SkillRunWaitReason)

    public static func decide(_ step: GuideStep) -> RunDecision {
        if let anchor = step.anchor {
            switch anchor.action {
            case .click, .menu, .focus, .navigate:
                return .performAccessibility(anchor)
            case .fieldFocus, .fieldBlur:
                return .wait(.needsValue)
            }
        }
        if let browser = step.browserAnchor {
            return browser.needsValue ? .wait(.needsValue) : .performBrowser(browser)
        }
        return .wait(.noAnchor)
    }
}
