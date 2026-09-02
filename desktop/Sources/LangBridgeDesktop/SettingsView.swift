import AppKit
import SwiftUI

private enum SettingsPane: String, CaseIterable, Identifiable {
    case model
    case connections
    case appearance

    var id: String { rawValue }

    var title: String {
        switch self {
        case .model: "Model"
        case .connections: "Connections"
        case .appearance: "Appearance"
        }
    }

    var systemImage: String {
        switch self {
        case .model: "sparkles"
        case .connections: "link"
        case .appearance: "paintbrush"
        }
    }
}

struct SettingsView: View {
    @ObservedObject var store: SettingsStore
    let onSaved: () -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var selection: SettingsPane = .model

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 0) {
                sidebar
                Divider()
                detail
            }

            Divider()
            footer
        }
        .frame(width: 760, height: 620)
        .background(Color(nsColor: .windowBackgroundColor))
    }

    private var sidebar: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(spacing: 10) {
                LangbridgeMark(size: 34)
                VStack(alignment: .leading, spacing: 0) {
                    Text("LangBridge")
                        .font(.headline)
                    Text("Settings")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            .padding(.horizontal, 16)
            .padding(.top, 18)

            VStack(spacing: 4) {
                ForEach(SettingsPane.allCases) { pane in
                    Button {
                        selection = pane
                    } label: {
                        Label(pane.title, systemImage: pane.systemImage)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(.horizontal, 11)
                            .padding(.vertical, 8)
                            .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .foregroundStyle(selection == pane ? .primary : .secondary)
                    .background(
                        selection == pane ? Brand.accentSoft : .clear,
                        in: RoundedRectangle(cornerRadius: 8, style: .continuous)
                    )
                }
            }
            .padding(.horizontal, 8)

            Spacer()
        }
        .frame(width: 184)
        .background(Color(nsColor: .underPageBackgroundColor))
    }

    private var detail: some View {
        VStack(alignment: .leading, spacing: 0) {
            VStack(alignment: .leading, spacing: 4) {
                Text(selection.title)
                    .font(.title2.weight(.semibold))
                Text(detailSubtitle)
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 24)
            .padding(.vertical, 18)

            Divider()

            ScrollView {
                Group {
                    switch selection {
                    case .model:
                        modelSettings
                    case .connections:
                        connectionSettings
                    case .appearance:
                        appearanceSettings
                    }
                }
                .padding(24)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private var detailSubtitle: String {
        switch selection {
        case .model: "Provider and model defaults for new tasks."
        case .connections: "Connect local notes and read-only services."
        case .appearance: "Choose how the whole LangBridge window looks."
        }
    }

    private var modelSettings: some View {
        SettingsCard(title: "Default model", systemImage: "cpu") {
            VStack(spacing: 14) {
                settingsRow("Provider") {
                    Picker("Provider", selection: Binding(
                        get: { store.provider },
                        set: { store.selectProvider($0) }
                    )) {
                        Text("DeepSeek").tag("deepseek")
                        Text("Moonshot / Kimi").tag("moonshot")
                        Text("OpenAI").tag("openai")
                        Text("Anthropic").tag("anthropic")
                    }
                    .labelsHidden()
                    .frame(width: 250)
                }

                Divider()

                settingsRow("API key") {
                    SecureField("Required", text: $store.apiKey)
                        .textContentType(.password)
                        .frame(width: 250)
                }

                Divider()

                settingsRow("Model") {
                    Picker("Default model", selection: $store.model) {
                        ForEach(store.availableModels, id: \.self) { model in
                            Text(model).tag(model)
                        }
                    }
                    .labelsHidden()
                    .frame(width: 250)
                }
            }
        }
    }

    private var connectionSettings: some View {
        VStack(spacing: 16) {
            SettingsCard(title: "Obsidian", systemImage: "folder") {
                HStack(spacing: 12) {
                    VStack(alignment: .leading, spacing: 3) {
                        Text(store.obsidianVaultPath.isEmpty
                             ? "No vault selected"
                             : URL(fileURLWithPath: store.obsidianVaultPath).lastPathComponent)
                            .font(.callout.weight(.medium))
                        Text(store.obsidianVaultPath.isEmpty
                             ? "Choose the folder that contains your Obsidian vault."
                             : store.obsidianVaultPath)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .lineLimit(2)
                            .truncationMode(.middle)
                    }
                    Spacer(minLength: 12)
                    Button("Choose Vault Folder…") { chooseObsidianVault() }
                }
            }

            SettingsCard(title: "Gmail", systemImage: "envelope") {
                VStack(alignment: .leading, spacing: 14) {
                    HStack(alignment: .top, spacing: 12) {
                        VStack(alignment: .leading, spacing: 4) {
                            Text(gmailStateTitle)
                                .font(.callout.weight(.semibold))
                            Text("Uses the standard Gmail API with gmail.readonly access. LangBridge can search and read mail, but cannot send, delete, or change it.")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        Spacer(minLength: 12)
                        gmailStatusBadge
                    }

                    Divider()

                    if !store.gmailOAuthConfigured {
                        VStack(alignment: .leading, spacing: 8) {
                            setupStep(number: "1", text: "Enable Gmail API in a Google Cloud project.")
                            setupStep(number: "2", text: "Create a Desktop app OAuth client and download its JSON file.")
                            setupStep(number: "3", text: "Select that JSON here, then sign in with Google.")
                        }

                        HStack {
                            Link("Open Google setup guide", destination: Self.gmailSetupURL)
                            Spacer()
                            Button("Select OAuth JSON…") {
                                store.chooseGmailOAuthClient()
                            }
                            .buttonStyle(.borderedProminent)
                            .tint(Brand.accent)
                        }

                        Text("This opens a file picker only for the small OAuth JSON downloaded from Google Cloud. It never asks you to choose an email folder.")
                            .font(.caption2)
                            .foregroundStyle(.tertiary)
                    } else {
                        HStack(spacing: 10) {
                            Button("Change OAuth JSON…") {
                                store.chooseGmailOAuthClient()
                            }
                            Spacer()
                            if store.gmailConnected {
                                Button("Disconnect") { store.disconnectGmail() }
                                    .disabled(store.gmailBusy)
                            } else {
                                Button("Sign in with Google") { store.connectGmail() }
                                    .buttonStyle(.borderedProminent)
                                    .tint(Brand.accent)
                                    .disabled(store.gmailBusy)
                            }
                            if store.gmailBusy {
                                ProgressView().controlSize(.small)
                            }
                        }
                    }

                    if let status = store.gmailStatus {
                        Label(status, systemImage: store.gmailConnected ? "checkmark.circle.fill" : "info.circle")
                            .font(.caption)
                            .foregroundStyle(store.gmailConnected ? Brand.accent : .secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
        }
    }

    private var appearanceSettings: some View {
        SettingsCard(title: "Window background", systemImage: "photo") {
            VStack(alignment: .leading, spacing: 16) {
                settingsRow("Background") {
                    Picker("Background", selection: $store.wallpaperID) {
                        ForEach(store.wallpaperOptions) { option in
                            Text(option.videoURL == nil || option.id == WallpaperOption.defaultID
                                 ? option.name
                                 : "\(option.name) · Dynamic")
                                .tag(option.id)
                        }
                    }
                    .labelsHidden()
                    .frame(width: 300)
                }

                Divider()

                HStack(spacing: 14) {
                    if let thumbnailURL = store.selectedWallpaper.thumbnailURL,
                       let image = NSImage(contentsOf: thumbnailURL) {
                        Image(nsImage: image)
                            .resizable()
                            .scaledToFill()
                            .frame(width: 160, height: 92)
                            .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
                    } else {
                        RoundedRectangle(cornerRadius: 10, style: .continuous)
                            .fill(Color(nsColor: .textBackgroundColor))
                            .frame(width: 160, height: 92)
                            .overlay(Text("Default").font(.caption).foregroundStyle(.secondary))
                    }
                    VStack(alignment: .leading, spacing: 5) {
                        Text(store.selectedWallpaper.name)
                            .font(.headline)
                        Text(store.wallpaperActive
                             ? "Apple’s downloaded aerial plays behind the whole window."
                             : "The original LangBridge background.")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                        Button("Open macOS Wallpaper Settings…") { openWallpaperSettings() }
                            .padding(.top, 4)
                    }
                }
            }
        }
    }

    private var gmailStateTitle: String {
        if store.gmailConnected { return "Google account connected" }
        if store.gmailOAuthConfigured { return "Ready for Google sign-in" }
        return "Setup required before sign-in"
    }

    @ViewBuilder
    private var gmailStatusBadge: some View {
        let title = store.gmailConnected
            ? "Connected"
            : (store.gmailOAuthConfigured ? "Ready" : "Not set up")
        Text(title)
            .font(.caption.weight(.semibold))
            .foregroundStyle(store.gmailConnected ? Brand.accent : .secondary)
            .padding(.horizontal, 9)
            .padding(.vertical, 5)
            .background(
                store.gmailConnected ? Brand.accentSoft : Color.secondary.opacity(0.10),
                in: Capsule()
            )
    }

    private func settingsRow<Content: View>(
        _ title: String,
        @ViewBuilder content: () -> Content
    ) -> some View {
        HStack(spacing: 16) {
            Text(title)
            Spacer(minLength: 12)
            content()
        }
    }

    private func setupStep(number: String, text: String) -> some View {
        HStack(alignment: .top, spacing: 9) {
            Text(number)
                .font(.caption2.weight(.bold))
                .frame(width: 20, height: 20)
                .foregroundStyle(Brand.accent)
                .background(Brand.accentSoft, in: Circle())
            Text(text)
                .font(.caption)
                .foregroundStyle(.secondary)
                .padding(.top, 2)
        }
    }

    private var footer: some View {
        HStack(spacing: 10) {
            Text("Saved to ~/.langbridge/config.json")
                .font(.caption)
                .foregroundStyle(.secondary)
            Spacer()
            if let error = store.lastSaveError {
                Label(error, systemImage: "exclamationmark.triangle")
                    .font(.caption)
                    .foregroundStyle(.red)
                    .lineLimit(1)
            }
            Button("Cancel") { dismiss() }
            Button("Save") {
                if store.save() {
                    onSaved()
                    dismiss()
                }
            }
            .buttonStyle(.borderedProminent)
            .tint(Brand.accent)
        }
        .padding(.horizontal, 18)
        .frame(height: 56)
    }

    private func chooseObsidianVault() {
        let panel = NSOpenPanel()
        panel.title = "Choose your Obsidian Vault Folder"
        panel.prompt = "Choose Vault Folder"
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        guard panel.runModal() == .OK, let url = panel.url else { return }
        store.obsidianVaultPath = url.standardizedFileURL.path
    }

    private func openWallpaperSettings() {
        guard let url = URL(string: "x-apple.systempreferences:com.apple.Wallpaper-Settings.extension")
        else { return }
        NSWorkspace.shared.open(url)
    }

    private static let gmailSetupURL = URL(
        string: "https://developers.google.com/workspace/guides/create-credentials#desktop-app"
    )!
}

private struct SettingsCard<Content: View>: View {
    let title: String
    let systemImage: String
    @ViewBuilder let content: Content

    init(
        title: String,
        systemImage: String,
        @ViewBuilder content: () -> Content
    ) {
        self.title = title
        self.systemImage = systemImage
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Label(title, systemImage: systemImage)
                .font(.headline)
            content
        }
        .padding(18)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            Color(nsColor: .controlBackgroundColor),
            in: RoundedRectangle(cornerRadius: 12, style: .continuous)
        )
        .overlay {
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .stroke(Color.primary.opacity(0.08))
        }
    }
}
