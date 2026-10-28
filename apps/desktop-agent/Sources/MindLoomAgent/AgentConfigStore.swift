import Foundation

enum AgentConfigStore {
    private static var configURL: URL {
        let home = FileManager.default.homeDirectoryForCurrentUser
        return home.appendingPathComponent(".mindloom/agent.json")
    }

    private static var eventsDirectoryURL: URL {
        let home = FileManager.default.homeDirectoryForCurrentUser
        return home.appendingPathComponent(".mindloom/events")
    }

    static func load() -> AgentConfig {
        let url = configURL
        guard FileManager.default.fileExists(atPath: url.path) else {
            save(.default)
            return .default
        }
        do {
            let data = try Data(contentsOf: url)
            let decoder = JSONDecoder()
            let loaded = try decoder.decode(AgentConfig.self, from: data)
            return migrateLocalhostDefaultsIfNeeded(loaded)
        } catch {
            NSLog("MindLoomAgent: failed to load config, using defaults: %@", "\(error)")
            return .default
        }
    }

    /// Keep this binary's baked hosts. A production-packaged app upgrades leftover
    /// localhost URLs; a local-packaged app must not keep a Railway apiBase from
    /// ~/.mindloom/agent.json or uploads never hit docker compose.
    private static func migrateLocalhostDefaultsIfNeeded(_ config: AgentConfig) -> AgentConfig {
        var next = config
        next.apiBase = AgentConfig.resolvedAPIBase(apiBase: next.apiBase, webBase: next.webBase)
        let bundled = AgentConfig.default
        // Installed from a website: always use that site. Switching sites signs
        // the user out, since a token from one API is not valid on another.
        if AgentConfig.installOverride != nil {
            if normalized(next.webBase) != normalized(bundled.webBase)
                || normalized(next.apiBase) != normalized(bundled.apiBase) {
                next.apiBase = bundled.apiBase
                next.webBase = bundled.webBase
                next.accessToken = ""
                next.orgId = "default"
                next.userId = "desktop-user"
                next.email = ""
                save(next)
            }
            return next
        }
        let savedIsLocal = isLocalDevHost(next.webBase) && isLocalDevHost(next.apiBase)
        let bundledIsLocal = isLocalDevHost(bundled.webBase)
        if !savedIsLocal && bundledIsLocal {
            next.apiBase = bundled.apiBase
            next.webBase = bundled.webBase
            next.accessToken = ""
            next.orgId = "default"
            next.userId = "desktop-user"
            next.email = ""
            save(next)
        } else if savedIsLocal && !bundledIsLocal {
            next.apiBase = bundled.apiBase
            next.webBase = bundled.webBase
            save(next)
        }
        return next
    }

    private static func normalized(_ value: String) -> String {
        let lower = value.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        return lower.hasSuffix("/") ? String(lower.dropLast()) : lower
    }

    private static func isLocalDevHost(_ value: String) -> Bool {
        let lower = value.lowercased()
        return lower.contains("localhost") || lower.contains("127.0.0.1")
    }

    static func save(_ config: AgentConfig) {
        do {
            let dir = configURL.deletingLastPathComponent()
            try FileManager.default.createDirectory(
                at: dir, withIntermediateDirectories: true,
                attributes: [.posixPermissions: 0o700]
            )
            try? FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: dir.path)
            let encoder = JSONEncoder()
            encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
            let data = try encoder.encode(config)
            try data.write(to: configURL, options: .atomic)
            // The config holds the sign-in token: readable by this user only.
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: configURL.path)
        } catch {
            NSLog("MindLoomAgent: failed to save config: %@", "\(error)")
        }
    }

    /// Local event logs are kept this many days, then deleted.
    static let eventRetentionDays = 14

    static func pruneOldEvents() {
        let cutoff = Date().addingTimeInterval(-Double(eventRetentionDays) * 86_400)
        let files = (try? FileManager.default.contentsOfDirectory(
            at: eventsDirectoryURL, includingPropertiesForKeys: [.contentModificationDateKey]
        )) ?? []
        for file in files where file.lastPathComponent.hasPrefix("events-") {
            let modified = (try? file.resourceValues(forKeys: [.contentModificationDateKey]))?
                .contentModificationDate ?? Date()
            if modified < cutoff { try? FileManager.default.removeItem(at: file) }
        }
    }

    static func appendLocalEvent(_ event: LocalInteractionEvent) {
        do {
            try FileManager.default.createDirectory(
                at: eventsDirectoryURL, withIntermediateDirectories: true,
                attributes: [.posixPermissions: 0o700]
            )
            let day = ISO8601DateFormatter().string(from: Date()).prefix(10)
            let url = eventsDirectoryURL.appendingPathComponent("events-\(day).jsonl")
            let encoder = JSONEncoder()
            encoder.dateEncodingStrategy = .iso8601
            var data = try encoder.encode(event)
            data.append(contentsOf: [0x0A])
            if FileManager.default.fileExists(atPath: url.path) {
                let handle = try FileHandle(forWritingTo: url)
                defer { try? handle.close() }
                try handle.seekToEnd()
                try handle.write(contentsOf: data)
            } else {
                try data.write(to: url, options: .atomic)
                try? FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
                pruneOldEvents()  // once a day, when the day's file is created
            }
        } catch {
            NSLog("MindLoomAgent: failed to append local event: %@", "\(error)")
        }
    }
}
