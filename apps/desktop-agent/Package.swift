// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "MindLoomAgent",
    platforms: [.macOS(.v15)],
    products: [
        .executable(name: "MindLoomAgent", targets: ["MindLoomAgent"]),
    ],
    targets: [
        .target(
            name: "GuideCore",
            path: "Sources/GuideCore"
        ),
        // What device tasks may do, the screen outline, key combos and the action gate.
        // No AppKit or Accessibility, so its checks run anywhere.
        .target(
            name: "DriverCore",
            path: "Sources/DriverCore"
        ),
        .executableTarget(
            name: "MindLoomAgent",
            dependencies: ["GuideCore", "DriverCore"],
            path: "Sources/MindLoomAgent"
        ),
        // XCTest is unavailable with Command Line Tools only; run `swift run GuideCoreChecks`
        // and `swift run DriverCoreChecks`.
        .executableTarget(
            name: "GuideCoreChecks",
            dependencies: ["GuideCore"],
            path: "Sources/GuideCoreChecks"
        ),
        .executableTarget(
            name: "DriverCoreChecks",
            dependencies: ["DriverCore"],
            path: "Sources/DriverCoreChecks"
        ),
        // A small app with known controls, for checking the driver against real AX.
        .executableTarget(
            name: "DriverFixture",
            path: "Sources/DriverFixture"
        ),
    ],
    swiftLanguageModes: [.v5]
)
