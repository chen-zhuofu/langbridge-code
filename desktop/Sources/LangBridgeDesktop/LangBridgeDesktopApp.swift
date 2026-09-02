import AppKit
import SwiftUI

enum WindowSizing {
    static let minimumContentHeight: CGFloat = 520

    static func minimumContentWidth(for screenWidth: CGFloat) -> CGFloat {
        screenWidth / 6
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var observers: [NSObjectProtocol] = []

    func applicationDidFinishLaunching(_ notification: Notification) {
        let center = NotificationCenter.default
        for name in [
            NSWindow.didBecomeKeyNotification,
            NSWindow.didMoveNotification,
            NSWindow.didResizeNotification,
            NSWindow.didChangeScreenNotification,
        ] {
            observers.append(center.addObserver(
                forName: name,
                object: nil,
                queue: .main
            ) { [weak self] notification in
                guard let window = notification.object as? NSWindow else { return }
                self?.fitMainWindowToScreen(window)
            })
        }
        observers.append(center.addObserver(
            forName: NSApplication.didChangeScreenParametersNotification,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            self?.fitAllMainWindowsToScreens()
        })

        DispatchQueue.main.async { [weak self] in
            self?.fitAllMainWindowsToScreens()
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.25) { [weak self] in
            self?.fitAllMainWindowsToScreens()
        }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }

    deinit {
        observers.forEach(NotificationCenter.default.removeObserver)
    }

    private func fitAllMainWindowsToScreens() {
        NSApplication.shared.windows.forEach(fitMainWindowToScreen)
    }

    private func fitMainWindowToScreen(_ window: NSWindow) {
        guard window.title == "LangBridge",
              let screen = window.screen ?? NSScreen.main
        else { return }
        window.contentMinSize = NSSize(
            width: WindowSizing.minimumContentWidth(for: screen.frame.width),
            height: WindowSizing.minimumContentHeight
        )
        let fitted = WindowFrameFitter.fit(window.frame, inside: screen.visibleFrame)
        guard !NSEqualRects(fitted, window.frame) else { return }
        window.setFrame(fitted, display: true, animate: false)
    }
}

@main
struct LangBridgeDesktopApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var model: AppModel

    init() {
        let settings = SettingsStore()
        _model = StateObject(wrappedValue: AppModel(settings: settings))
    }

    var body: some Scene {
        WindowGroup("LangBridge") {
            RootView(model: model)
        }
        .windowStyle(.titleBar)
        .windowToolbarStyle(.unifiedCompact)
        .commands {
            CommandGroup(replacing: .newItem) {
                Button("New Task") {
                    model.newTask()
                }
                .keyboardShortcut("n")
                Button("Open Project…") {
                    model.chooseWorkspace()
                }
                .keyboardShortcut("o")
            }
            CommandGroup(replacing: .appSettings) {
                Button("Settings…") {
                    model.showingSettings = true
                }
                .keyboardShortcut(",")
            }
        }
    }
}
