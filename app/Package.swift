// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "Relay",
    defaultLocalization: "en",
    platforms: [.macOS(.v15)],
    targets: [
        // Engine API models, client and pure helpers; no UI, unit-tested.
        .target(name: "RelayKit"),
        // The menu-bar app and main window.
        .executableTarget(
            name: "RelayApp",
            dependencies: ["RelayKit"],
            exclude: ["Resources"]
        ),
        .testTarget(
            name: "RelayKitTests",
            dependencies: ["RelayKit"],
            resources: [.copy("Fixtures")]
        ),
    ],
    swiftLanguageModes: [.v5]
)
