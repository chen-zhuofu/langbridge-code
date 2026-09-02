import AppKit
import SwiftUI

struct SchedulesView: View {
    @ObservedObject var store: ScheduleStore
    @ObservedObject var settings: SettingsStore
    let defaultWorkspace: String
    let onOpenSettings: () -> Void

    @State private var selectedID: String?
    @State private var editorItem: ScheduleItem?
    @State private var showingEditor = false
    @State private var deletingItem: ScheduleItem?
    @Environment(\.wallpaperActive) private var wallpaperActive
    private let refreshTimer = Timer.publish(every: 10, on: .main, in: .common).autoconnect()

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            if settings.obsidianVaultPath.isEmpty {
                vaultSetup
            } else if store.schedules.isEmpty && !store.isLoading {
                emptyState
            } else {
                scheduleContent
            }
        }
        .background {
            if wallpaperActive {
                Color.black.opacity(0.46)
            } else {
                Color(nsColor: .textBackgroundColor)
            }
        }
        .onAppear { store.refresh() }
        .onReceive(refreshTimer) { _ in store.refresh() }
        .onChange(of: selectedID) {
            if let selectedID { store.loadRuns(for: selectedID) }
        }
        .onChange(of: store.schedules) {
            if selectedID == nil, let first = store.schedules.first {
                selectedID = first.id
            } else if let selectedID,
                      !store.schedules.contains(where: { $0.id == selectedID })
            {
                self.selectedID = store.schedules.first?.id
            }
        }
        .sheet(isPresented: $showingEditor) {
            ScheduleEditorView(
                item: editorItem,
                defaultWorkspace: defaultWorkspace,
                onSave: { draft in store.save(draft, existing: editorItem) }
            )
        }
        .alert(
            "Delete schedule?",
            isPresented: Binding(
                get: { deletingItem != nil },
                set: { if !$0 { deletingItem = nil } }
            ),
            presenting: deletingItem
        ) { item in
            Button("Cancel", role: .cancel) {}
            Button("Delete", role: .destructive) {
                store.delete(item)
                if selectedID == item.id { selectedID = nil }
            }
        } message: { item in
            Text("\(item.name) will be removed from the active list. Its definition remains recoverable locally.")
        }
    }

    private var header: some View {
        HStack(spacing: 12) {
            Image(systemName: "calendar.badge.clock")
                .font(.title2)
                .foregroundStyle(Brand.accent)
            VStack(alignment: .leading, spacing: 2) {
                Text("Schedules")
                    .font(.headline)
                Text("Runs continue in the background when LangBridge is closed.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            if store.isLoading {
                ProgressView().controlSize(.small)
            }
            Button {
                editorItem = nil
                showingEditor = true
            } label: {
                Label("New Schedule", systemImage: "plus")
            }
            .buttonStyle(.borderedProminent)
            .tint(Brand.accent)
            .disabled(settings.obsidianVaultPath.isEmpty)
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 12)
        .background {
            if wallpaperActive {
                Rectangle().fill(.ultraThinMaterial)
            } else {
                Rectangle().fill(.bar)
            }
        }
    }

    private var vaultSetup: some View {
        VStack(spacing: 16) {
            Spacer()
            Image(systemName: "note.text")
                .font(.system(size: 48))
                .foregroundStyle(Brand.accent)
            Text("Choose an Obsidian Vault")
                .font(.title2.weight(.semibold))
            Text("Scheduled reports are saved as Markdown inside your Vault.")
                .foregroundStyle(.secondary)
            Button("Open Settings", action: onOpenSettings)
                .buttonStyle(.borderedProminent)
                .tint(Brand.accent)
            Spacer()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private var emptyState: some View {
        VStack(spacing: 14) {
            Spacer()
            Image(systemName: "calendar.badge.plus")
                .font(.system(size: 44))
                .foregroundStyle(.secondary)
            Text("No schedules yet")
                .font(.title2.weight(.semibold))
            Text("Create one here, or ask LangBridge in a task using natural language.")
                .foregroundStyle(.secondary)
            Button("Create Schedule") {
                editorItem = nil
                showingEditor = true
            }
            .buttonStyle(.borderedProminent)
            .tint(Brand.accent)
            Spacer()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private var scheduleContent: some View {
        HSplitView {
            List(store.schedules, selection: $selectedID) { item in
                ScheduleRow(item: item)
                    .tag(item.id)
                    .contextMenu {
                        Button(item.enabled ? "Pause" : "Resume") {
                            item.enabled ? store.pause(item) : store.resume(item)
                        }
                        Button("Run Now") { store.runNow(item) }
                        Divider()
                        Button("Delete", role: .destructive) { deletingItem = item }
                    }
            }
            .frame(minWidth: 0, idealWidth: 320, maxWidth: 390)

            if let item = selectedSchedule {
                ScheduleDetail(
                    item: item,
                    runs: store.runs[item.id] ?? [],
                    onToggle: { item.enabled ? store.pause(item) : store.resume(item) },
                    onRun: { store.runNow(item) },
                    onEdit: {
                        editorItem = item
                        showingEditor = true
                    },
                    onDelete: { deletingItem = item }
                )
            } else {
                Text("Select a schedule to view its details.")
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .overlay(alignment: .bottomLeading) {
            if let error = store.errorMessage {
                Label(error, systemImage: "exclamationmark.triangle")
                    .font(.caption)
                    .foregroundStyle(.red)
                    .padding(12)
                    .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 9))
                    .padding(12)
            }
        }
    }

    private var selectedSchedule: ScheduleItem? {
        if let selectedID, let item = store.schedules.first(where: { $0.id == selectedID }) {
            return item
        }
        return store.schedules.first
    }
}

private struct ScheduleRow: View {
    let item: ScheduleItem

    var body: some View {
        HStack(spacing: 9) {
            Circle()
                .fill(statusColor)
                .frame(width: 8, height: 8)
            VStack(alignment: .leading, spacing: 3) {
                Text(item.name).lineLimit(1)
                Text(item.enabled ? item.recurrenceLabel : "Paused")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
        }
        .padding(.vertical, 3)
    }

    private var statusColor: Color {
        if !item.enabled { return .secondary }
        switch item.lastStatus {
        case "failed": return .red
        case "running": return Brand.accent
        default: return .green
        }
    }
}

private struct ScheduleDetail: View {
    let item: ScheduleItem
    let runs: [ScheduleRun]
    let onToggle: () -> Void
    let onRun: () -> Void
    let onEdit: () -> Void
    let onDelete: () -> Void

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                HStack {
                    VStack(alignment: .leading, spacing: 5) {
                        Text(item.name).font(.title2.weight(.semibold))
                        Label(item.enabled ? "Active" : "Paused", systemImage: item.enabled ? "checkmark.circle.fill" : "pause.circle")
                            .foregroundStyle(item.enabled ? Color.green : Color.secondary)
                    }
                    Spacer()
                    Button(item.enabled ? "Pause" : "Resume", action: onToggle)
                    Button("Run Now", action: onRun)
                        .buttonStyle(.borderedProminent)
                        .tint(Brand.accent)
                    Menu {
                        Button("Edit", action: onEdit)
                        Button("Delete", role: .destructive, action: onDelete)
                    } label: {
                        Image(systemName: "ellipsis.circle")
                    }
                    .menuStyle(.borderlessButton)
                }

                detailCard

                VStack(alignment: .leading, spacing: 8) {
                    Text("Prompt").font(.headline)
                    Text(item.prompt)
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(12)
                        .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 10))
                }

                VStack(alignment: .leading, spacing: 10) {
                    Text("Run History").font(.headline)
                    if runs.isEmpty {
                        Text("No runs yet.").foregroundStyle(.secondary)
                    } else {
                        ForEach(runs.prefix(20)) { run in
                            HStack(spacing: 10) {
                                Image(systemName: run.status == "completed" ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                                    .foregroundStyle(run.status == "completed" ? Color.green : Color.red)
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(Self.displayDate(run.startedAt))
                                    if !run.error.isEmpty {
                                        Text(run.error).font(.caption).foregroundStyle(.red).lineLimit(2)
                                    }
                                }
                                Spacer()
                                if !run.outputPath.isEmpty {
                                    Button("Open Note") {
                                        NSWorkspace.shared.open(URL(fileURLWithPath: run.outputPath))
                                    }
                                }
                                Text(run.status.capitalized).foregroundStyle(.secondary)
                            }
                            .padding(10)
                            .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 9))
                        }
                    }
                }
            }
            .padding(24)
        }
    }

    private var detailCard: some View {
        Grid(alignment: .leading, horizontalSpacing: 18, verticalSpacing: 10) {
            detailRow("Schedule", item.recurrenceLabel)
            detailRow("Next run", Self.displayDate(item.nextRunAt))
            detailRow("Last run", Self.displayDate(item.lastRunAt))
            detailRow("Workspace", item.workspace)
            detailRow("Tools", item.tools.isEmpty ? "None" : item.tools.joined(separator: ", "))
            detailRow("Obsidian", item.outputSubdirectory)
            if !item.lastError.isEmpty { detailRow("Last error", item.lastError) }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(14)
        .background(Brand.accentSoft, in: RoundedRectangle(cornerRadius: 12))
    }

    private func detailRow(_ label: String, _ value: String) -> some View {
        GridRow {
            Text(label).foregroundStyle(.secondary)
            Text(value).textSelection(.enabled)
        }
    }

    private static func displayDate(_ value: String?) -> String {
        guard let value, !value.isEmpty else { return "—" }
        let formatter = ISO8601DateFormatter()
        guard let date = formatter.date(from: value) else { return value }
        return date.formatted(date: .abbreviated, time: .shortened)
    }
}

