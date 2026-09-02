import AppKit
import Foundation

@MainActor
final class AppModel: ObservableObject {
    @Published private(set) var workspaces: [Workspace]
    @Published private(set) var tasks: [TaskSession] = []
    @Published private(set) var histories: [UUID: [SessionItem]] = [:]
    @Published private(set) var inboxItems: [TaskInboxItem] = []
    @Published private(set) var columnLayout = SessionColumnLayout()
    @Published var replacementCandidateTaskID: UUID?
    @Published var showingSettings = false
    @Published var showingSchedules = false

    let settings: SettingsStore
    let runtime: RuntimeLocator
    let scheduleStore: ScheduleStore

    private let workspacesDefaultsKey = "langbridge.desktop.workspaces"
    private var historyLoaders: [UUID: BridgeProcess] = [:]

    init(settings: SettingsStore, runtime: RuntimeLocator = RuntimeLocator()) {
        self.settings = settings
        self.runtime = runtime
        scheduleStore = ScheduleStore(runtime: runtime)
        workspaces = Self.loadWorkspaces(key: workspacesDefaultsKey)
        if workspaces.isEmpty, let initial = runtime.defaultWorkspace {
            workspaces = [initial]
            persistWorkspaces()
        }
        showingSettings = !settings.hasCredential
        if settings.hasCredential {
            if let workspace = workspaces.first {
                newTask(in: workspace)
            }
            refreshIdleWorkspaceHistories()
        }
        scheduleStore.installAndRefresh()
    }

    var selectedTask: TaskSession? {
        tasks.first { $0.id == selectedTaskID }
    }

    var selectedTaskID: UUID? { columnLayout.activeID }

    var visibleTasks: [TaskSession] {
        columnLayout.ids.compactMap { id in tasks.first { $0.id == id } }
    }

    var runningTaskCount: Int {
        tasks.filter { $0.state.isRunning }.count
    }

    func chooseWorkspace() {
        let panel = NSOpenPanel()
        panel.title = "Open a project"
        panel.prompt = "Open Project"
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.canCreateDirectories = true
        guard panel.runModal() == .OK, let url = panel.url else { return }
        addWorkspace(url)
    }

    func addWorkspace(_ url: URL) {
        let workspace = Workspace(path: url.path)
        if let existing = workspaces.first(where: { $0.path == workspace.path }) {
            if let existingTask = tasks.first(where: { $0.workspace.id == existing.id }) {
                showTask(existingTask)
            } else {
                newTask(in: existing)
            }
            return
        }
        workspaces.append(workspace)
        persistWorkspaces()
        newTask(in: workspace)
    }

    func removeWorkspace(_ workspace: Workspace) {
        historyLoaders.removeValue(forKey: workspace.id)?.shutdown()
        tasks.filter { $0.workspace.id == workspace.id }.forEach { $0.close() }
        tasks.removeAll { $0.workspace.id == workspace.id }
        for id in columnLayout.ids where tasks.allSatisfy({ $0.id != id }) {
            columnLayout.remove(id)
        }
        histories[workspace.id] = nil
        inboxItems.removeAll { $0.workspaceID == workspace.id }
        workspaces.removeAll { $0.id == workspace.id }
        persistWorkspaces()
    }

    func newTask(in workspace: Workspace? = nil) {
        guard settings.hasCredential else {
            showingSettings = true
            return
        }
        guard let workspace = workspace ?? selectedTask?.workspace ?? workspaces.first else {
            chooseWorkspace()
            return
        }
        if let draft = tasks.first(where: {
            $0.workspace.id == workspace.id && $0.isBlankDraft
        }) {
            showTask(draft)
            return
        }
        let task = makeTask(workspace: workspace)
        tasks.append(task)
        showTask(task)
    }

    func openHistory(_ item: SessionItem, in workspace: Workspace) {
        if let existing = tasks.first(where: { $0.sessionPath == item.path }) {
            showTask(existing, scrollToBottom: true)
            return
        }
        let task = makeTask(workspace: workspace, resume: item)
        tasks.append(task)
        showTask(task, scrollToBottom: true)
    }

    func openHistoryToRight(_ item: SessionItem, in workspace: Workspace) {
        if let existing = tasks.first(where: { $0.sessionPath == item.path }) {
            openToRight(existing)
            return
        }
        let task = makeTask(workspace: workspace, resume: item)
        tasks.append(task)
        openToRight(task)
    }

