import Foundation

struct RuntimeLocator {
    let repositoryRoot: URL?

    init(environment: [String: String] = ProcessInfo.processInfo.environment) {
        if let override = environment["LANGBRIDGE_REPO_ROOT"], !override.isEmpty {
            repositoryRoot = URL(fileURLWithPath: override).standardizedFileURL
            return
        }

        if let marker = Bundle.main.url(forResource: "repo-root", withExtension: "txt"),
           let value = try? String(contentsOf: marker, encoding: .utf8)
            .trimmingCharacters(in: .whitespacesAndNewlines),
           !value.isEmpty
        {
            repositoryRoot = URL(fileURLWithPath: value).standardizedFileURL
            return
        }

        var candidate = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
        for _ in 0..<6 {
            if Self.isRepositoryRoot(candidate) {
                repositoryRoot = candidate.standardizedFileURL
                return
            }
            candidate.deleteLastPathComponent()
        }
        repositoryRoot = nil
    }

    var defaultWorkspace: Workspace? {
        repositoryRoot.map { Workspace(path: $0.path) }
    }

    @MainActor
    func launchConfiguration(workspace: Workspace, settings: SettingsStore) throws -> BridgeLaunchConfiguration {
        guard let root = repositoryRoot else {
            throw RuntimeError.missingRepository
        }

        let venvPython = root.appendingPathComponent(".venv/bin/python")
        var environment = ProcessInfo.processInfo.environment
        let sourcePath = root.appendingPathComponent("src").path
        let existingPythonPath = environment["PYTHONPATH"] ?? ""
        environment["PYTHONPATH"] = existingPythonPath.isEmpty
            ? sourcePath
            : sourcePath + ":" + existingPythonPath
        let notifier = Bundle.main.bundleURL
            .appendingPathComponent("Contents/Helpers/LangBridgeNotifier.app/Contents/MacOS/LangBridgeNotifier")
        if FileManager.default.isExecutableFile(atPath: notifier.path) {
            environment["LANGBRIDGE_NOTIFIER_PATH"] = notifier.path
        }
        let credentialHelper = Bundle.main.bundleURL
            .appendingPathComponent("Contents/Helpers/LangBridgeCredentialHelper")
        if FileManager.default.isExecutableFile(atPath: credentialHelper.path) {
            environment["LANGBRIDGE_CREDENTIAL_HELPER"] = credentialHelper.path
        }
        for (key, value) in settings.bridgeEnvironment where !value.isEmpty {
            environment[key] = value
        }

        if FileManager.default.isExecutableFile(atPath: venvPython.path) {
            return BridgeLaunchConfiguration(
                executable: venvPython,
                arguments: ["-m", "langbridge_code.ui.bridge"],
                workingDirectory: URL(fileURLWithPath: workspace.path),
                environment: environment
            )
        }

        guard let uv = Self.findExecutable(named: "uv", environment: environment) else {
            throw RuntimeError.missingPython(root.path)
        }
        return BridgeLaunchConfiguration(
            executable: uv,
            arguments: ["run", "--project", root.path, "python", "-m", "langbridge_code.ui.bridge"],
            workingDirectory: URL(fileURLWithPath: workspace.path),
            environment: environment
        )
    }

    func schedulerConfiguration(arguments: [String]) throws -> BridgeLaunchConfiguration {
        guard let root = repositoryRoot else {
            throw RuntimeError.missingRepository
        }
        var environment = ProcessInfo.processInfo.environment
        let sourcePath = root.appendingPathComponent("src").path
        let existingPythonPath = environment["PYTHONPATH"] ?? ""
        environment["PYTHONPATH"] = existingPythonPath.isEmpty
            ? sourcePath
            : sourcePath + ":" + existingPythonPath
        let notifier = Bundle.main.bundleURL
            .appendingPathComponent("Contents/Helpers/LangBridgeNotifier.app/Contents/MacOS/LangBridgeNotifier")
        if FileManager.default.isExecutableFile(atPath: notifier.path) {
            environment["LANGBRIDGE_NOTIFIER_PATH"] = notifier.path
        }
        let credentialHelper = Bundle.main.bundleURL
            .appendingPathComponent("Contents/Helpers/LangBridgeCredentialHelper")
        if FileManager.default.isExecutableFile(atPath: credentialHelper.path) {
            environment["LANGBRIDGE_CREDENTIAL_HELPER"] = credentialHelper.path
        }
        let venvPython = root.appendingPathComponent(".venv/bin/python")
        if FileManager.default.isExecutableFile(atPath: venvPython.path) {
            return BridgeLaunchConfiguration(
                executable: venvPython,
                arguments: ["-m", "langbridge_code.scheduler"] + arguments,
                workingDirectory: root,
                environment: environment
            )
        }
        guard let uv = Self.findExecutable(named: "uv", environment: environment) else {
            throw RuntimeError.missingPython(root.path)
        }
        return BridgeLaunchConfiguration(
            executable: uv,
            arguments: ["run", "--project", root.path, "python", "-m", "langbridge_code.scheduler"] + arguments,
            workingDirectory: root,
            environment: environment
        )
    }

    private static func isRepositoryRoot(_ url: URL) -> Bool {
        FileManager.default.fileExists(atPath: url.appendingPathComponent("pyproject.toml").path)
            && FileManager.default.fileExists(atPath: url.appendingPathComponent("src/ui/bridge.py").path)
    }

    private static func findExecutable(named name: String, environment: [String: String]) -> URL? {
        let candidates = (environment["PATH"] ?? "")
            .split(separator: ":")
            .map(String.init)
            + ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin"]
        for directory in candidates {
            let url = URL(fileURLWithPath: directory).appendingPathComponent(name)
            if FileManager.default.isExecutableFile(atPath: url.path) {
                return url
            }
        }
        return nil
    }
}

struct BridgeLaunchConfiguration {
    let executable: URL
    let arguments: [String]
    let workingDirectory: URL
    let environment: [String: String]
}

enum RuntimeError: LocalizedError {
    case missingRepository
    case missingPython(String)

    var errorDescription: String? {
        switch self {
        case .missingRepository:
            "Could not locate the LangBridge checkout. Set LANGBRIDGE_REPO_ROOT and relaunch."
        case let .missingPython(root):
            "No LangBridge Python environment found. Run `uv sync` in \(root), then create a new task."
        }
    }
}