private struct ScheduleEditorView: View {
    let item: ScheduleItem?
    let onSave: (ScheduleDraft) -> Void
    @Environment(\.dismiss) private var dismiss

    @State private var name: String
    @State private var prompt: String
    @State private var workspace: String
    @State private var recurrence: String
    @State private var clock: Date
    @State private var runAt: Date
    @State private var weekday: Int
    @State private var cron: String
    @State private var outputSubdirectory: String
    @State private var outputWasCustomized: Bool
    @State private var browserEnabled: Bool
    @State private var gmailEnabled: Bool
    @State private var projectReadingEnabled: Bool
    @State private var webpageEnabled: Bool
    @State private var skillsEnabled: Bool

    init(item: ScheduleItem?, defaultWorkspace: String, onSave: @escaping (ScheduleDraft) -> Void) {
        self.item = item
        self.onSave = onSave
        let calendar = Calendar.current
        var components = calendar.dateComponents([.year, .month, .day], from: Date())
        let timeParts = (item?.time ?? "21:00").split(separator: ":").compactMap { Int($0) }
        components.hour = timeParts.first ?? 21
        components.minute = timeParts.count > 1 ? timeParts[1] : 0
        let parsedRunAt = item.flatMap { ISO8601DateFormatter().date(from: $0.runAt) }
        _name = State(initialValue: item?.name ?? "")
        _prompt = State(initialValue: item?.prompt ?? "")
        _workspace = State(initialValue: item?.workspace ?? defaultWorkspace)
        _recurrence = State(initialValue: item?.recurrence ?? "daily")
        _clock = State(initialValue: calendar.date(from: components) ?? Date())
        _runAt = State(initialValue: parsedRunAt ?? Date().addingTimeInterval(3600))
        _weekday = State(initialValue: item?.weekday ?? 0)
        _cron = State(initialValue: item?.cron ?? "0 21 * * *")
        _outputSubdirectory = State(initialValue: item?.outputSubdirectory ?? "LangBridge")
        _outputWasCustomized = State(initialValue: item != nil)
        let tools = Set(item?.tools ?? ["browser"])
        _browserEnabled = State(initialValue: tools.contains("browser"))
        _gmailEnabled = State(initialValue: tools.contains("gmail"))
        _projectReadingEnabled = State(initialValue: !tools.isDisjoint(with: ["glob", "grep", "read_file"]))
        _webpageEnabled = State(initialValue: tools.contains("read_webpage"))
        _skillsEnabled = State(initialValue: tools.contains("read_skill"))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text(item == nil ? "New Schedule" : "Edit Schedule")
                .font(.title2.weight(.semibold))
            Form {
                TextField("Name", text: $name)
                TextField("Prompt", text: $prompt, axis: .vertical)
                    .lineLimit(3...8)
                HStack {
                    TextField("Workspace", text: $workspace)
                    Button("Choose…") { chooseWorkspace() }
                }
                Picker("Repeats", selection: $recurrence) {
                    Text("Once").tag("once")
                    Text("Daily").tag("daily")
                    Text("Weekdays").tag("weekdays")
                    Text("Weekly").tag("weekly")
                    Text("Custom cron").tag("cron")
                }
                if recurrence == "once" {
                    DatePicker("Run at", selection: $runAt)
                } else if recurrence == "cron" {
                    TextField("Cron", text: $cron)
                } else {
                    DatePicker("Time", selection: $clock, displayedComponents: .hourAndMinute)
                    if recurrence == "weekly" {
                        Picker("Day", selection: $weekday) {
                            ForEach(Array(["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"].enumerated()), id: \.offset) { index, day in
                                Text(day).tag(index)
                            }
                        }
                    }
                }
                TextField("Obsidian folder", text: Binding(
                    get: { outputSubdirectory },
                    set: { value in
                        outputSubdirectory = value
                        outputWasCustomized = true
                    }
                ))
                Section("Approved tools") {
                    Toggle("Read-only X browser", isOn: $browserEnabled)
                    Toggle("Read-only Gmail", isOn: $gmailEnabled)
                    Toggle("Read project files", isOn: $projectReadingEnabled)
                    Toggle("Read web pages", isOn: $webpageEnabled)
                    Toggle("Read app Skills", isOn: $skillsEnabled)
                }
            }
            .formStyle(.grouped)

            HStack {
                Text("Uses the Mac's current time zone. Background runs cannot request more permissions.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Spacer()
                Button("Cancel") { dismiss() }
                Button("Save") {
                    onSave(makeDraft())
                    dismiss()
                }
                .buttonStyle(.borderedProminent)
                .tint(Brand.accent)
                .disabled(name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                    || prompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                    || workspace.isEmpty
                    || outputSubdirectory.isEmpty)
            }
        }
        .padding(24)
        .frame(width: 640, height: 650)
        .onChange(of: name) {
            guard !outputWasCustomized else { return }
            let component = name
                .replacingOccurrences(of: "/", with: "-")
                .replacingOccurrences(of: ":", with: "-")
                .trimmingCharacters(in: CharacterSet(charactersIn: " ."))
            outputSubdirectory = "LangBridge/" + (component.isEmpty ? "Scheduled Task" : component)
        }
    }

