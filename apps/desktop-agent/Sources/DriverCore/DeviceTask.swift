import Foundation

/// A device task from `/captures/device-tasks`: part of an automation run that the Mac
/// app performs in the apps the person allowed. The API decides each action; this app
/// checks it again (ActionGate), performs it and reports the result.
public enum DeviceTaskStatus: String, Codable {
    case pending
    case running
    case needsApproval = "needs_approval"
    case needsPerson = "needs_person"
    case completed
    case failed
    case stopped

    public var isFinished: Bool { self == .completed || self == .failed || self == .stopped }
    public var isWaitingForPerson: Bool { self == .needsApproval || self == .needsPerson }
}

/// What the task is waiting for: an action to approve, or something for the person to do.
public struct DeviceTaskPending: Codable, Equatable {
    public var instruction: String?
    public var description: String?
    public var reason: String?
    public var tool: String?

    public init(instruction: String? = nil, description: String? = nil, reason: String? = nil, tool: String? = nil) {
        self.instruction = instruction
        self.description = description
        self.reason = reason
        self.tool = tool
    }
}

public struct DeviceTaskLogEntry: Codable, Equatable {
    public var kind: String
    public var status: String
    public var summary: String?
}

public struct DeviceTask: Codable, Equatable {
    public var taskId: String
    public var runId: String
    public var status: DeviceTaskStatus
    public var title: String
    public var allowedApps: [String]
    public var pending: DeviceTaskPending?
    public var summary: String?
    public var error: String?
    public var steps: Int
    public var log: [DeviceTaskLogEntry]
    /// ISO 8601 as the API sends it.
    public var createdAt: String?

    enum CodingKeys: String, CodingKey {
        case taskId = "task_id"
        case runId = "run_id"
        case status, title
        case allowedApps = "allowed_apps"
        case pending, summary, error, steps, log
        case createdAt = "created_at"
    }

    public init(
        taskId: String, runId: String, status: DeviceTaskStatus, title: String = "",
        allowedApps: [String] = [], pending: DeviceTaskPending? = nil, summary: String? = nil,
        error: String? = nil, steps: Int = 0, log: [DeviceTaskLogEntry] = [], createdAt: String? = nil
    ) {
        self.taskId = taskId
        self.runId = runId
        self.status = status
        self.title = title
        self.allowedApps = allowedApps
        self.pending = pending
        self.summary = summary
        self.error = error
        self.steps = steps
        self.log = log
        self.createdAt = createdAt
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        taskId = try c.decode(String.self, forKey: .taskId)
        runId = try c.decodeIfPresent(String.self, forKey: .runId) ?? ""
        status = try c.decode(DeviceTaskStatus.self, forKey: .status)
        title = try c.decodeIfPresent(String.self, forKey: .title) ?? ""
        allowedApps = try c.decodeIfPresent([String].self, forKey: .allowedApps) ?? []
        pending = try c.decodeIfPresent(DeviceTaskPending.self, forKey: .pending)
        summary = try c.decodeIfPresent(String.self, forKey: .summary)
        error = try c.decodeIfPresent(String.self, forKey: .error)
        steps = try c.decodeIfPresent(Int.self, forKey: .steps) ?? 0
        log = (try? c.decodeIfPresent([DeviceTaskLogEntry].self, forKey: .log)) ?? []
        createdAt = try c.decodeIfPresent(String.self, forKey: .createdAt)
    }

    public var createdDate: Date? {
        guard let createdAt else { return nil }
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = formatter.date(from: createdAt) { return date }
        formatter.formatOptions = [.withInternetDateTime]
        return formatter.date(from: createdAt)
    }
}

/// A JSON value in an action's arguments.
public enum JSONValue: Codable, Equatable {
    case string(String)
    case number(Double)
    case bool(Bool)
    case array([JSONValue])
    case object([String: JSONValue])
    case null

    public init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() { self = .null }
        else if let b = try? c.decode(Bool.self) { self = .bool(b) }
        else if let n = try? c.decode(Double.self) { self = .number(n) }
        else if let s = try? c.decode(String.self) { self = .string(s) }
        else if let a = try? c.decode([JSONValue].self) { self = .array(a) }
        else { self = .object(try c.decode([String: JSONValue].self)) }
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self {
        case .string(let s): try c.encode(s)
        case .number(let n): try c.encode(n)
        case .bool(let b): try c.encode(b)
        case .array(let a): try c.encode(a)
        case .object(let o): try c.encode(o)
        case .null: try c.encodeNil()
        }
    }

    public var string: String? {
        switch self {
        case .string(let s): return s
        case .number(let n): return n == n.rounded() ? String(Int(n)) : String(n)
        case .bool(let b): return b ? "true" : "false"
        default: return nil
        }
    }

    public var number: Double? {
        switch self {
        case .number(let n): return n
        case .string(let s): return Double(s)
        default: return nil
        }
    }

    public var bool: Bool? {
        if case .bool(let b) = self { return b }
        return nil
    }

    public var strings: [String] {
        if case .array(let items) = self { return items.compactMap(\.string) }
        return []
    }
}

