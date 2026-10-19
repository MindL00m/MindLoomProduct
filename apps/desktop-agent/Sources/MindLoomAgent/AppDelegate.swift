import AppKit
import Foundation

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var statusController: StatusItemController?
    private var controlPanel: ControlPanel?
    private var engine: CaptureEngine?
    private var guideController: GuideController?

    func applicationDidFinishLaunching(_ notification: Notification) {
        // Regular policy so the control window is a normal visible app window.
        // Menu-bar-only (.accessory) was invisible when the status area was full.
        NSApp.setActivationPolicy(.regular)

        let config = AgentConfigStore.load()
        let engine = CaptureEngine(config: config)
        self.engine = engine

        let library = GuideLibrary(config: config)
        let guides = GuideController(engine: engine, library: library)
        let panel = ControlPanel(engine: engine, guides: guides, config: config)
        let status = StatusItemController(engine: engine, config: config)
        engine.onStatusChange = { _ in
            status.applyExternalStatus(engine.status)
            panel.refreshFromEngine()
        }
        panel.onConfigChange = { updated in
            status.reloadConfig(updated)
            library.reloadConfig(updated)
        }
        library.onChange = {
            panel.refreshGuides()
        }

        self.guideController = guides
        self.controlPanel = panel
        self.statusController = status
        panel.show()

        fputs("MindLoomAgent: control window opened.\n", stderr)
        fputs(
            "MindLoomAgent: Accessibility trusted=\(AccessibilityPermission.isTrusted). "
                + "Allowlist apps=\(config.allowlist.count).\n"
                + "MindLoomAgent: executable=\(AccessibilityPermission.currentExecutablePath)\n"
                + "MindLoomAgent: bundle=\(Bundle.main.bundleURL.path)\n",
            stderr
        )
        fflush(stderr)

        if !AccessibilityPermission.isTrusted {
            AccessibilityPermission.promptIfNeeded()
        }
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        controlPanel?.show()
        return true
    }
}
