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
            return try decoder.decode(AgentConfig.self, from: data)
        } catch {
            NSLog("MindLoomAgent: failed to load config, using defaults: %@", "\(error)")
            return .default
        }
    }

    static func save(_ config: AgentConfig) {
        do {
            let dir = configURL.deletingLastPathComponent()
            try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
            let encoder = JSONEncoder()
            encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
            let data = try encoder.encode(config)
            try data.write(to: configURL, options: .atomic)
        } catch {
            NSLog("MindLoomAgent: failed to save config: %@", "\(error)")
        }
    }

    static func appendLocalEvent(_ event: LocalInteractionEvent) {
        do {
            try FileManager.default.createDirectory(at: eventsDirectoryURL, withIntermediateDirectories: true)
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
            }
        } catch {
            NSLog("MindLoomAgent: failed to append local event: %@", "\(error)")
        }
    }
}