    func showTask(_ task: TaskSession, scrollToBottom: Bool = false) {
        showTask(id: task.id)
        if scrollToBottom {
            task.requestScrollToBottom()
        }
    }

    func openInboxItem(_ item: TaskInboxItem) {
        if let index = inboxItems.firstIndex(where: { $0.id == item.id }) {
            inboxItems[index].isUnread = false
        }
        if let task = tasks.first(where: { $0.id == item.taskID }) {
            showTask(task, scrollToBottom: true)
            return
        }
        guard let workspace = workspaces.first(where: { $0.id == item.workspaceID }) else {
            return
        }
        openHistory(
            SessionItem(path: item.sessionPath, label: item.title),
            in: workspace
        )
    }

    func canMarkSessionUnread(_ sessionPath: String) -> Bool {
        !inboxItems.contains { $0.sessionPath == sessionPath && $0.isUnread }
    }

    func markSessionUnread(
        sessionPath: String,
        title: String,
        workspaceID: UUID,
        taskID: UUID? = nil
    ) {
        if let index = inboxItems.firstIndex(where: { $0.sessionPath == sessionPath }) {
            inboxItems[index].isUnread = true
            return
        }
        inboxItems.insert(
            TaskInboxItem(
                taskID: taskID ?? UUID(),
                workspaceID: workspaceID,
                sessionPath: sessionPath,
                title: title,
                status: "ok"
            ),
            at: 0
        )
    }

    private func recordTaskCompletion(_ task: TaskSession, status: String) {
        guard let sessionPath = task.sessionPath else { return }
        inboxItems.removeAll { $0.sessionPath == sessionPath }
        inboxItems.insert(
            TaskInboxItem(
                taskID: task.id,
                workspaceID: task.workspace.id,
                sessionPath: sessionPath,
                title: task.title,
                status: status
            ),
            at: 0
        )
    }

    func showTask(id: UUID) {
        guard let task = tasks.first(where: { $0.id == id }) else { return }
        columnLayout.show(id)
        showingSchedules = false
        task.start()
        releaseIdleTasksOutsideVisibleColumns()
    }

    func openToRight(_ task: TaskSession) {
        guard tasks.contains(where: { $0.id == task.id }) else { return }
        showingSchedules = false
        task.requestScrollToBottom()
        task.start()
        if !columnLayout.openToRight(task.id) {
            replacementCandidateTaskID = task.id
        }
        releaseIdleTasksOutsideVisibleColumns()
    }

    func replaceColumn(at index: Int) {
        guard let candidate = replacementCandidateTaskID else { return }
        columnLayout.replaceColumn(at: index, with: candidate)
        replacementCandidateTaskID = nil
        showingSchedules = false
        tasks.first(where: { $0.id == candidate })?.start()
        releaseIdleTasksOutsideVisibleColumns()
    }

    func cancelColumnReplacement() {
        replacementCandidateTaskID = nil
        releaseIdleTasksOutsideVisibleColumns()
    }

    func hideColumn(_ task: TaskSession) {
        columnLayout.hide(task.id)
        releaseIdleTasksOutsideVisibleColumns()
    }

    func renameTask(_ task: TaskSession, to title: String) {
        task.renameCurrentSession(to: title)
    }

    func renameHistory(_ item: SessionItem, in workspace: Workspace, to title: String) {
        let normalized = title.split(whereSeparator: \Character.isWhitespace).joined(separator: " ")
        guard !normalized.isEmpty else { return }
        let finalTitle = String(normalized.prefix(120)).trimmingCharacters(in: .whitespaces)
        let titleURL = URL(fileURLWithPath: item.path, isDirectory: true)
            .appendingPathComponent(".session-title")
        do {
            try (finalTitle + "\n").write(to: titleURL, atomically: true, encoding: .utf8)
        } catch {
            return
        }
        guard var items = histories[workspace.id],
              let index = items.firstIndex(where: { $0.path == item.path })
        else { return }
        items[index] = SessionItem(
            path: item.path,
            label: finalTitle,
            archived: item.archived
        )
        histories[workspace.id] = items
    }

    func archiveHistory(_ item: SessionItem, in workspace: Workspace) {
        if let task = tasks.first(where: { $0.sessionPath == item.path }), !task.canArchive {
            return
        }
        guard setArchived(true, item: item, workspace: workspace) else { return }
        if let task = tasks.first(where: { $0.sessionPath == item.path }) {
            task.close()
            tasks.removeAll { $0.id == task.id }
            columnLayout.remove(task.id)
        }
        if columnLayout.ids.isEmpty {
            newTask(in: workspace)
        }
    }

