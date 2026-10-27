import Foundation
import GuideCore

enum APIClientError: Error, LocalizedError {
    case invalidURL
    case missingAccessToken
    case httpStatus(Int, String)
    case decodeFailed

    var errorDescription: String? {
        switch self {
        case .invalidURL: return "Invalid API base URL."
        case .missingAccessToken:
            return "Not signed in. Use Sign in with Google in the Loom Capture window."
        case .httpStatus(let code, let body):
            if code == 401 {
                return "Session expired or signed for a different API. Sign out, then Sign in with Google in Loom Capture."
            }
            return "API error \(code): \(body)"
        case .decodeFailed: return "Could not decode API response."
        }
    }
}

final class APIClient {
    private let config: AgentConfig
    private let session: URLSession
    private let encoder: JSONEncoder
    private let decoder: JSONDecoder

    init(config: AgentConfig, session: URLSession = .shared) {
        self.config = config
        self.session = session
        self.encoder = JSONEncoder()
        self.encoder.dateEncodingStrategy = .iso8601
        self.encoder.keyEncodingStrategy = .useDefaultKeys
        self.decoder = JSONDecoder()
        self.decoder.dateDecodingStrategy = .iso8601
    }

    func uploadActivitySession(_ payload: ActivitySessionPayload) async throws {
        let url = try endpoint("/captures/activity-sessions")
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        try applyAuth(&request)
        request.httpBody = try encoder.encode(payload)

        let (data, response) = try await session.data(for: request)
        try validate(response: response, data: data)
    }

    @discardableResult
    func analyzeActivitySession(sessionId: String) async throws -> [String: Any] {
        let encodedId = sessionId.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? sessionId
        let url = try endpoint("/captures/activity-sessions/\(encodedId)/analyze")
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        try applyAuth(&request)

        let (data, response) = try await session.data(for: request)
        try validate(response: response, data: data)
        let object = try JSONSerialization.jsonObject(with: data)
        guard let dict = object as? [String: Any] else { throw APIClientError.decodeFailed }
        return dict
    }

    func listGuides() async throws -> [Guide] {
        var request = URLRequest(url: try endpoint("/captures/guides"))
        try applyAuth(&request)
        let (data, response) = try await session.data(for: request)
        try validate(response: response, data: data)
        do {
            return try decoder.decode([Guide].self, from: data)
        } catch {
            throw APIClientError.decodeFailed
        }
    }

    // MARK: - Skill runs

    enum RunOutcome {
        case done
        case waiting(SkillRunWaitReason, detail: String?)
        case failed(String)
    }

    func listSkillRuns(status: SkillRunStatus) async throws -> [SkillRun] {
        try await sendRun("GET", "/captures/skill-runs?status=\(status.rawValue)")
    }

    func getSkillRun(_ runId: String) async throws -> SkillRun {
        try await sendRun("GET", "/captures/skill-runs/\(pathComponent(runId))")
    }

    func claimSkillRun(_ runId: String) async throws -> SkillRun {
        try await sendRun("POST", "/captures/skill-runs/\(pathComponent(runId))/claim")
    }

    @discardableResult
    func advanceSkillRun(_ runId: String, stepIndex: Int, outcome: RunOutcome) async throws -> SkillRun {
        var body: [String: Any] = ["step_index": stepIndex]
        switch outcome {
        case .done:
            body["outcome"] = "done"
        case .waiting(let reason, let detail):
            body["outcome"] = "waiting"
            body["wait_reason"] = reason.rawValue
            if let detail { body["error"] = Self.apiText(detail) }
        case .failed(let error):
            body["outcome"] = "failed"
            body["error"] = Self.apiText(error)
        }
        return try await sendRun(
            "POST",
            "/captures/skill-runs/\(pathComponent(runId))/advance",
            body: try JSONSerialization.data(withJSONObject: body)
        )
    }

    @discardableResult
    func stopSkillRun(_ runId: String) async throws -> SkillRun {
        try await sendRun("POST", "/captures/skill-runs/\(pathComponent(runId))/stop")
    }

    private func sendRun<T: Decodable>(_ method: String, _ path: String, body: Data? = nil) async throws -> T {
        var request = URLRequest(url: try endpoint(path))
        request.httpMethod = method
        if let body {
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = body
        }
        try applyAuth(&request)
        let (data, response) = try await session.data(for: request)
        try validate(response: response, data: data)
        do {
            return try decoder.decode(T.self, from: data)
        } catch {
            throw APIClientError.decodeFailed
        }
    }

    /// The API caps run errors at 500 code points.
    private static func apiText(_ text: String) -> String {
        String(String.UnicodeScalarView(text.unicodeScalars.prefix(500)))
    }

    private func pathComponent(_ value: String) -> String {
        value.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed.subtracting(CharacterSet(charactersIn: "/"))) ?? value
    }

    private func applyAuth(_ request: inout URLRequest) throws {
        let token = config.accessToken.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !token.isEmpty else { throw APIClientError.missingAccessToken }
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
    }

    private func endpoint(_ path: String) throws -> URL {
        let base = config.apiBase.hasSuffix("/") ? String(config.apiBase.dropLast()) : config.apiBase
        guard let url = URL(string: base + path) else { throw APIClientError.invalidURL }
        return url
    }

    private func validate(response: URLResponse, data: Data) throws {
        guard let http = response as? HTTPURLResponse else {
            throw APIClientError.httpStatus(-1, "No HTTP response")
        }
        guard (200..<300).contains(http.statusCode) else {
            let body = String(data: data, encoding: .utf8) ?? ""
            throw APIClientError.httpStatus(http.statusCode, body)
        }
    }
}
