// swift-tools-version: 5.10

import PackageDescription

let package = Package(
    name: "LangBridgeDesktop",
    platforms: [
        .macOS(.v14),
    ],
    products: [
        .executable(name: "LangBridgeDesktop", targets: ["LangBridgeDesktop"]),
        .executable(name: "LangBridgeNotifier", targets: ["LangBridgeNotifier"]),
        .executable(name: "LangBridgeCredentialHelper", targets: ["LangBridgeCredentialHelper"]),
    ],
    targets: [
        .target(name: "LangBridgeKeychain"),
        .executableTarget(
            name: "LangBridgeDesktop",
            resources: [
                .copy("Resources/LangbridgeMark.svg"),
            ]
        ),
        .testTarget(
            name: "LangBridgeDesktopTests",
            dependencies: ["LangBridgeDesktop", "LangBridgeKeychain"]
        ),
        .executableTarget(name: "LangBridgeNotifier"),
        .executableTarget(
            name: "LangBridgeCredentialHelper",
            dependencies: ["LangBridgeKeychain"]
        ),
    ]
)
