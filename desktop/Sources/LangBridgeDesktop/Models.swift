import Foundation
import SwiftUI

struct Workspace: Identifiable, Codable, Hashable {
    let id: UUID
    let path: String

    init(id: UUID = UUID(), path: String) {
        self.id = id
        self.path = URL(fileURLWithPath: path).standardizedFileURL.path
    }

    var name: String {
        URL(fileURLWithPath: path).lastPathComponent
    }
}

struct SessionItem: Identifiable, Hashable {
    let path: String
    let label: String
    let archived: Bool

    init(path: String, label: String, archived: Bool? = nil) {
        self.path = path
        self.label = Self.displayLabel(path: path, label: label)
        self.archived = archived ?? SessionArchive.isArchived(path)
    }

    var id: String { path }

    static func displayLabel(path: String, label: String) -> String {
        let directoryName = URL(fileURLWithPath: path).lastPathComponent
        let generatedLabel = directoryName.hasPrefix("session-")
            ? String(directoryName.dropFirst("session-".count))
            : directoryName
        guard label == generatedLabel else { return label }
        return label.replacingOccurrences(
            of: #"-\d{4}-\d{2}-\d{2}T\d{6}$"#,
            with: "",
            options: .regularExpression
        )
    }
}

struct TaskInboxItem: Identifiable, Equatable {
    let id: UUID
    let taskID: UUID
    let workspaceID: UUID
    let sessionPath: String
    let title: String
    let status: String
    var isUnread: Bool

    init(
        id: UUID = UUID(),
        taskID: UUID,
        workspaceID: UUID,
        sessionPath: String,
        title: String,
        status: String,
        isUnread: Bool = true
    ) {
        self.id = id
        self.taskID = taskID
        self.workspaceID = workspaceID
        self.sessionPath = sessionPath
        self.title = title
        self.status = status
        self.isUnread = isUnread
    }
}

enum SessionArchive {
    private static let markerName = ".session-archived"

    static func isArchived(_ sessionPath: String) -> Bool {
        FileManager.default.fileExists(atPath: markerURL(sessionPath).path)
    }

    static func setArchived(_ archived: Bool, sessionPath: String) throws {
        let marker = markerURL(sessionPath)
        if archived {
            try Data().write(to: marker, options: .atomic)
        } else if FileManager.default.fileExists(atPath: marker.path) {
            try FileManager.default.removeItem(at: marker)
        }
    }

    private static func markerURL(_ sessionPath: String) -> URL {
        URL(fileURLWithPath: sessionPath, isDirectory: true)
            .appendingPathComponent(markerName)
    }
}

struct SidebarTaskIdentity {
    let id: UUID
    let sessionPath: String?
}

enum SidebarEntryID: Equatable {
    case draft(UUID)
    case session(String)
}

enum SidebarSessionLayout {
    static func entryIDs(
        tasks: [SidebarTaskIdentity],
        sessions: [SessionItem]
    ) -> [SidebarEntryID] {
        let listedPaths = Set(sessions.map(\.path))
        let drafts = tasks.compactMap { task -> SidebarEntryID? in
            guard let path = task.sessionPath else { return nil }
            return listedPaths.contains(path) ? nil : .draft(task.id)
        }
        return drafts + sessions.map { .session($0.path) }
    }

    static func unlistedTaskIDs(
        tasks: [SidebarTaskIdentity],
        sessions: [SessionItem]
    ) -> Set<UUID> {
        Set(entryIDs(tasks: tasks, sessions: sessions).compactMap { entry in
            guard case let .draft(id) = entry else { return nil }
            return id
        })
    }
}

struct SessionColumnLayout: Equatable {
    static let maximumColumns = 3

    private(set) var ids: [UUID] = []
    private(set) var activeID: UUID?

    mutating func show(_ id: UUID) {
        if ids.contains(id) {
            activeID = id
            return
        }
        if ids.isEmpty {
            ids = [id]
        } else if let activeID, let index = ids.firstIndex(of: activeID) {
            ids[index] = id
        } else {
            ids[0] = id
        }
        activeID = id
    }

    mutating func openToRight(_ id: UUID) -> Bool {
        if ids.contains(id) {
            activeID = id
            return true
        }
        guard ids.count < Self.maximumColumns else { return false }
        let insertionIndex = activeID.flatMap(ids.firstIndex).map { $0 + 1 } ?? ids.count
        ids.insert(id, at: insertionIndex)
        activeID = id
        return true
    }

    mutating func replaceColumn(at index: Int, with id: UUID) {
        guard ids.indices.contains(index) else { return }
        ids.removeAll { $0 == id }
        let target = min(index, ids.count - 1)
        if ids.indices.contains(target) {
            ids[target] = id
        } else {
            ids.append(id)
        }
        activeID = id
    }

