import AppKit
import SwiftUI

struct RootView: View {
    @ObservedObject var model: AppModel
    @State private var sidebarVisible = true

    var body: some View {
        Group {
            if sidebarVisible {
                HSplitView {
                    SidebarView(model: model)
                        .frame(minWidth: 0, idealWidth: 276, maxWidth: 340)
                    detail
                        .frame(minWidth: 0)
                        .layoutPriority(1)
                }
            } else {
                detail
            }
        }
        .background {
            WallpaperView(option: model.settings.selectedWallpaper)
                .allowsHitTesting(false)
        }
        .environment(\.wallpaperActive, model.settings.wallpaperActive)
        .frame(minHeight: WindowSizing.minimumContentHeight)
        .background(WindowAppearanceView(transparent: model.settings.wallpaperActive))
        .toolbar {
            ToolbarItem(placement: .navigation) {
                Button {
                    sidebarVisible.toggle()
                } label: {
                    Image(systemName: "sidebar.left")
                }
                .help(sidebarVisible ? "Hide Sidebar" : "Show Sidebar")
            }
        }
        .sheet(isPresented: $model.showingSettings) {
            SettingsView(store: model.settings) {
                model.settingsDidSave()
            }
            .interactiveDismissDisabled(!model.settings.hasCredential)
        }
        .confirmationDialog(
            "All three columns are in use. Which one should be replaced?",
            isPresented: Binding(
                get: { model.replacementCandidateTaskID != nil },
                set: { if !$0 { model.cancelColumnReplacement() } }
            )
        ) {
            ForEach(Array(model.visibleTasks.enumerated()), id: \.element.id) { index, task in
                Button("Replace \(task.title)") { model.replaceColumn(at: index) }
            }
            Button("Cancel", role: .cancel) { model.cancelColumnReplacement() }
        }
        .onReceive(NotificationCenter.default.publisher(for: NSApplication.willTerminateNotification)) { _ in
            model.shutdown()
        }
    }

    @ViewBuilder
    private var detail: some View {
        if model.showingSchedules {
            SchedulesView(
                store: model.scheduleStore,
                settings: model.settings,
                defaultWorkspace: model.selectedTask?.workspace.path
                    ?? model.workspaces.first?.path
                    ?? model.runtime.repositoryRoot?.path
                    ?? "",
                onOpenSettings: { model.showingSettings = true }
            )
        } else if !model.visibleTasks.isEmpty {
            SessionColumnsView(model: model)
        } else {
            welcome
        }
    }

    private var welcome: some View {
        VStack(spacing: 18) {
            LangbridgeMark(size: 86)
            Text("LangBridge")
                .font(.system(size: 30, weight: .semibold, design: .rounded))
            Text("Open a project to start a coding task.")
                .foregroundStyle(.secondary)
            Button("Open Project") {
                model.chooseWorkspace()
            }
            .buttonStyle(.borderedProminent)
            .tint(Brand.accent)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background {
            if model.settings.wallpaperActive {
                Rectangle().fill(.ultraThinMaterial)
            } else {
                Color(nsColor: .textBackgroundColor)
            }
        }
    }
}