    private func makeDraft() -> ScheduleDraft {
        let clockFormatter = DateFormatter()
        clockFormatter.dateFormat = "HH:mm"
        let spec: [String: Any]
        switch recurrence {
        case "once": spec = ["run_at": ISO8601DateFormatter().string(from: runAt)]
        case "cron": spec = ["cron": cron]
        case "weekly": spec = ["time": clockFormatter.string(from: clock), "weekday": weekday]
        default: spec = ["time": clockFormatter.string(from: clock)]
        }
        var tools: [String] = []
        if browserEnabled { tools.append("browser") }
        if gmailEnabled { tools.append("gmail") }
        if projectReadingEnabled { tools += ["glob", "grep", "read_file"] }
        if webpageEnabled { tools.append("read_webpage") }
        if skillsEnabled { tools.append("read_skill") }
        return ScheduleDraft(
            name: name.trimmingCharacters(in: .whitespacesAndNewlines),
            prompt: prompt.trimmingCharacters(in: .whitespacesAndNewlines),
            workspace: workspace,
            recurrence: recurrence,
            scheduleSpec: spec,
            tools: tools,
            outputSubdirectory: outputSubdirectory
        )
    }

    private func chooseWorkspace() {
        let panel = NSOpenPanel()
        panel.title = "Choose the task workspace"
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        guard panel.runModal() == .OK, let url = panel.url else { return }
        workspace = url.standardizedFileURL.path
    }
}