public enum DriverTool: String, Codable {
    case openApp = "open_app"
    case focusWindow = "focus_window"
    case press
    case setValue = "set_value"
    case typeText = "type_text"
    case key
    case selectMenu = "select_menu"
    case scroll
    case wait
}

/// One action the API asks for. `approved` is true only when the person approved it (or
/// auto-approve is on for their own skill); `expectLabel` is the label the target had
/// on the screen the action was chosen from.
public struct DeviceAction: Codable, Equatable {
    public var id: String
    public var tool: String
    public var args: [String: JSONValue]
    public var description: String
    public var approved: Bool
    public var expectLabel: String?

    enum CodingKeys: String, CodingKey {
        case id, tool, args, description, approved
        case expectLabel = "expect_label"
    }

    public init(
        id: String, tool: String, args: [String: JSONValue] = [:], description: String = "",
        approved: Bool = false, expectLabel: String? = nil
    ) {
        self.id = id
        self.tool = tool
        self.args = args
        self.description = description
        self.approved = approved
        self.expectLabel = expectLabel
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        tool = try c.decode(String.self, forKey: .tool)
        args = try c.decodeIfPresent([String: JSONValue].self, forKey: .args) ?? [:]
        description = try c.decodeIfPresent(String.self, forKey: .description) ?? ""
        approved = try c.decodeIfPresent(Bool.self, forKey: .approved) ?? false
        expectLabel = try c.decodeIfPresent(String.self, forKey: .expectLabel)
    }

    public var driverTool: DriverTool? { DriverTool(rawValue: tool) }
    public func string(_ key: String) -> String? { args[key]?.string }
    public func flag(_ key: String) -> Bool { args[key]?.bool ?? false }
    public var ref: String? { string("ref").flatMap { $0.isEmpty ? nil : $0 } }
}

/// The API's answer to a step: what to do next, or why to wait.
public struct DeviceStepResponse: Codable, Equatable {
    public var status: DeviceTaskStatus
    public var action: DeviceAction?
    public var message: String?
    public var task: DeviceTask?
}

/// Why this app refused or couldn't perform an action (matches the API's `blocked`).
public enum ActionBlock: String, Codable, Equatable {
    case needsApproval = "needs_approval"
    case secureField = "secure_field"
    case appNotAllowed = "app_not_allowed"
    case declinedForeground = "declined_foreground"
}

public struct DeviceActionResult: Codable, Equatable {
    public var actionId: String
    public var ok: Bool
    public var detail: String
    public var changed: Bool?
    public var blocked: ActionBlock?

    enum CodingKeys: String, CodingKey {
        case actionId = "action_id"
        case ok, detail, changed, blocked
    }

    public init(actionId: String, ok: Bool, detail: String, changed: Bool? = nil, blocked: ActionBlock? = nil) {
        self.actionId = actionId
        self.ok = ok
        self.detail = String(detail.prefix(2000))
        self.changed = changed
        self.blocked = blocked
    }
}

/// One element the screen outline lists, for the API's own checks.
public struct ElementSummary: Codable, Equatable {
    public var ref: String
    public var role: String
    public var label: String
    public var secure: Bool
    public var focused: Bool

    public init(ref: String, role: String, label: String, secure: Bool = false, focused: Bool = false) {
        self.ref = ref
        self.role = role
        self.label = label
        self.secure = secure
        self.focused = focused
    }
}

/// What the Mac sends each step: the app it is working in, as an outline.
public struct DeviceObservation: Codable, Equatable {
    public var app: String
    public var bundleId: String
    public var text: String
    public var elements: [ElementSummary]
    public var runningApps: [String]

    enum CodingKeys: String, CodingKey {
        case app
        case bundleId = "bundle_id"
        case text, elements
        case runningApps = "running_apps"
    }

    public init(app: String, bundleId: String, text: String, elements: [ElementSummary], runningApps: [String]) {
        self.app = app
        self.bundleId = bundleId
        self.text = text
        self.elements = elements
        self.runningApps = runningApps
    }
}
