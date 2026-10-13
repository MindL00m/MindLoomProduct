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
    /// Loom web origin used for desktop Google sign-in (Compose UI defaults to :5500).
    var webBase: String
    var orgId: String
    var userId: String
    var accessToken: String
    /// Last signed-in email (display only).
    var email: String
    var allowlist: [AllowlistedApp]
    var idleGapSeconds: TimeInterval

    var isSignedIn: Bool {
        !accessToken.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    /// Production builds bake these into Info.plist via `scripts/package-app.sh`.
    static var bundledWebBase: String {
        plistString("LoomWebBase") ?? "http://localhost:5500"
    }

    static var bundledAPIBase: String {
        let raw = plistString("LoomAPIBase") ?? "http://localhost:8000"
        return resolvedAPIBase(apiBase: raw, webBase: bundledWebBase)
    }

    static var `default`: AgentConfig {
        AgentConfig(
            apiBase: bundledAPIBase,
            webBase: bundledWebBase,
            orgId: "default",
            userId: "desktop-user",
            accessToken: "",
            email: "",
            allowlist: [],
            idleGapSeconds: 90
        )
    }

    /// Absolute API origin. Relative values such as `/api` are resolved against `webBase`.
    static func resolvedAPIBase(apiBase: String, webBase: String) -> String {
        let api = apiBase.trimmingCharacters(in: .whitespacesAndNewlines)
        if api.lowercased().hasPrefix("http://") || api.lowercased().hasPrefix("https://") {
            return api.hasSuffix("/") ? String(api.dropLast()) : api
        }
        let web = webBase.hasSuffix("/") ? String(webBase.dropLast()) : webBase
        if api.isEmpty { return web }
        return api.hasPrefix("/") ? web + api : web + "/" + api
    }

    private static func plistString(_ key: String) -> String? {
        guard let value = Bundle.main.object(forInfoDictionaryKey: key) as? String else { return nil }
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }

    enum CodingKeys: String, CodingKey {
        case apiBase, webBase, orgId, userId, accessToken, email, allowlist, idleGapSeconds
    }

    init(
        apiBase: String,
        webBase: String,
        orgId: String,
        userId: String,
        accessToken: String,
        email: String,
        allowlist: [AllowlistedApp],
        idleGapSeconds: TimeInterval
    ) {
        self.apiBase = apiBase
        self.webBase = webBase
        self.orgId = orgId
        self.userId = userId
        self.accessToken = accessToken
        self.email = email
        self.allowlist = allowlist
        self.idleGapSeconds = idleGapSeconds
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        webBase = try container.decodeIfPresent(String.self, forKey: .webBase) ?? AgentConfig.bundledWebBase
        let rawAPI = try container.decodeIfPresent(String.self, forKey: .apiBase) ?? AgentConfig.bundledAPIBase
        apiBase = AgentConfig.resolvedAPIBase(apiBase: rawAPI, webBase: webBase)
        orgId = try container.decodeIfPresent(String.self, forKey: .orgId) ?? AgentConfig.default.orgId
        userId = try container.decodeIfPresent(String.self, forKey: .userId) ?? AgentConfig.default.userId
        accessToken = try container.decodeIfPresent(String.self, forKey: .accessToken) ?? ""
        email = try container.decodeIfPresent(String.self, forKey: .email) ?? ""
        allowlist = try container.decodeIfPresent([AllowlistedApp].self, forKey: .allowlist) ?? []
        idleGapSeconds = try container.decodeIfPresent(TimeInterval.self, forKey: .idleGapSeconds) ?? 90
    }
}
