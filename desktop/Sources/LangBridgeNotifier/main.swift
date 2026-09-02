import AppKit
import Foundation

private final class NotifierDelegate: NSObject, NSApplicationDelegate, NSUserNotificationCenterDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSUserNotificationCenter.default.delegate = self
        DispatchQueue.main.asyncAfter(deadline: .now() + 15) {
            NSApp.terminate(nil)
        }
    }

    func userNotificationCenter(
        _ center: NSUserNotificationCenter,
        shouldPresent notification: NSUserNotification
    ) -> Bool {
        true
    }

    func userNotificationCenter(
        _ center: NSUserNotificationCenter,
        didActivate notification: NSUserNotification
    ) {
        if let path = notification.userInfo?["output_path"] as? String, !path.isEmpty {
            NSWorkspace.shared.open(URL(fileURLWithPath: path))
        } else {
            let mainApp = Bundle.main.bundleURL
                .deletingLastPathComponent()
                .deletingLastPathComponent()
                .deletingLastPathComponent()
            NSWorkspace.shared.open(mainApp)
        }
        NSApp.terminate(nil)
    }
}

@main
struct LangBridgeNotifier {
    static func main() {
        let arguments = CommandLine.arguments
        if arguments.contains("--authorize") {
            return
        }
        if arguments.count >= 3 {
            post(arguments)
            return
        }
        let application = NSApplication.shared
        let delegate = NotifierDelegate()
        application.setActivationPolicy(.accessory)
        application.delegate = delegate
        NSUserNotificationCenter.default.delegate = delegate
        application.run()
    }

    private static func post(_ arguments: [String]) {
        let application = NSApplication.shared
        application.setActivationPolicy(.accessory)
        let notification = NSUserNotification()
        notification.title = arguments[1]
        notification.informativeText = arguments[2]
        notification.soundName = NSUserNotificationDefaultSoundName
        notification.hasActionButton = true
        notification.actionButtonTitle = "Open"
        if arguments.count >= 4, !arguments[3].isEmpty {
            notification.userInfo = ["output_path": arguments[3]]
        }
        NSUserNotificationCenter.default.deliver(notification)
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) {
            application.terminate(nil)
        }
        application.run()
    }
}