    mutating func hide(_ id: UUID) {
        guard let index = ids.firstIndex(of: id) else { return }
        ids.remove(at: index)
        if activeID == id {
            activeID = ids.isEmpty ? nil : ids[min(index, ids.count - 1)]
        }
    }

    mutating func remove(_ id: UUID) {
        hide(id)
    }
}

struct WallpaperOption: Identifiable, Hashable {
    static let defaultID = "default"
    static let defaultOption = WallpaperOption(
        id: defaultID,
        name: "Default",
        videoURL: nil,
        thumbnailURL: nil
    )

    let id: String
    let name: String
    let videoURL: URL?
    let thumbnailURL: URL?
}

struct ImageAttachment: Identifiable, Hashable {
    let id: UUID
    let path: String
    let name: String

    init(id: UUID = UUID(), path: String, name: String? = nil) {
        self.id = id
        self.path = path
        self.name = name ?? URL(fileURLWithPath: path).lastPathComponent
    }
}

enum ChatEntryKind: String {
    case user
    case assistant
    case trace
    case system
}

struct ChatEntry: Identifiable {
    let id = UUID()
    let kind: ChatEntryKind
    let text: String
    var turnID: UUID?
    /// Durable backend turn number (traces.md ``## Turn N``), stable across
    /// resumes — distinct from ``turnID``, which only groups live trace
    /// activity within this process.
    var backendTurnID: Int?
    var checkpointAvailable: Bool = false
    var role: String?
    var tool: String?
    var style: String?
    var imagePaths: [String] = []
}

/// Pure gating logic for the hover "restore to here" action, kept separate
/// from SwiftUI so it is directly unit-testable.
enum RewindAvailability {
    static func isAvailable(entry: ChatEntry, taskIsIdle: Bool) -> Bool {
        entry.kind == .user
            && entry.backendTurnID != nil
            && entry.checkpointAvailable
            && taskIsIdle
    }
}

struct TraceActivity: Identifiable {
    let id: UUID
    let turnID: UUID?
    let entries: [ChatEntry]
}

enum TranscriptItem: Identifiable {
    case entry(ChatEntry)
    case activity(TraceActivity)

    var id: UUID {
        switch self {
        case let .entry(entry): entry.id
        case let .activity(activity): activity.id
        }
    }
}

enum TranscriptGrouper {
    static func group(_ entries: [ChatEntry]) -> [TranscriptItem] {
        var items: [TranscriptItem] = []
        var traceEntries: [ChatEntry] = []

        func flushTraceEntries() {
            guard let first = traceEntries.first else { return }
            items.append(.activity(.init(
                id: first.id,
                turnID: first.turnID,
                entries: traceEntries
            )))
            traceEntries = []
        }

        for entry in entries {
            if entry.kind == .trace {
                if let first = traceEntries.first, first.turnID != entry.turnID {
                    flushTraceEntries()
                }
                traceEntries.append(entry)
            } else {
                flushTraceEntries()
                items.append(.entry(entry))
            }
        }
        flushTraceEntries()
        return items
    }
}

struct ModelItem: Identifiable, Hashable {
    let id: String
    let provider: String
}

struct SkillItem: Identifiable, Hashable {
    let name: String
    let description: String

    var id: String { name }
}

enum SlashSkillSuggestions {
    static func query(in text: String) -> String? {
        guard text.first == "/" else { return nil }
        let command = text.dropFirst()
        guard !command.contains(where: { $0.isWhitespace }) else { return nil }
        return command.lowercased()
    }

    static func matches(for text: String, in skills: [SkillItem]) -> [SkillItem] {
        guard let query = query(in: text) else { return [] }
        return skills
            .filter { skill in
                query.isEmpty
                    || skill.name.localizedCaseInsensitiveContains(query)
            }
            .sorted { $0.name.localizedStandardCompare($1.name) == .orderedAscending }
    }
}

struct ScheduleItem: Identifiable, Hashable {
    let id: String
    var name: String
    var prompt: String
    var workspace: String
    var recurrence: String
    var time: String
    var weekday: Int?
    var runAt: String
    var cron: String
    var tools: [String]
    var outputSubdirectory: String
    var enabled: Bool
    var nextRunAt: String?
    var lastRunAt: String?
    var lastStatus: String
    var lastError: String
    var lastOutputPath: String

