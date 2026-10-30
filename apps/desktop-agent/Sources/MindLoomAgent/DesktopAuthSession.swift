import AppKit
import Darwin
import Foundation
import Security

struct DesktopAuthResult {
    var accessToken: String
    var orgId: String
    var userId: String
    var email: String
}

enum DesktopAuthError: Error, LocalizedError, Equatable {
    case invalidWebBase
    case bindFailed
    case timedOut
    case cancelled
    case invalidCallback

    var errorDescription: String? {
        switch self {
        case .invalidWebBase: return "Invalid web app URL in agent config (webBase)."
        case .bindFailed: return "Could not start local sign-in listener on 127.0.0.1."
        case .timedOut: return "Sign-in timed out. Try again from the agent."
        case .cancelled: return "Sign-in was cancelled."
        case .invalidCallback: return "Sign-in callback was incomplete."
        }
    }
}

/// One-shot loopback HTTP server on 127.0.0.1 that receives the Loom JWT from the web page.
final class DesktopAuthSession {
    private var serverFD: Int32 = -1
    private var acceptSource: DispatchSourceRead?
    private var continuation: CheckedContinuation<DesktopAuthResult, Error>?
    private let queue = DispatchQueue(label: "com.mindloom.desktop-auth")
    private var finished = false
    /// One-time value the web page must echo back, so only the page this app opened
    /// can complete sign-in (no other web page or local process can inject a token).
    private var expectedState = ""
    /// Only the MindLoom web app may call the callback from a browser.
    private var allowedOrigin = ""

    func signIn(webBase: String, apiBase: String, timeoutSeconds: TimeInterval = 180) async throws -> DesktopAuthResult {
        let port = try startListener()
        defer { stopListener() }

        let base = webBase.hasSuffix("/") ? String(webBase.dropLast()) : webBase
        guard var urlComponents = URLComponents(string: base + "/desktop-auth") else {
            throw DesktopAuthError.invalidWebBase
        }
        var random = [UInt8](repeating: 0, count: 32)
        guard SecRandomCopyBytes(kSecRandomDefault, random.count, &random) == errSecSuccess else {
            throw DesktopAuthError.invalidWebBase
        }
        expectedState = Data(random).base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
        if let web = URLComponents(string: base), let scheme = web.scheme, let host = web.host {
            allowedOrigin = "\(scheme)://\(host)" + (web.port.map { ":\($0)" } ?? "")
        }
        // The page always uses its own API; the token never travels in a URL.
        urlComponents.queryItems = [
            URLQueryItem(name: "port", value: String(port)),
            URLQueryItem(name: "state", value: expectedState),
        ]
        guard let url = urlComponents.url else { throw DesktopAuthError.invalidWebBase }
        NSLog("[MindLoom] Desktop auth listening on http://127.0.0.1:%u — opening %@", port, url.absoluteString)
        NSWorkspace.shared.open(url)

        return try await withCheckedThrowingContinuation { (cont: CheckedContinuation<DesktopAuthResult, Error>) in
            self.continuation = cont
            self.queue.asyncAfter(deadline: .now() + timeoutSeconds) { [weak self] in
                self?.fail(.timedOut)
            }
        }
    }

    func cancel() {
        fail(.cancelled)
    }

    private func startListener() throws -> UInt16 {
        let fd = socket(AF_INET, SOCK_STREAM, 0)
        guard fd >= 0 else { throw DesktopAuthError.bindFailed }

        var reuse: Int32 = 1
        _ = setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &reuse, socklen_t(MemoryLayout.size(ofValue: reuse)))

        var addr = sockaddr_in()
        addr.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_port = in_port_t(0).bigEndian
        addr.sin_addr = in_addr(s_addr: inet_addr("127.0.0.1"))

