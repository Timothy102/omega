// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "OmegaCore",
    platforms: [.macOS(.v14)],
    products: [
        .library(name: "OmegaCore", targets: ["OmegaCore"])
    ],
    targets: [
        .target(
            name: "OmegaCore",
            swiftSettings: [.swiftLanguageMode(.v6)]
        ),
        .testTarget(
            name: "OmegaCoreTests",
            dependencies: ["OmegaCore"],
            swiftSettings: [.swiftLanguageMode(.v6)]
        ),
    ]
)
