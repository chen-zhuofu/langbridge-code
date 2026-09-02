import Foundation
import SwiftUI

private enum SessionRenameTarget {
    case task(TaskSession)
    case history(SessionItem, Workspace)
}

struct SidebarView: View {
    @ObservedObject var model: AppModel
    @Environment(\.wallpaperActive) private var wallpaperActive
    @State private var renameTarget: SessionRenameTarget?
    @State private var renameText = ""
    @State private var inboxExpanded = true

    var body: some View {
        VStack(spacing: 0) {
            brandHeader

            Button {
                model.newTask()
            } label: {
                Label("New task", systemImage: "square.and.pencil")
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.vertical, 5)
            }
            .buttonStyle(.borderedProminent)
            .tint(Brand.accent)
            .padding(.horizontal, 12)
            .padding(.bottom, 12)

            Button {
                model.showSchedules()
            } label: {
                HStack {
                    Label("Schedules", systemImage: "calendar.badge.clock")
                    Spacer()
                    let active = model.scheduleStore.schedules.filter(\.enabled).count
                    if active > 0 {
                        Text("\(active)")
                            .font(.caption2.monospacedDigit())
                            .foregroundStyle(.secondary)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.vertical, 5)
            }
            .buttonStyle(.plain)
            .padding(.horizontal, 14)
            .padding(.bottom, 8)

            ScrollView {
                LazyVStack(spacing: 14) {
                    DisclosureGroup(isExpanded: $inboxExpanded) {
                        ForEach(model.inboxItems.filter(\.isUnread)) { item in
                            InboxRow(
                                item: item,
                                onOpen: { model.openInboxItem(item) }
                            )
                        }
                    } label: {
                        HStack {
                            Label("Inbox", systemImage: "tray")
                            Spacer()
                            let unreadCount = model.inboxItems.filter(\.isUnread).count
                            if unreadCount > 0 {
                                Text("\(unreadCount)")
                                    .font(.caption2.monospacedDigit())
                                    .foregroundStyle(.secondary)
                            }
                        }
                    }
                    .padding(.horizontal, 6)

                    ForEach(model.workspaces) { workspace in
                        VStack(spacing: 3) {
                            workspaceHeader(workspace)

                            ForEach(unlistedTasks(in: workspace)) { task in
                                TaskRow(
                                    task: task,
                                    isSelected: !model.showingSchedules && model.selectedTaskID == task.id,
                                    onOpen: { model.showTask(task, scrollToBottom: true) }
                                )
                                .contextMenu {
                                    Button("Open to Right") {
                                        model.openToRight(task)
                                    }
                                    if task.title != "New task" {
                                        Button("Rename…") {
                                            beginRename(.task(task), currentName: task.title)
                                        }
                                    }
                                    if let sessionPath = task.sessionPath {
                                        Divider()
                                        Button("Mark as Unread", systemImage: "envelope.badge") {
                                            model.markSessionUnread(
                                                sessionPath: sessionPath,
                                                title: task.title,
                                                workspaceID: workspace.id,
                                                taskID: task.id
                                            )
                                        }
                                        .disabled(!model.canMarkSessionUnread(sessionPath))
                                        Button("Archive", systemImage: "archivebox") {
                                            model.archiveHistory(
                                                SessionItem(path: sessionPath, label: task.title),
                                                in: workspace
                                            )
                                        }
                                        .disabled(!task.canArchive)
                                    }
                                }
                            }

                            ForEach(activeHistories(in: workspace)) { item in
                                let task = task(for: item, in: workspace)
                                Group {
                                    if let task {
                                        TaskRow(
                                            task: task,
                                            isSelected: !model.showingSchedules
                                                && model.selectedTaskID == task.id,
                                            onOpen: { model.showTask(task, scrollToBottom: true) }
                                        )
                                    } else {
                                        HistoryRow(
                                            item: item,
                                            onOpen: { model.openHistory(item, in: workspace) }
                                        )
                                    }
                                }
                                .contextMenu {
                                    Button("Open to Right") {
                                        model.openHistoryToRight(item, in: workspace)
                                    }
                                    Button("Rename…") {
                                        if let task {
                                            beginRename(.task(task), currentName: task.title)
                                        } else {
                                            beginRename(
                                                .history(item, workspace),
                                                currentName: item.label
                                            )
                                        }
                                    }
                                    Divider()
                                    Button("Mark as Unread", systemImage: "envelope.badge") {
                                        model.markSessionUnread(
                                            sessionPath: item.path,
                                            title: task?.title ?? item.label,
                                            workspaceID: workspace.id,
                                            taskID: task?.id
                                        )
                                    }
                                    .disabled(!model.canMarkSessionUnread(item.path))
                                    Button("Archive", systemImage: "archivebox") {
                                        model.archiveHistory(item, in: workspace)
                                    }
                                    .disabled(task?.canArchive == false)
                                }
                            }
                        }
                    }

                    archivedSection
                }
                .padding(.horizontal, 8)
                .padding(.bottom, 10)
            }

            Divider()
            HStack {
                Button {
                    model.chooseWorkspace()
                } label: {
                    Label("Open project", systemImage: "folder.badge.plus")
                }
                .buttonStyle(.plain)
                Spacer()
                if model.runningTaskCount > 0 {
                    Text("\(model.runningTaskCount) running")
                        .font(.caption)
                        .foregroundStyle(Brand.accent)
                }
                Button {
                    model.showingSettings = true
                } label: {
                    Image(systemName: "gearshape")
                }
                .buttonStyle(.plain)
                .help("Settings")
            }
            .font(.callout)
            .padding(12)
        }
        .background {
            if wallpaperActive {
                Rectangle().fill(.ultraThinMaterial)
            } else {
                Color(nsColor: .windowBackgroundColor)
            }
        }
        .alert(
            "Rename session",
            isPresented: Binding(
                get: { renameTarget != nil },
                set: { if !$0 { renameTarget = nil } }
            )
        ) {
            TextField("Name", text: $renameText)
            Button("Cancel", role: .cancel) { renameTarget = nil }
            Button("Rename") { submitRename() }
                .disabled(renameText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
        } message: {
            Text("The session folder and its history will stay unchanged.")
        }
    }

    private func beginRename(_ target: SessionRenameTarget, currentName: String) {
        renameText = currentName
        renameTarget = target
    }

    private func submitRename() {
        let title = renameText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !title.isEmpty, let renameTarget else { return }
        switch renameTarget {
        case let .task(task):
            model.renameTask(task, to: title)
        case let .history(item, workspace):
            model.renameHistory(item, in: workspace, to: title)
        }
        self.renameTarget = nil
    }

    private var brandHeader: some View {
        HStack(spacing: 10) {
            LangbridgeMark(size: 30)
            Text("LangBridge")
                .font(.headline)
            Spacer()
        }
        .padding(14)
    }

    private func tasks(in workspace: Workspace) -> [TaskSession] {
        model.tasks.filter { $0.workspace.id == workspace.id }
    }

    private func unlistedTasks(in workspace: Workspace) -> [TaskSession] {
        let tasks = tasks(in: workspace)
        let ids = SidebarSessionLayout.unlistedTaskIDs(
            tasks: tasks.map { .init(id: $0.id, sessionPath: $0.sessionPath) },
            sessions: model.histories[workspace.id] ?? []
        )
        return tasks.filter { ids.contains($0.id) }
    }

    private func task(for item: SessionItem, in workspace: Workspace) -> TaskSession? {
        tasks(in: workspace).first { $0.sessionPath == item.path }
    }

    private func activeHistories(in workspace: Workspace) -> [SessionItem] {
        (model.histories[workspace.id] ?? []).filter { !$0.archived }
    }

    private func archivedHistories(in workspace: Workspace) -> [SessionItem] {
        (model.histories[workspace.id] ?? []).filter(\.archived)
    }

    @ViewBuilder
    private var archivedSection: some View {
        let hasArchivedItems = model.workspaces.contains {
            !archivedHistories(in: $0).isEmpty
        }
        if hasArchivedItems {
            DisclosureGroup {
                ForEach(model.workspaces) { workspace in
                    let items = archivedHistories(in: workspace)
                    if !items.isEmpty {
                        DisclosureGroup {
                            ForEach(items) { item in
                                HistoryRow(
                                    item: item,
                                    onOpen: {
                                        model.unarchiveHistory(item, in: workspace)
                                        model.openHistory(item, in: workspace)
                                    },
                                    onArchive: {
                                        model.unarchiveHistory(item, in: workspace)
                                    },
                                    archiveSystemImage: "arrow.uturn.backward",
                                    archiveHelp: "Unarchive task"
                                )
                                .contextMenu {
                                    Button("Unarchive", systemImage: "arrow.uturn.backward") {
                                        model.unarchiveHistory(item, in: workspace)
                                    }
                                }
                            }
                        } label: {
                            Label(workspace.name, systemImage: "folder")
                                .foregroundStyle(.secondary)
                                .padding(.leading, 12)
                        }
                    }
                }
            } label: {
                Label("Archived", systemImage: "archivebox")
                    .foregroundStyle(.secondary)
                    .padding(.horizontal, 6)
            }
        }
    }

    private func workspaceHeader(_ workspace: Workspace) -> some View {
        HStack(spacing: 6) {
            Image(systemName: "folder")
            Text(workspace.name)
                .lineLimit(1)
            Spacer()
            Button {
                model.newTask(in: workspace)
            } label: {
                Image(systemName: "plus")
                    .frame(width: 22, height: 22)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .help("New task in \(workspace.name)")
        }
        .font(.callout)
        .foregroundStyle(.secondary)
        .padding(.leading, 6)
        .contextMenu {
            Button("Remove Project", role: .destructive) {
                model.removeWorkspace(workspace)
            }
        }
    }
}

private struct TaskRow: View {
    @ObservedObject var task: TaskSession
    let isSelected: Bool
    let onOpen: () -> Void

    var body: some View {
        HStack(spacing: 8) {
            Button(action: onOpen) {
                HStack(spacing: 8) {
                    Circle()
                        .fill(task.state.color)
                        .frame(width: 7, height: 7)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(task.title)
                            .lineLimit(1)
                        if task.state.isRunning || task.state == .waiting {
                            Text(task.workflow.isEmpty ? task.state.label : task.workflow)
                                .font(.caption2)
                                .foregroundStyle(task.state.color)
                                .lineLimit(1)
                        }
                    }
                    Spacer(minLength: 0)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
        }
        .padding(.leading, 8)
        .padding(.trailing, 4)
        .padding(.vertical, 4)
        .background {
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .fill(isSelected ? Color.primary.opacity(0.12) : Color.clear)
        }
        .contentShape(Rectangle())
    }
}

private struct InboxRow: View {
    let item: TaskInboxItem
    let onOpen: () -> Void

    var body: some View {
        HStack(spacing: 6) {
            Button(action: onOpen) {
                HStack(spacing: 8) {
                    Image(systemName: systemImage)
                        .foregroundStyle(statusColor)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(item.title)
                            .fontWeight(item.isUnread ? .semibold : .regular)
                            .lineLimit(1)
                        Text(statusLabel)
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                    Spacer(minLength: 0)
                    if item.isUnread {
                        Circle()
                            .fill(Brand.accent)
                            .frame(width: 6, height: 6)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .help("Open the completed task")
        }
        .padding(.leading, 8)
        .padding(.vertical, 3)
    }

    private var systemImage: String {
        switch item.status {
        case "ok": "checkmark.circle.fill"
        case "stopped": "stop.circle.fill"
        default: "exclamationmark.circle.fill"
        }
    }

    private var statusColor: Color {
        switch item.status {
        case "ok": Brand.accent
        case "stopped": .orange
        default: .red
        }
    }

    private var statusLabel: String {
        switch item.status {
        case "ok": "Finished"
        case "stopped": "Stopped"
        default: "Failed"
        }
    }
}

private struct HistoryRow: View {
    let item: SessionItem
    let onOpen: () -> Void
    var onArchive: (() -> Void)? = nil
    var archiveSystemImage = "archivebox"
    var archiveHelp = "Archive task"

    var body: some View {
        HStack(spacing: 8) {
            Button(action: onOpen) {
                HStack(spacing: 8) {
                    Circle()
                        .fill(Color.secondary)
                        .frame(width: 7, height: 7)
                    Text(item.label)
                        .lineLimit(1)
                        .foregroundStyle(.secondary)
                    Spacer(minLength: 0)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            if let onArchive {
                ArchiveHandle(
                    systemImage: archiveSystemImage,
                    help: archiveHelp,
                    action: onArchive
                )
            }
        }
        .padding(.leading, 8)
        .padding(.trailing, 4)
        .padding(.vertical, 4)
        .contentShape(Rectangle())
    }
}

private struct ArchiveHandle: View {
    let systemImage: String
    let help: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Image(systemName: systemImage)
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
                .frame(width: 24, height: 24)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .help(help)
    }
}