    func unarchiveHistory(_ item: SessionItem, in workspace: Workspace) {
        _ = setArchived(false, item: item, workspace: workspace)
    }

    func settingsDidSave() {
        showingSettings = false
        tasks.forEach { $0.refreshCredentials() }
        scheduleStore.installAndRefresh()
        if tasks.isEmpty, let workspace = workspaces.first {
            newTask(in: workspace)
        }
        refreshIdleWorkspaceHistories()
    }

    func showSchedules() {
        showingSchedules = true
        scheduleStore.refresh()
    }

    func shutdown() {
        tasks.forEach { $0.close() }
        historyLoaders.values.forEach { $0.shutdown() }
        historyLoaders.removeAll()
    }

    private func refreshIdleWorkspaceHistories() {
        for workspace in workspaces where !tasks.contains(where: { $0.workspace.id == workspace.id }) {
            loadHistory(in: workspace)
        }
    }

    private func loadHistory(in workspace: Workspace) {
        guard historyLoaders[workspace.id] == nil else { return }
        do {
            let configuration = try runtime.launchConfiguration(workspace: workspace, settings: settings)
            let process = BridgeProcess(configuration: configuration)
            process.onEvent = { [weak self, weak process] event in
                guard let self,
                      event["type"] as? String == "hello",
                      let rawSessions = event["sessions"] as? [[String: Any]]
                else { return }
                self.histories[workspace.id] = BridgeEventDecoder.sessions(
                    from: ["items": rawSessions]
                )
                process?.shutdown()
                self.historyLoaders[workspace.id] = nil
            }
            process.onExit = { [weak self, weak process] _ in
                guard let self, self.historyLoaders[workspace.id] === process else { return }
                self.historyLoaders[workspace.id] = nil
            }
            historyLoaders[workspace.id] = process
            try process.start()
        } catch {
            historyLoaders[workspace.id] = nil
        }
    }

    private func makeTask(workspace: Workspace, resume: SessionItem? = nil) -> TaskSession {
        let task = TaskSession(
            workspace: workspace,
            runtime: runtime,
            settings: settings,
            resume: resume
        )
        task.onSessionList = { [weak self] items in
            self?.histories[workspace.id] = items
        }
        task.onBecameIdle = { [weak self] in
            self?.releaseIdleTasksOutsideVisibleColumns()
        }
        task.onTurnFinished = { [weak self, weak task] status in
            guard let task else { return }
            self?.recordTaskCompletion(task, status: status)
        }
        task.onSessionForked = { [weak self, weak task] item in
            guard let self, let task else { return }
            // Anchor placement on the task that actually forked, not
            // whichever column happens to be active, so forking from a
            // non-active column still opens immediately to its right.
            columnLayout.show(task.id)
            openHistoryToRight(item, in: task.workspace)
        }
        return task
    }

    private func releaseIdleTasksOutsideVisibleColumns() {
        var protectedIDs = Set(columnLayout.ids)
        if let replacementCandidateTaskID {
            protectedIDs.insert(replacementCandidateTaskID)
        }
        let releasable = tasks.filter { !protectedIDs.contains($0.id) }
        for task in releasable where task.deactivateIfIdle() {
            tasks.removeAll { $0.id == task.id }
        }
    }

    @discardableResult
    private func setArchived(
        _ archived: Bool,
        item: SessionItem,
        workspace: Workspace
    ) -> Bool {
        do {
            try SessionArchive.setArchived(archived, sessionPath: item.path)
        } catch {
            return false
        }
        guard var items = histories[workspace.id],
              let index = items.firstIndex(where: { $0.path == item.path })
        else { return true }
        items[index] = SessionItem(
            path: item.path,
            label: item.label,
            archived: archived
        )
        histories[workspace.id] = items
        return true
    }

    private func persistWorkspaces() {
        guard let data = try? JSONEncoder().encode(workspaces) else { return }
        UserDefaults.standard.set(data, forKey: workspacesDefaultsKey)
    }

    private static func loadWorkspaces(key: String) -> [Workspace] {
        guard let data = UserDefaults.standard.data(forKey: key),
              let values = try? JSONDecoder().decode([Workspace].self, from: data)
        else { return [] }
        return values.filter { FileManager.default.fileExists(atPath: $0.path) }
    }
}
