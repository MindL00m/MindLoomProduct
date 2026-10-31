import AppKit
import Foundation

if let flag = CommandLine.arguments.firstIndex(of: "--driver-selftest") {
    MainActor.assumeIsolated {
        DriverSelfTest.runAndExit(Array(CommandLine.arguments.dropFirst(flag + 1)))
    }
}

let appDelegate = AppDelegate()
let app = NSApplication.shared
app.delegate = appDelegate

fputs("MindLoomAgent: starting…\n", stderr)
fputs("MindLoomAgent: a “Loom Capture” window should appear (menu-bar icon may be hidden when the bar is full).\n", stderr)
fflush(stderr)

app.run()
