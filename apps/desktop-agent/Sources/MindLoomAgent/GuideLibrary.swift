import Foundation
import GuideCore

/// Cached approved guides for the signed-in user, refreshed periodically.
final class GuideLibrary {
    static let refreshInterval: TimeInterval = 600

    private(set) var guides: [Guide] = []
    private(set) var lastError: String?
    private(set) var isLoading = false
    var onChange: (() -> Void)?

    private var config: AgentConfig
    private var timer: Timer?

    init(config: AgentConfig) {
        self.config = config
        timer = Timer.scheduledTimer(withTimeInterval: Self.refreshInterval, repeats: true) { [weak self] _ in
            self?.refresh()
        }
        refresh()
    }

    deinit {
        timer?.invalidate()
    }

    func reloadConfig(_ config: AgentConfig) {
        let accountChanged = config.accessToken != self.config.accessToken || config.apiBase != self.config.apiBase
        self.config = config
        if accountChanged { refresh() }
    }

    func guides(for bundleId: String) -> [Guide] {
        guides.filter { $0.bundleIds.contains(bundleId) }
    }

    func refresh() {
        guard config.isSignedIn else {
            guides = []
            lastError = nil
            onChange?()
            return
        }
        guard !isLoading else { return }
        isLoading = true
        onChange?()
        let client = APIClient(config: config)
        Task {
            let result: Result<[Guide], Error>
            do {
                result = .success(try await client.listGuides())
            } catch {
                result = .failure(error)
            }
            await MainActor.run {
                self.isLoading = false
                switch result {
                case .success(let guides):
                    self.guides = guides
                    self.lastError = nil
                case .failure(let error):
                    self.lastError = (error as? LocalizedError)?.errorDescription ?? error.localizedDescription
                }
                self.onChange?()
            }
        }
    }
}
