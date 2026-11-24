// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "ChessVisionOverlay",
    platforms: [.macOS(.v14)],
    targets: [
        .executableTarget(name: "ChessVisionOverlay", path: "Sources/ChessVisionOverlay"),
    ]
)
