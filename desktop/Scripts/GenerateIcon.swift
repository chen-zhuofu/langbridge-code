import AppKit
import Foundation

guard (3...4).contains(CommandLine.arguments.count) else {
    fputs("Usage: GenerateIcon.swift <mark.svg> <AppIcon.icns> [white|black]\n", stderr)
    exit(2)
}

let sourceURL = URL(fileURLWithPath: CommandLine.arguments[1])
let outputURL = URL(fileURLWithPath: CommandLine.arguments[2])
let backgroundName = CommandLine.arguments.count == 4 ? CommandLine.arguments[3] : "white"
guard backgroundName == "white" || backgroundName == "black" else {
    fputs("Background must be white or black.\n", stderr)
    exit(2)
}
let backgroundColor: NSColor = backgroundName == "black" ? .black : .white
guard let source = NSImage(contentsOf: sourceURL) else {
    fputs("Could not load logo: \(sourceURL.path)\n", stderr)
    exit(1)
}

func pngData(pixels: Int) -> Data {
    guard let bitmap = NSBitmapImageRep(
        bitmapDataPlanes: nil,
        pixelsWide: pixels,
        pixelsHigh: pixels,
        bitsPerSample: 8,
        samplesPerPixel: 4,
        hasAlpha: true,
        isPlanar: false,
        colorSpaceName: .deviceRGB,
        bytesPerRow: 0,
        bitsPerPixel: 0
    ) else {
        fatalError("Could not allocate \(pixels)x\(pixels) icon")
    }

    bitmap.size = NSSize(width: pixels, height: pixels)
    NSGraphicsContext.saveGraphicsState()
    guard let context = NSGraphicsContext(bitmapImageRep: bitmap) else {
        fatalError("Could not create \(pixels)x\(pixels) drawing context")
    }
    NSGraphicsContext.current = context
    backgroundColor.setFill()
    NSRect(x: 0, y: 0, width: pixels, height: pixels).fill()
    // The original Interview Tracker mark intentionally fills beyond the SVG's
    // nominal safe area. A slight negative inset matches its 75% visual width.
    let inset = CGFloat(pixels) * -0.031
    source.draw(
        in: NSRect(
            x: inset,
            y: inset,
            width: CGFloat(pixels) - inset * 2,
            height: CGFloat(pixels) - inset * 2
        ),
        from: .zero,
        operation: .sourceOver,
        fraction: 1
    )
    context.flushGraphics()
    NSGraphicsContext.restoreGraphicsState()
    guard let data = bitmap.representation(using: .png, properties: [:]) else {
        fatalError("Could not encode \(pixels)x\(pixels) icon")
    }
    return data
}

func appendBigEndian(_ value: UInt32, to data: inout Data) {
    var encoded = value.bigEndian
    withUnsafeBytes(of: &encoded) { data.append(contentsOf: $0) }
}

// Modern ICNS entries contain PNG data. Retina aliases intentionally repeat
// pixel sizes under the logical-size FourCCs used by Finder and the Dock.
let entries: [(String, Int)] = [
    ("icp4", 16),
    ("ic11", 32),
    ("icp5", 32),
    ("ic12", 64),
    ("icp6", 64),
    ("ic07", 128),
    ("ic13", 256),
    ("ic08", 256),
    ("ic14", 512),
    ("ic09", 512),
    ("ic10", 1024),
]

var chunks = Data()
for (type, pixels) in entries {
    let png = pngData(pixels: pixels)
    chunks.append(contentsOf: type.utf8)
    appendBigEndian(UInt32(png.count + 8), to: &chunks)
    chunks.append(png)
}

var icns = Data("icns".utf8)
appendBigEndian(UInt32(chunks.count + 8), to: &icns)
icns.append(chunks)
try FileManager.default.createDirectory(
    at: outputURL.deletingLastPathComponent(),
    withIntermediateDirectories: true
)
try icns.write(to: outputURL, options: .atomic)