        let bindResult = withUnsafePointer(to: &addr) { ptr -> Int32 in
            ptr.withMemoryRebound(to: sockaddr.self, capacity: 1) { sockPtr in
                bind(fd, sockPtr, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }
        guard bindResult == 0 else {
            close(fd)
            throw DesktopAuthError.bindFailed
        }
        guard listen(fd, 8) == 0 else {
            close(fd)
            throw DesktopAuthError.bindFailed
        }

        var bound = sockaddr_in()
        var len = socklen_t(MemoryLayout<sockaddr_in>.size)
        let nameResult = withUnsafeMutablePointer(to: &bound) { ptr -> Int32 in
            ptr.withMemoryRebound(to: sockaddr.self, capacity: 1) { sockPtr in
                getsockname(fd, sockPtr, &len)
            }
        }
        guard nameResult == 0 else {
            close(fd)
            throw DesktopAuthError.bindFailed
        }
        let port = UInt16(bigEndian: bound.sin_port)
        guard port > 0 else {
            close(fd)
            throw DesktopAuthError.bindFailed
        }

        serverFD = fd
        let source = DispatchSource.makeReadSource(fileDescriptor: fd, queue: queue)
        source.setEventHandler { [weak self] in
            self?.acceptClient()
        }
        source.setCancelHandler {
            // fd closed in stopListener
        }
        source.resume()
        acceptSource = source
        return port
    }

    private func stopListener() {
        acceptSource?.cancel()
        acceptSource = nil
        if serverFD >= 0 {
            close(serverFD)
            serverFD = -1
        }
    }

    private func acceptClient() {
        guard serverFD >= 0 else { return }
        var addr = sockaddr_in()
        var len = socklen_t(MemoryLayout<sockaddr_in>.size)
        let client = withUnsafeMutablePointer(to: &addr) { ptr -> Int32 in
            ptr.withMemoryRebound(to: sockaddr.self, capacity: 1) { sockPtr in
                accept(serverFD, sockPtr, &len)
            }
        }
        guard client >= 0 else { return }
        queue.async { [weak self] in
            self?.handle(clientFD: client)
        }
    }

    private func fail(_ error: DesktopAuthError) {
        guard !finished else { return }
        finished = true
        guard let continuation else { return }
        self.continuation = nil
        continuation.resume(throwing: error)
    }

    private func succeed(_ result: DesktopAuthResult) {
        guard !finished else { return }
        finished = true
        guard let continuation else { return }
        self.continuation = nil
        continuation.resume(returning: result)
    }

    private func handle(clientFD: Int32) {
        defer { close(clientFD) }
        var buffer = Data()
        var chunk = [UInt8](repeating: 0, count: 64 * 1024)
        // Read headers (and maybe body start).
        while true {
            let n = read(clientFD, &chunk, chunk.count)
            if n < 0 {
                if errno == EINTR { continue }
                return
            }
            if n == 0 { break }
            buffer.append(chunk, count: n)
            if buffer.range(of: Data("\r\n\r\n".utf8)) != nil { break }
            if buffer.count > 1024 * 1024 { return }
        }

        guard let headerEnd = buffer.range(of: Data("\r\n\r\n".utf8)) else {
            respond(clientFD: clientFD, status: 400, body: "Bad request")
            return
        }
        let headerData = buffer.subdata(in: buffer.startIndex..<headerEnd.lowerBound)
        let headerText = String(data: headerData, encoding: .utf8) ?? ""
        var body = buffer.subdata(in: headerEnd.upperBound..<buffer.endIndex)

        let lines = headerText.split(separator: "\r\n", omittingEmptySubsequences: false)
        guard let requestLine = lines.first else {
            respond(clientFD: clientFD, status: 400, body: "Bad request")
            return
        }
        let parts = requestLine.split(separator: " ")
        guard parts.count >= 2 else {
            respond(clientFD: clientFD, status: 400, body: "Bad request")
            return
        }
        let method = String(parts[0]).uppercased()
        let pathAndQuery = String(parts[1])

        let contentLength = lines.dropFirst().compactMap { line -> Int? in
            let s = String(line)
            guard s.lowercased().hasPrefix("content-length:") else { return nil }
            return Int(s.dropFirst("content-length:".count).trimmingCharacters(in: .whitespaces))
        }.first ?? 0

        while body.count < contentLength {
            let n = read(clientFD, &chunk, chunk.count)
            if n <= 0 { break }
            body.append(chunk, count: n)
        }
        if contentLength > 0, body.count > contentLength {
            body = body.prefix(contentLength)
        }

        if method == "OPTIONS" {
            respond(clientFD: clientFD, status: 204, body: "", extraHeaders: corsHeaders())
            return
        }

        guard pathAndQuery == "/callback" || pathAndQuery.hasPrefix("/callback?") else {
            respond(clientFD: clientFD, status: 404, body: "Not found", extraHeaders: corsHeaders())
            return
        }

        guard method == "POST" else {
            respond(clientFD: clientFD, status: 405, body: "Method not allowed", extraHeaders: corsHeaders())
            return
        }
        let origin = lines.dropFirst().compactMap { line -> String? in
            let s = String(line)
            guard s.lowercased().hasPrefix("origin:") else { return nil }
            return s.dropFirst("origin:".count).trimmingCharacters(in: .whitespaces)
        }.first
        if let origin, !allowedOrigin.isEmpty, origin != allowedOrigin {
            respond(clientFD: clientFD, status: 403, body: "Forbidden origin")
            return
        }
        let fields = parseJSONBody(body) ?? [:]

        // Wrong or missing values get a 400 but do not end the sign-in, so a stray
        // request cannot cancel it either.
        guard
            constantTimeEquals(fields["state"] ?? "", expectedState),
            let token = fields["access_token"]?.trimmingCharacters(in: .whitespacesAndNewlines),
            !token.isEmpty,
            let orgId = fields["org_id"], !orgId.isEmpty,
            let userId = fields["user_id"], !userId.isEmpty
        else {
            respond(clientFD: clientFD, status: 400, body: "Invalid sign-in", extraHeaders: corsHeaders())
            return
        }
        let email = fields["email"] ?? ""
        let html = """
        <!doctype html><html><body style="font-family:system-ui;padding:40px;text-align:center">
        <h2>Signed in to Loom Capture</h2>
        <p>You can close this tab and return to the agent.</p>
        </body></html>
        """
        respond(
            clientFD: clientFD,
            status: 200,
            body: html,
            contentType: "text/html; charset=utf-8",
            extraHeaders: corsHeaders()
        )
        NSLog("[MindLoom] Desktop auth received token for org=%@ user=%@", orgId, userId)
        succeed(DesktopAuthResult(accessToken: token, orgId: orgId, userId: userId, email: email))
    }

    private func constantTimeEquals(_ a: String, _ b: String) -> Bool {
        let x = Array(a.utf8), y = Array(b.utf8)
        guard !y.isEmpty, x.count == y.count else { return false }
        var diff: UInt8 = 0
        for i in 0..<x.count { diff |= x[i] ^ y[i] }
        return diff == 0
    }

    private func corsHeaders() -> [String] {
        [
            "Access-Control-Allow-Origin: \(allowedOrigin.isEmpty ? "null" : allowedOrigin)",
            "Vary: Origin",
            "Access-Control-Allow-Methods: POST, OPTIONS",
            "Access-Control-Allow-Headers: Content-Type",
            "Access-Control-Max-Age: 600",
        ]
    }

    private func parseQuery(_ pathAndQuery: String) -> [String: String] {
        guard let components = URLComponents(string: "http://127.0.0.1\(pathAndQuery)") else {
            return [:]
        }
        var out: [String: String] = [:]
        for item in components.queryItems ?? [] {
            if let value = item.value {
                out[item.name] = value
            }
        }
        return out
    }

    private func parseJSONBody(_ data: Data) -> [String: String]? {
        guard
            let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else { return nil }
        var out: [String: String] = [:]
        for (key, value) in obj {
            if let s = value as? String {
                out[key] = s
            } else if let n = value as? NSNumber {
                out[key] = n.stringValue
            }
        }
        return out
    }

    private func respond(
        clientFD: Int32,
        status: Int,
        body: String,
        contentType: String = "text/plain; charset=utf-8",
        extraHeaders: [String] = []
    ) {
        let reason: String
        switch status {
        case 200: reason = "OK"
        case 204: reason = "No Content"
        case 400: reason = "Bad Request"
        case 404: reason = "Not Found"
        case 405: reason = "Method Not Allowed"
        default: reason = "Error"
        }
        let data = Data(body.utf8)
        var header = "HTTP/1.1 \(status) \(reason)\r\n"
        header += "Content-Type: \(contentType)\r\n"
        header += "Content-Length: \(data.count)\r\n"
        header += "Connection: close\r\n"
        for line in extraHeaders {
            header += "\(line)\r\n"
        }
        header += "\r\n"
        var payload = Data(header.utf8)
        payload.append(data)
        payload.withUnsafeBytes { raw in
            guard let base = raw.bindMemory(to: UInt8.self).baseAddress else { return }
            var sent = 0
            while sent < payload.count {
                let n = write(clientFD, base + sent, payload.count - sent)
                if n <= 0 { break }
                sent += n
            }
        }
    }
}
