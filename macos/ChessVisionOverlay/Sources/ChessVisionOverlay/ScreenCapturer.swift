import CoreGraphics
import Foundation
import ImageIO
import ScreenCaptureKit
import UniformTypeIdentifiers

enum CaptureError: LocalizedError {
    case noDisplay

    var errorDescription: String? { "no display available to capture" }
}

/// Captures the main display (or a region of it) with ScreenCaptureKit, leaving out this app's own
/// windows so the overlay never ends up in the images that get recognized.
final class ScreenCapturer {
    private var filter: SCContentFilter?
    private var display: SCDisplay?

    /// Main display bounds in points, origin top-left (the coordinates `capture(region:)` uses).
    var displayBounds: CGRect {
        guard let display else { return .zero }
        return CGRect(x: 0, y: 0, width: display.width, height: display.height)
    }

    func prepare() async throws {
        let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
        let mainID = CGMainDisplayID()
        guard let display = content.displays.first(where: { $0.displayID == mainID }) ?? content.displays.first else {
            throw CaptureError.noDisplay
        }
        let me = ProcessInfo.processInfo.processIdentifier
        let ownApps = content.applications.filter { $0.processID == me }
        filter = SCContentFilter(display: display, excludingApplications: ownApps, exceptingWindows: [])
        self.display = display
    }

    func reset() {
        filter = nil
        display = nil
    }

    /// Returns the image and the captured rect in display points.
    func capture(region: CGRect?) async throws -> (CGImage, CGRect) {
        if filter == nil { try await prepare() }
        guard let filter else { throw CaptureError.noDisplay }
        let rect = (region ?? displayBounds).integral.intersection(displayBounds)
        let scale = CGFloat(filter.pointPixelScale)
        let config = SCStreamConfiguration()
        config.sourceRect = rect
        config.width = max(1, Int(rect.width * scale))
        config.height = max(1, Int(rect.height * scale))
        config.showsCursor = false
        config.colorSpaceName = CGColorSpace.sRGB
        let image = try await SCScreenshotManager.captureImage(contentFilter: filter, configuration: config)
        return (image, rect)
    }
}

func jpegData(from image: CGImage, quality: Double = 0.9) -> Data? {
    let data = NSMutableData()
    guard let destination = CGImageDestinationCreateWithData(data, UTType.jpeg.identifier as CFString, 1, nil) else {
        return nil
    }
    CGImageDestinationAddImage(destination, image, [kCGImageDestinationLossyCompressionQuality: quality] as CFDictionary)
    return CGImageDestinationFinalize(destination) ? data as Data : nil
}

func loadImage(at url: URL) -> CGImage? {
    guard let source = CGImageSourceCreateWithURL(url as CFURL, nil) else { return nil }
    return CGImageSourceCreateImageAtIndex(source, 0, nil)
}

func writePNG(_ image: CGImage, to url: URL) -> Bool {
    guard let destination = CGImageDestinationCreateWithURL(url as CFURL, UTType.png.identifier as CFString, 1, nil) else {
        return false
    }
    CGImageDestinationAddImage(destination, image, nil)
    return CGImageDestinationFinalize(destination)
}
