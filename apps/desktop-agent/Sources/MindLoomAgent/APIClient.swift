import Foundation

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
