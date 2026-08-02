import Foundation

enum CaptureStatus: String {
    case idle
    case capturing
    case paused
    case needsPermission
}

enum InteractionAction: String, Codable {
    case focus
    case click
    case menu
    case fieldFocus = "field_focus"
    case fieldBlur = "field_blur"
    case navigate
}

struct ControlIdentity: Codable, Equatable {
    var role: String
    var identifier: String?
    var title: String?
    var path: [String]
}

struct LocalInteractionEvent: Codable {
    var ts: Date
    var bundleId: String
    var appName: String
    var windowTitle: String
    var action: InteractionAction
    var control: ControlIdentity
    var durationMs: Int?
}

struct FieldInteractionSummary: Codable {
    var role: String
    var label: String
    var durationMs: Int

    enum CodingKeys: String, CodingKey {
        case role
        case label
        case durationMs
    }
}

struct TaskStats: Codable {
    var eventCount: Int
    var activeMs: Int
}

struct TaskSummary: Codable {
    var taskId: String
    var startedAt: Date
    var endedAt: Date
    var primaryApp: String
    var apps: [String]
    var stepHints: [String]
    var fieldInteractions: [FieldInteractionSummary]
    var stats: TaskStats
}

struct ActivitySessionPayload: Codable {
    var sessionId: String
    var orgId: String
    var userId: String
    var source: String
    var startedAt: Date
    var endedAt: Date
    var tasks: [TaskSummary]
    var note: String
}

struct AllowlistedApp: Codable, Equatable, Identifiable {
    var bundleId: String
    var displayName: String

    var id: String { bundleId }
}

struct AgentConfig: Codable {
    var apiBase: String
    var orgId: String
    var userId: String
    var allowlist: [AllowlistedApp]
    var idleGapSeconds: TimeInterval

    static let `default` = AgentConfig(
        apiBase: "http://localhost:8000",
        orgId: "default",
        userId: "desktop-user",
        allowlist: [],
        idleGapSeconds: 90
    )
}
