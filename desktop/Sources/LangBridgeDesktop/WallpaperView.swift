import AppKit
import AVFoundation
import SwiftUI

private struct WallpaperActiveKey: EnvironmentKey {
    static let defaultValue = false
}

extension EnvironmentValues {
    var wallpaperActive: Bool {
        get { self[WallpaperActiveKey.self] }
        set { self[WallpaperActiveKey.self] = newValue }
    }
}

struct WallpaperView: View {
    let option: WallpaperOption

    var body: some View {
        Group {
            if let videoURL = option.videoURL {
                ZStack {
                    if let thumbnailURL = option.thumbnailURL,
                       let image = NSImage(contentsOf: thumbnailURL) {
                        Image(nsImage: image)
                            .resizable()
                            .scaledToFill()
                    }
                    LoopingAerialView(url: videoURL)
                }
                .overlay(Color.black.opacity(0.18))
            } else {
                Color(nsColor: .textBackgroundColor)
            }
        }
        .ignoresSafeArea()
    }
}

private struct LoopingAerialView: NSViewRepresentable {
    let url: URL

    func makeNSView(context: Context) -> AerialPlayerView {
        let view = AerialPlayerView()
        view.play(url: url)
        return view
    }

    func updateNSView(_ nsView: AerialPlayerView, context: Context) {
        nsView.play(url: url)
    }
}

private final class AerialPlayerView: NSView {
    private let playerLayer = AVPlayerLayer()
    private var player: AVQueuePlayer?
    private var looper: AVPlayerLooper?
    private var currentURL: URL?

    override init(frame frameRect: NSRect) {
        super.init(frame: frameRect)
        wantsLayer = true
        playerLayer.videoGravity = .resizeAspectFill
        playerLayer.backgroundColor = NSColor.clear.cgColor
        layer?.addSublayer(playerLayer)
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    override func layout() {
        super.layout()
        playerLayer.frame = bounds
    }

    func play(url: URL) {
        guard currentURL != url else { return }
        currentURL = url
        let queue = AVQueuePlayer()
        queue.isMuted = true
        queue.actionAtItemEnd = .none
        let looper = AVPlayerLooper(player: queue, templateItem: AVPlayerItem(url: url))
        player = queue
        self.looper = looper
        playerLayer.player = queue
        queue.play()
    }
}

enum WindowFrameFitter {
    static func fit(_ frame: NSRect, inside visibleFrame: NSRect) -> NSRect {
        guard visibleFrame.width > 0, visibleFrame.height > 0 else { return frame }
        var fitted = frame
        fitted.size.width = min(frame.width, visibleFrame.width)
        fitted.size.height = min(frame.height, visibleFrame.height)
        fitted.origin.x = min(
            max(frame.minX, visibleFrame.minX),
            visibleFrame.maxX - fitted.width
        )
        fitted.origin.y = min(
            max(frame.minY, visibleFrame.minY),
            visibleFrame.maxY - fitted.height
        )
        return fitted
    }
}

struct WindowAppearanceView: NSViewRepresentable {
    let transparent: Bool

    func makeNSView(context: Context) -> NSView {
        let view = NSView()
        configure(view)
        return view
    }

    func updateNSView(_ nsView: NSView, context: Context) {
        configure(nsView)
    }

    private func configure(_ view: NSView) {
        update(view.window)
        DispatchQueue.main.async { update(view.window) }
    }

    private func update(_ window: NSWindow?) {
        guard let window else { return }
        window.isOpaque = !transparent
        window.backgroundColor = transparent ? .clear : .windowBackgroundColor
        window.titlebarAppearsTransparent = transparent
    }
}
