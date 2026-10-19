// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "MindLoomAgent",
    platforms: [.macOS(.v13)],
    products: [
        .executable(name: "MindLoomAgent", targets: ["MindLoomAgent"]),
    ],
    targets: [
        .target(
            name: "GuideCore",
            path: "Sources/GuideCore"
        ),
        .executableTarget(
            name: "MindLoomAgent",
            dependencies: ["GuideCore"],
            path: "Sources/MindLoomAgent"
        ),
        // XCTest is unavailable with Command Line Tools only; run `swift run GuideCoreChecks`.
        .executableTarget(
            name: "GuideCoreChecks",
            dependencies: ["GuideCore"],
            path: "Sources/GuideCoreChecks"
        ),
    ]
)