    init?(dictionary: [String: Any]) {
        guard let id = dictionary["id"] as? String,
              let name = dictionary["name"] as? String,
              let prompt = dictionary["prompt"] as? String,
              let workspace = dictionary["workspace"] as? String,
              let recurrence = dictionary["recurrence"] as? String
        else { return nil }
        let spec = dictionary["schedule_spec"] as? [String: Any] ?? [:]
        self.id = id
        self.name = name
        self.prompt = prompt
        self.workspace = workspace
        self.recurrence = recurrence
        time = spec["time"] as? String ?? "21:00"
        weekday = (spec["weekday"] as? NSNumber)?.intValue
        runAt = spec["run_at"] as? String ?? ""
        cron = spec["cron"] as? String ?? ""
        tools = dictionary["tools"] as? [String] ?? []
        outputSubdirectory = dictionary["output_subdirectory"] as? String ?? ""
        enabled = dictionary["enabled"] as? Bool ?? false
        nextRunAt = dictionary["next_run_at"] as? String
        lastRunAt = dictionary["last_run_at"] as? String
        lastStatus = dictionary["last_status"] as? String ?? "never"
        lastError = dictionary["last_error"] as? String ?? ""
        lastOutputPath = dictionary["last_output_path"] as? String ?? ""
    }

    var recurrenceLabel: String {
        switch recurrence {
        case "once": return "Once"
        case "daily": return "Daily at \(time)"
        case "weekdays": return "Weekdays at \(time)"
        case "weekly":
            let days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
            return "Every \(days.indices.contains(weekday ?? -1) ? days[weekday ?? 0] : "week") at \(time)"
        case "cron": return "Cron \(cron)"
        default: return recurrence
        }
    }
}

struct ScheduleRun: Identifiable, Hashable {
    let id: String
    let startedAt: String
    let finishedAt: String
    let status: String
    let outputPath: String
    let error: String

    init?(dictionary: [String: Any]) {
        guard let id = dictionary["id"] as? String else { return nil }
        self.id = id
        startedAt = dictionary["started_at"] as? String ?? ""
        finishedAt = dictionary["finished_at"] as? String ?? ""
        status = dictionary["status"] as? String ?? "unknown"
        outputPath = dictionary["output_path"] as? String ?? ""
        error = dictionary["error"] as? String ?? ""
    }
}

struct ScheduleDraft {
    var name: String
    var prompt: String
    var workspace: String
    var recurrence: String
    var scheduleSpec: [String: Any]
    var tools: [String]
    var outputSubdirectory: String
}

enum TaskRunState: Equatable {
    case starting
    case ready
    case thinking
    case working
    case waiting
    case stopping
    case failed(String)

    init(engineState: String) {
        switch engineState {
        case "ready": self = .ready
        case "thinking": self = .thinking
        case "waiting for approval", "waiting for answer", "paused": self = .waiting
        case "stopping": self = .stopping
        default: self = .working
        }
    }

    var label: String {
        switch self {
        case .starting: "Starting"
        case .ready: "Ready"
        case .thinking: "Thinking"
        case .working: "Working"
        case .waiting: "Needs attention"
        case .stopping: "Stopping"
        case .failed: "Failed"
        }
    }

    var isRunning: Bool {
        switch self {
        case .thinking, .working, .waiting, .stopping: true
        default: false
        }
    }

    var color: Color {
        switch self {
        case .starting: .secondary
        case .ready: .secondary
        case .thinking, .working: Brand.accent
        case .waiting: .orange
        case .stopping: .orange
        case .failed: .red
        }
    }
}

enum Brand {
    static let accent = Color(red: 0.25, green: 0.49, blue: 0.92)
    static let accentSoft = Color(red: 0.25, green: 0.49, blue: 0.92).opacity(0.12)
}

enum BridgeEventDecoder {
    static func decode(line: String) -> [String: Any]? {
        guard let data = line.data(using: .utf8),
              let object = try? JSONSerialization.jsonObject(with: data),
              let event = object as? [String: Any],
              event["type"] as? String != nil
        else {
            return nil
        }
        return event
    }

    static func sessions(from event: [String: Any]) -> [SessionItem] {
        guard let items = event["items"] as? [[String: Any]] else { return [] }
        return items.compactMap { item in
            guard let path = item["path"] as? String,
                  let label = item["label"] as? String
            else { return nil }
            return SessionItem(
                path: path,
                label: label,
                archived: item["archived"] as? Bool
            )
        }
    }

    static func imagePaths(from event: [String: Any]) -> [String] {
        guard let images = event["images"] as? [Any] else { return [] }
        return images.compactMap { item in
            if let path = item as? String { return path }
            if let object = item as? [String: Any] { return object["path"] as? String }
            return nil
        }
    }

    static func skills(from event: [String: Any]) -> [SkillItem] {
        guard let items = event["items"] as? [[String: Any]] else { return [] }
        return items.compactMap { item in
            guard let name = item["name"] as? String, !name.isEmpty else { return nil }
            return SkillItem(
                name: name,
                description: item["description"] as? String ?? ""
            )
        }
    }
}
