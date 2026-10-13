import ApplicationServices
import AppKit
import Foundation

enum AccessibilityPermission {
    /// `AXIsProcessTrusted()` is stale on recent macOS. Always use the options API.
    static var isTrusted: Bool {
        let options = [
            kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: false,
        ] as CFDictionary
        return AXIsProcessTrustedWithOptions(options)
    }

    /// Path macOS TCC is evaluating — useful when multiple agent builds are listed.
    static var currentExecutablePath: String {
        Bundle.main.executablePath
            ?? CommandLine.arguments.first
            ?? "(unknown)"
    }

    static var currentAppPath: String {
        let url = Bundle.main.bundleURL
        if url.pathExtension == "app" { return url.path }
        return currentExecutablePath
    }

    static var isRunningFromAppBundle: Bool {
        Bundle.main.bundleURL.pathExtension == "app"
            || Bundle.main.bundlePath.hasSuffix(".app")
    }

    @discardableResult
    static func promptIfNeeded() -> Bool {
        let options = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
        return AXIsProcessTrustedWithOptions(options)
    }

    static func revealAppInFinder() {
        let url = Bundle.main.bundleURL
        if url.pathExtension == "app" {
            NSWorkspace.shared.activateFileViewerSelecting([url])
        }
    }

    static func openSystemSettings() {
        let urls = [
            "x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension?Privacy_Accessibility",
            "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
        ]
        for raw in urls {
            if let url = URL(string: raw), NSWorkspace.shared.open(url) {
                return
            }
        }
    }
}
