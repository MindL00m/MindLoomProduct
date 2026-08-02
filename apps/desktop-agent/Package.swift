// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "MindLoomAgent",
    platforms: [.macOS(.v13)],
    products: [
        .executable(name: "MindLoomAgent", targets: ["MindLoomAgent"]),
    ],
    targets: [
        .executableTarget(
            name: "MindLoomAgent",
            path: "Sources/MindLoomAgent"
        ),
    ]
)
