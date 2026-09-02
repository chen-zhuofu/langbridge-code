import AppKit
import SwiftUI

struct LangbridgeMark: View {
    var size: CGFloat = 34

    var body: some View {
        Group {
            if let image = Self.image {
                Image(nsImage: image)
                    .resizable()
                    .interpolation(.high)
                    .aspectRatio(contentMode: .fit)
            } else {
                fallback
            }
        }
        .frame(width: size, height: size)
        .accessibilityLabel("LangBridge")
    }

    private var fallback: some View {
        ZStack {
            ForEach(0..<8, id: \.self) { index in
                Capsule()
                    .fill(Brand.accent.opacity(1 - Double(index) * 0.07))
                    .frame(width: size * 0.56, height: size * 0.25)
                    .offset(x: size * 0.17)
                    .rotationEffect(.degrees(Double(index) * 45))
            }
        }
    }

    private static let image: NSImage? = {
        guard let resources = Bundle.main.resourceURL else { return nil }
        let url = resources
            .appendingPathComponent("LangBridgeDesktop_LangBridgeDesktop.bundle", isDirectory: true)
            .appendingPathComponent("LangbridgeMark.svg")
        return NSImage(contentsOf: url)
    }()
}
