import AppKit
import Foundation
import UniformTypeIdentifiers

@MainActor
final class SettingsStore: ObservableObject {
    @Published var provider: String
    @Published var apiKey: String
    @Published var model: String
    @Published var obsidianVaultPath: String
    @Published var wallpaperID: String
    @Published private(set) var wallpaperOptions: [WallpaperOption]
    @Published var gmailConnected: Bool
    @Published var gmailOAuthConfigured: Bool
    @Published var gmailBusy = false
    @Published var gmailStatus: String?
    @Published var lastSaveError: String?

    private let configURL: URL
    private let preferencesURL: URL
    private let mcpConfigURL: URL
    private var config: [String: Any]
    private var preferences: [String: Any]
    private var mcpConfig: [String: Any]
    private let gmailOAuth = GmailOAuthCoordinator()

    private static let mcpKeychainService = "com.langbridge.app.mcp"
    private static let gmailAccount = "gmail"

    private static let fallbackModels: [String: [String]] = [
        "moonshot": ["kimi-k2.7-code", "kimi-k3"],
        "openai": ["gpt-5.6", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"],
        "anthropic": ["claude-fable-5"],
        "deepseek": ["deepseek-v4-flash"],
    ]

    init(homeDirectory: URL = FileManager.default.homeDirectoryForCurrentUser) {
        configURL = homeDirectory
            .appendingPathComponent(".langbridge", isDirectory: true)
            .appendingPathComponent("config.json")
        preferencesURL = homeDirectory
            .appendingPathComponent("Library/Application Support/LangBridge", isDirectory: true)
            .appendingPathComponent("preferences.json")
        mcpConfigURL = homeDirectory
            .appendingPathComponent("Library/Application Support/LangBridge", isDirectory: true)
            .appendingPathComponent("mcp.json")
        config = Self.readConfig(at: configURL)
        preferences = Self.readConfig(at: preferencesURL)
        var loadedMCPConfig = Self.readConfig(at: mcpConfigURL)
        let migratedLegacyGmail = Self.migrateLegacyGmailServer(in: &loadedMCPConfig)
        mcpConfig = loadedMCPConfig

        let api = config["api"] as? [String: Any]
        let selectedProvider = api?["provider"] as? String ?? "deepseek"
        provider = selectedProvider
        let keys = config["api_keys"] as? [String: Any]
        apiKey = keys?[selectedProvider] as? String ?? ""
        model = config["model"] as? String ?? Self.fallbackModels[selectedProvider]?.first ?? ""
        obsidianVaultPath = preferences["obsidian_vault_path"] as? String ?? ""
        let discoveredWallpapers = Self.discoverWallpaperOptions(homeDirectory: homeDirectory)
        wallpaperOptions = discoveredWallpapers
        let savedWallpaperID = preferences["wallpaper_id"] as? String
        wallpaperID = savedWallpaperID.flatMap { saved in
            discoveredWallpapers.contains(where: { $0.id == saved }) ? saved : nil
        } ?? discoveredWallpapers.first(where: { $0.name.localizedCaseInsensitiveContains("New York") })?.id
            ?? WallpaperOption.defaultID
        gmailOAuthConfigured = Self.gmailOAuthDictionary(in: mcpConfig) != nil
        gmailConnected = Self.keychainCredentialIsValid(
            service: Self.mcpKeychainService,
            account: Self.gmailAccount
        )
        if migratedLegacyGmail {
            try? writeMCPConfig()
        }
    }

    var providers: [String] {
        ["deepseek", "moonshot", "openai", "anthropic"]
    }

    var availableModels: [String] {
        let defaults = Self.fallbackModels[provider] ?? []
        guard !model.isEmpty, !defaults.contains(model) else { return defaults }
        return [model] + defaults
    }

    var hasCredential: Bool {
        !apiKey.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    var selectedWallpaper: WallpaperOption {
        wallpaperOptions.first { $0.id == wallpaperID } ?? .defaultOption
    }

    var wallpaperActive: Bool {
        selectedWallpaper.id != WallpaperOption.defaultID && selectedWallpaper.videoURL != nil
    }

    var bridgeEnvironment: [String: String] {
        var values = [
            "LANGBRIDGE_API_PROVIDER": provider,
            "LANGBRIDGE_MODEL": model,
        ]
        let keyName: String
        switch provider {
        case "moonshot": keyName = "MOONSHOT_API_KEY"
        case "openai": keyName = "OPENAI_API_KEY"
        case "anthropic": keyName = "ANTHROPIC_API_KEY"
        default: keyName = "DEEPSEEK_API_KEY"
        }
        values[keyName] = apiKey.trimmingCharacters(in: .whitespacesAndNewlines)
        return values
    }

    func selectProvider(_ value: String) {
        guard provider != value else { return }
        provider = value
        let keys = config["api_keys"] as? [String: Any]
        apiKey = keys?[value] as? String ?? ""
        model = Self.fallbackModels[value]?.first ?? ""
    }

    func save() -> Bool {
        let cleanedKey = apiKey.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !cleanedKey.isEmpty else {
            lastSaveError = "Enter an API key before saving."
            return false
        }

        var api = config["api"] as? [String: Any] ?? [:]
        api["provider"] = provider
        config["api"] = api

        var keys = config["api_keys"] as? [String: Any] ?? [:]
        keys[provider] = cleanedKey
        config["api_keys"] = keys
        config["model"] = model

        do {
            let directory = configURL.deletingLastPathComponent()
            try FileManager.default.createDirectory(
                at: directory,
                withIntermediateDirectories: true,
                attributes: [.posixPermissions: 0o700]
            )
            let data = try JSONSerialization.data(withJSONObject: config, options: [.prettyPrinted, .sortedKeys])
            try data.write(to: configURL, options: .atomic)
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: configURL.path)

            let preferencesDirectory = preferencesURL.deletingLastPathComponent()
            try FileManager.default.createDirectory(
                at: preferencesDirectory,
                withIntermediateDirectories: true,
                attributes: [.posixPermissions: 0o700]
            )
            preferences["obsidian_vault_path"] = obsidianVaultPath
            preferences["wallpaper_id"] = wallpaperID
            let preferencesData = try JSONSerialization.data(
                withJSONObject: preferences,
                options: [.prettyPrinted, .sortedKeys]
            )
            try preferencesData.write(to: preferencesURL, options: .atomic)
            try FileManager.default.setAttributes(
                [.posixPermissions: 0o600], ofItemAtPath: preferencesURL.path
            )
            lastSaveError = nil
            return true
        } catch {
            lastSaveError = error.localizedDescription
            return false
        }
    }

    @discardableResult
    func chooseGmailOAuthClient() -> Bool {
        let panel = NSOpenPanel()
        panel.title = "Choose Google Desktop OAuth JSON"
        panel.prompt = "Use OAuth Client"
        panel.canChooseDirectories = false
        panel.canChooseFiles = true
        panel.allowsMultipleSelection = false
        panel.allowedContentTypes = [.json]
        guard panel.runModal() == .OK, let url = panel.url else { return false }
        do {
            let client = try GoogleDesktopOAuthClient(data: Data(contentsOf: url))
            try Self.deleteKeychainCredential(
                service: Self.mcpKeychainService,
                account: Self.gmailAccount
            )
            setGmailServer(client: client, enabled: false)
            try writeMCPConfig()
            gmailOAuthConfigured = true
            gmailConnected = false
            gmailStatus = "OAuth client configured. Click Connect Gmail to sign in."
            return true
        } catch {
            gmailStatus = error.localizedDescription
            return false
        }
    }

    func connectGmail() {
        guard gmailOAuthConfigured else {
            gmailStatus = "Select the Google Desktop OAuth JSON in Settings before signing in."
            return
        }
        guard let oauth = Self.gmailOAuthDictionary(in: mcpConfig) else {
            gmailStatus = "Select the Google Desktop OAuth JSON in Settings before signing in."
            return
        }
        do {
            let client = try GoogleDesktopOAuthClient(dictionary: oauth)
            gmailBusy = true
            gmailStatus = "Opening Google sign-in…"
            Task {
                do {
                    let credential = try await gmailOAuth.authorize(client: client)
                    try Self.storeKeychainCredential(
                        credential,
                        service: Self.mcpKeychainService,
                        account: Self.gmailAccount
                    )
                    setGmailServer(client: client, enabled: true)
                    try writeMCPConfig()
                    gmailConnected = true
                    gmailStatus = "Connected with read-only Gmail access."
                } catch {
                    gmailStatus = error.localizedDescription
                }
                gmailBusy = false
            }
        } catch {
            gmailStatus = error.localizedDescription
        }
    }

    func disconnectGmail() {
        do {
            try Self.deleteKeychainCredential(
                service: Self.mcpKeychainService,
                account: Self.gmailAccount
            )
            if let oauth = Self.gmailOAuthDictionary(in: mcpConfig),
               let client = try? GoogleDesktopOAuthClient(dictionary: oauth) {
                setGmailServer(client: client, enabled: false)
                try writeMCPConfig()
            }
            gmailConnected = false
            gmailStatus = "Gmail disconnected."
        } catch {
            gmailStatus = error.localizedDescription
        }
    }

    private func setGmailServer(client: GoogleDesktopOAuthClient, enabled: Bool) {
        var servers = mcpConfig["servers"] as? [String: Any] ?? [:]
        servers["gmail"] = [
            "enabled": enabled,
            "transport": "gmail-api",
            "agents": ["langbridge"],
            "credential_account": Self.gmailAccount,
            "allowed_tools": ["search_threads", "get_message", "get_thread", "list_labels"],
            "oauth": [
                "client_id": client.clientID,
                "client_secret": client.clientSecret,
                "auth_uri": client.authURI,
                "token_uri": client.tokenURI,
            ],
        ]
        mcpConfig["version"] = 1
        mcpConfig["servers"] = servers
    }

    private static func migrateLegacyGmailServer(in config: inout [String: Any]) -> Bool {
        var servers = config["servers"] as? [String: Any] ?? [:]
        guard var gmail = servers["gmail"] as? [String: Any],
              gmail["transport"] as? String == "streamable-http",
              (gmail["url"] as? String)?.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
                == "https://gmailmcp.googleapis.com/mcp/v1"
        else { return false }
        gmail["transport"] = "gmail-api"
        gmail.removeValue(forKey: "url")
        servers["gmail"] = gmail
        config["version"] = 1
        config["servers"] = servers
        return true
    }

    private func writeMCPConfig() throws {
        let directory = mcpConfigURL.deletingLastPathComponent()
        try FileManager.default.createDirectory(
            at: directory,
            withIntermediateDirectories: true,
            attributes: [.posixPermissions: 0o700]
        )
        let data = try JSONSerialization.data(
            withJSONObject: mcpConfig,
            options: [.prettyPrinted, .sortedKeys]
        )
        try data.write(to: mcpConfigURL, options: .atomic)
        try FileManager.default.setAttributes(
            [.posixPermissions: 0o600], ofItemAtPath: mcpConfigURL.path
        )
    }

    private static func gmailOAuthDictionary(in config: [String: Any]) -> [String: Any]? {
        let servers = config["servers"] as? [String: Any]
        let gmail = servers?["gmail"] as? [String: Any]
        let oauth = gmail?["oauth"] as? [String: Any]
        guard let clientID = oauth?["client_id"] as? String, !clientID.isEmpty else { return nil }
        return oauth
    }

    private static func keychainCredentialIsValid(service: String, account: String) -> Bool {
        guard let data = try? runCredentialHelper(
            operation: "get",
            service: service,
            account: account
        ),
        let value = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
        let accessToken = value["access_token"] as? String,
        !accessToken.isEmpty
        else { return false }
        return true
    }

    private static func runCredentialHelper(
        operation: String,
        service: String,
        account: String,
        input: Data? = nil
    ) throws -> Data {
        let helper = Bundle.main.bundleURL
            .appendingPathComponent("Contents/Helpers/LangBridgeCredentialHelper")
        guard FileManager.default.isExecutableFile(atPath: helper.path) else {
            throw GmailOAuthError.authentication("LangBridge credential helper is missing.")
        }
        let process = Process()
        let output = Pipe()
        let errors = Pipe()
        process.executableURL = helper
        process.arguments = [operation, service, account]
        process.standardOutput = output
        process.standardError = errors
        if let input {
            let pipe = Pipe()
            process.standardInput = pipe
            try process.run()
            try pipe.fileHandleForWriting.write(contentsOf: input)
            try pipe.fileHandleForWriting.close()
        } else {
            process.standardInput = FileHandle.nullDevice
            try process.run()
        }
        process.waitUntilExit()
        let result = output.fileHandleForReading.readDataToEndOfFile()
        guard process.terminationStatus == 0 else {
            let detail = String(
                data: errors.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8
            )?.trimmingCharacters(in: .whitespacesAndNewlines)
            throw GmailOAuthError.authentication(
                detail?.isEmpty == false ? detail! : "Could not access the Gmail credential."
            )
        }
        return result
    }

    private static func storeKeychainCredential(
        _ credential: [String: Any], service: String, account: String
    ) throws {
        let data = try JSONSerialization.data(withJSONObject: credential, options: [.sortedKeys])
        _ = try runCredentialHelper(
            operation: "set",
            service: service,
            account: account,
            input: data
        )
    }

    private static func deleteKeychainCredential(service: String, account: String) throws {
        _ = try runCredentialHelper(
            operation: "delete",
            service: service,
            account: account
        )
    }

    private static func readConfig(at url: URL) -> [String: Any] {
        guard let data = try? Data(contentsOf: url),
              let object = try? JSONSerialization.jsonObject(with: data),
              let dictionary = object as? [String: Any]
        else { return [:] }
        return dictionary
    }

    static func discoverWallpaperOptions(homeDirectory: URL) -> [WallpaperOption] {
        let aerials = homeDirectory
            .appendingPathComponent("Library/Application Support/com.apple.wallpaper/aerials", isDirectory: true)
        let manifestURL = aerials.appendingPathComponent("manifest/entries.json")
        let root = readConfig(at: manifestURL)
        guard let assets = root["assets"] as? [[String: Any]]
        else { return [.defaultOption] }

        let videos = aerials.appendingPathComponent("videos", isDirectory: true)
        let thumbnails = aerials.appendingPathComponent("thumbnails", isDirectory: true)
        let installed = assets.compactMap { asset -> WallpaperOption? in
            guard let id = asset["id"] as? String,
                  let name = asset["accessibilityLabel"] as? String,
                  !id.isEmpty,
                  !name.isEmpty
            else { return nil }
            let videoURL = videos.appendingPathComponent("\(id).mov")
            guard FileManager.default.fileExists(atPath: videoURL.path) else { return nil }
            let thumbnailURL = thumbnails.appendingPathComponent("\(id).png")
            return WallpaperOption(
                id: id,
                name: name,
                videoURL: videoURL,
                thumbnailURL: FileManager.default.fileExists(atPath: thumbnailURL.path)
                    ? thumbnailURL
                    : nil
            )
        }
        .sorted {
            let leftNewYork = $0.name.localizedCaseInsensitiveContains("New York")
            let rightNewYork = $1.name.localizedCaseInsensitiveContains("New York")
            if leftNewYork != rightNewYork { return leftNewYork }
            return $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending
        }
        return [.defaultOption] + installed
    }
}
