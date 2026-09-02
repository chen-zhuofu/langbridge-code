import Foundation

struct ApprovalRequest {
    let summary: String
    let details: String
}

struct AgentQuestion {
    let text: String
    let options: [String]
}

enum PermissionMode: String, CaseIterable {
    case manual
    case auto
    case bypass

    var label: String {
        switch self {
        case .manual: "Manual"
        case .auto: "Auto"
        case .bypass: "Bypass"
        }
    }

    var icon: String {
        switch self {
        case .manual: "hand.raised"
        case .auto: "checkmark.shield"
        case .bypass: "bolt.fill"
        }
    }

    var help: String {
        switch self {
        case .manual: "Ask before high-risk actions"
        case .auto: "Use two-stage background safety checks"
        case .bypass: "Run without permission checks"
        }
    }
}

@MainActor
final class TaskSession: ObservableObject, Identifiable {
    let id = UUID()
    let workspace: Workspace

    @Published var title: String
    @Published var messages: [ChatEntry] = []
    @Published var state: TaskRunState = .starting
    @Published var workflow = ""
    @Published var liveText = ""
    @Published var liveRole = "LangBridge"
    @Published var contextLine = ""
    @Published var cacheHitRate: Double = 0
    @Published var model = ""
    @Published var models: [ModelItem] = []
    @Published var skills: [SkillItem] = []
    @Published var gitBranch = ""
    @Published var engineCWD = ""
    @Published var turnActive = false
    @Published private(set) var activeTurnID: UUID?
    @Published private(set) var sessionPath: String?
    @Published var permissionMode: PermissionMode = .manual
    @Published var queuedCount = 0
    @Published var approval: ApprovalRequest?
    @Published var question: AgentQuestion?
    @Published private(set) var scrollToBottomRequestID = UUID()

    var onSessionList: (([SessionItem]) -> Void)?
    var onBecameIdle: (() -> Void)?
    var onTurnFinished: ((String) -> Void)?
    var onSessionForked: ((SessionItem) -> Void)?

    private let runtime: RuntimeLocator
    private let settings: SettingsStore
    private var bridge: BridgeProcess?
    private var didRequestResume = false
    private var isClosing = false
    private var pendingTurnEndStatus: String?

    var isBlankDraft: Bool {
        sessionPath == nil && messages.isEmpty && title == "New task"
    }

    var canArchive: Bool {
        !state.isRunning && !turnActive && approval == nil && question == nil
    }

    /// Fully idle: ready state with no active turn, approval, or question.
    /// Gates the rewind action both here and in the chat UI.
    var isFullyIdle: Bool {
        state == .ready && !turnActive && approval == nil && question == nil
    }

    /// Fork is only offered for a persisted, fully idle session.
    var canFork: Bool {
        sessionPath != nil && isFullyIdle
    }

    init(
        workspace: Workspace,
        runtime: RuntimeLocator,
        settings: SettingsStore,
        resume: SessionItem? = nil
    ) {
        self.workspace = workspace
        self.runtime = runtime
        self.settings = settings
        sessionPath = resume?.path
        title = resume?.label ?? "New task"
    }

    func start() {
        guard bridge == nil else { return }
        isClosing = false
        state = .starting
        do {
            let configuration = try runtime.launchConfiguration(workspace: workspace, settings: settings)
            let process = BridgeProcess(configuration: configuration)
            process.onEvent = { [weak self, weak process] event in
                guard let self, self.bridge === process else { return }
                self.handle(event)
            }
            process.onError = { [weak self, weak process] text in
                guard let self, self.bridge === process else { return }
                self.append(.init(kind: .system, text: text, style: "error"))
            }
            process.onExit = { [weak self, weak process] code in
                guard let self, self.bridge === process, !self.isClosing else { return }
                self.bridge = nil
                self.state = .failed("Engine exited with code \(code)")
                self.activeTurnID = nil
                self.liveText = ""
                self.append(.init(
                    kind: .system,
                    text: "LangBridge engine exited with code \(code).",
                    style: "error"
                ))
            }
            bridge = process
            try process.start()
        } catch {
            bridge = nil
            state = .failed(error.localizedDescription)
            append(.init(kind: .system, text: error.localizedDescription, style: "error"))
        }
    }

    func sendPrompt(_ rawText: String, images: [ImageAttachment] = []) {
        let text = rawText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty || !images.isEmpty else { return }
        if question != nil {
            guard !text.isEmpty else { return }
            bridge?.send(["type": "answer", "text": text])
            return
        }
        let imagePaths = images.map(\.path)
        if !turnActive {
            let turnID = ensureActiveTurnID()
            append(.init(kind: .user, text: text, turnID: turnID, imagePaths: imagePaths))
            if title == "New task", !text.isEmpty {
                title = Self.makeTitle(text)
            }
        }
        bridge?.send([
            "type": "user_message",
            "text": text,
            "images": imagePaths.map { ["path": $0] },
        ])
    }

    func answerQuestion(_ answer: String) {
        bridge?.send(["type": "answer", "text": answer])
    }

    func resolveApproval(_ approved: Bool) {
        bridge?.send(["type": "approval", "approved": approved])
    }

    func requestScrollToBottom() {
        scrollToBottomRequestID = UUID()
    }

    func stop() {
        bridge?.send(["type": "stop"])
    }

    /// Idle-only: restore the workspace and conversation to immediately
    /// before ``turnID``. The bridge re-validates idleness server-side.
    func rewind(toBackendTurnID turnID: Int) {
        guard isFullyIdle else { return }
        bridge?.send(["type": "rewind_to_turn", "turn_id": turnID])
    }

    func togglePause() {
        bridge?.send(["type": "pause_toggle"])
    }

    func setPermissionMode(_ mode: PermissionMode) {
        bridge?.send(["type": "permission_mode", "value": mode.rawValue])
    }

    func selectModel(_ item: ModelItem) {
        bridge?.send(["type": "set_model", "model": item.id, "provider": item.provider])
    }

    func refreshSkills() {
        bridge?.send(["type": "list_skills"])
    }

    func refreshCredentials() {
        bridge?.send(["type": "reload_credentials"])
    }

    func renameCurrentSession(to title: String) {
        bridge?.send(["type": "rename_session", "title": title])
    }

    /// Idle-only: fork the current session into a new, independent one.
    /// The bridge re-validates idleness and a persisted session server-side.
    func forkSession() {
        guard canFork else { return }
        bridge?.send(["type": "fork_session"])
    }

    func close() {
        isClosing = true
        stopBridge()
    }

    @discardableResult
    func deactivateIfIdle() -> Bool {
        guard !isBlankDraft,
              state != .starting,
              !state.isRunning,
              !turnActive,
              approval == nil,
              question == nil
        else { return false }
        isClosing = true
        stopBridge()
        return true
    }

    func handle(_ event: [String: Any]) {
        guard let type = event["type"] as? String else { return }
        switch type {
        case "hello":
            model = event["model"] as? String ?? model
            gitBranch = event["git_branch"] as? String ?? ""
            engineCWD = event["cwd"] as? String ?? workspace.path
            if let rawSessions = event["sessions"] as? [[String: Any]] {
                onSessionList?(BridgeEventDecoder.sessions(from: ["items": rawSessions]))
            }
            if let rawSkills = event["skills"] as? [[String: Any]] {
                skills = BridgeEventDecoder.skills(from: ["items": rawSkills])
            }
            bridge?.send(["type": "list_models"])
            if let path = sessionPath, !didRequestResume {
                didRequestResume = true
                bridge?.send(["type": "resume_session", "path": path])
            }
        case "state":
            state = TaskRunState(engineState: event["state"] as? String ?? "ready")
            workflow = event["workflow"] as? String ?? ""
            turnActive = event["turn_active"] as? Bool ?? false
            if let rawMode = event["permission_mode"] as? String,
               let mode = PermissionMode(rawValue: rawMode)
            {
                permissionMode = mode
            } else {
                permissionMode = event["yolo"] as? Bool == true ? .bypass : .manual
            }
            queuedCount = event["queued"] as? Int ?? 0
            if event.keys.contains("session_path") {
                let path = event["session_path"] as? String
                sessionPath = path?.isEmpty == false ? path : nil
            }
            if !turnActive {
                activeTurnID = nil
                liveText = ""
                if let status = pendingTurnEndStatus {
                    pendingTurnEndStatus = nil
                    onTurnFinished?(status)
                }
                onBecameIdle?()
            }
        case "context_line":
            contextLine = event["text"] as? String ?? ""
            cacheHitRate = (event["cache_hit_rate"] as? NSNumber)?.doubleValue ?? 0
        case "assistant":
            liveText = ""
            append(.init(
                kind: .assistant,
                text: event["text"] as? String ?? "",
                turnID: ensureActiveTurnID()
            ))
        case "system":
            append(.init(
                kind: .system,
                text: event["text"] as? String ?? "",
                style: event["style"] as? String
            ))
        case "trace":
            liveText = ""
            append(.init(
                kind: .trace,
                text: event["text"] as? String ?? "",
                turnID: ensureActiveTurnID(),
                role: event["role"] as? String,
                tool: event["tool"] as? String
            ))
        case "stream":
            _ = ensureActiveTurnID()
            liveRole = event["role"] as? String ?? "LangBridge"
            liveText = event["text"] as? String ?? ""
        case "queued":
            append(.init(
                kind: .user,
                text: event["text"] as? String ?? "",
                imagePaths: BridgeEventDecoder.imagePaths(from: event)
            ))
        case "turn_started":
            let text = event["text"] as? String ?? ""
            let backendTurnID = event["turn_id"] as? Int
            let checkpointAvailable = event["checkpoint_available"] as? Bool ?? false
            // A queued message already has a locally-appended bubble; attach
            // the backend turn id to it instead of appending a duplicate.
            // FIFO (firstIndex) so two identical queued prompts resolve in
            // the chronological order they were dispatched, not reversed.
            if let index = messages.firstIndex(where: {
                $0.kind == .user && $0.backendTurnID == nil && $0.text == text
            }) {
                messages[index].turnID = ensureActiveTurnID()
                messages[index].backendTurnID = backendTurnID
                messages[index].checkpointAvailable = checkpointAvailable
            } else {
                append(.init(
                    kind: .user,
                    text: text,
                    turnID: ensureActiveTurnID(),
                    backendTurnID: backendTurnID,
                    checkpointAvailable: checkpointAvailable,
                    imagePaths: BridgeEventDecoder.imagePaths(from: event)
                ))
            }
        case "turn_id_assigned":
            // Backfill the immediately-sent prompt's bubble; never appends.
            guard let backendTurnID = event["turn_id"] as? Int else { break }
            let checkpointAvailable = event["checkpoint_available"] as? Bool ?? false
            if let index = messages.lastIndex(where: { $0.kind == .user && $0.backendTurnID == nil }) {
                messages[index].backendTurnID = backendTurnID
                messages[index].checkpointAvailable = checkpointAvailable
            }
        case "approval_request":
            approval = ApprovalRequest(
                summary: event["summary"] as? String ?? "Approval required",
                details: event["details"] as? String ?? ""
            )
        case "approval_resolved":
            approval = nil
        case "question":
            question = AgentQuestion(
                text: event["text"] as? String ?? "",
                options: event["options"] as? [String] ?? []
            )
        case "answer_recorded":
            question = nil
            if let text = event["text"] as? String, !text.isEmpty {
                append(.init(kind: .user, text: text))
            }
        case "turn_end":
            liveText = ""
            activeTurnID = nil
            pendingTurnEndStatus = event["status"] as? String ?? "ok"
            refreshSkills()
            if event["status"] as? String == "error" {
                append(.init(
                    kind: .system,
                    text: event["message"] as? String ?? "The task failed.",
                    style: "error"
                ))
            }
        case "sessions":
            onSessionList?(BridgeEventDecoder.sessions(from: event))
        case "session_resumed":
            if let path = event["path"] as? String, !path.isEmpty {
                sessionPath = path
            }
            if let label = event["label"] as? String {
                title = SessionItem.displayLabel(path: sessionPath ?? "", label: label)
            }
            activeTurnID = nil
            liveText = ""
            if let conversation = event["conversation"] as? [[String: Any]] {
                replaceMessages(withConversation: conversation)
            } else {
                messages = []
                if let preview = event["preview"] as? String, !preview.isEmpty {
                    append(.init(kind: .system, text: preview))
                }
            }
        case "rewound":
            activeTurnID = nil
            liveText = ""
            if let conversation = event["conversation"] as? [[String: Any]] {
                replaceMessages(withConversation: conversation)
            }
        case "session_renamed":
            let renamedPath = event["path"] as? String
            let isCurrent = event["current"] as? Bool == true
            if isCurrent || renamedPath == sessionPath {
                if let label = event["label"] as? String {
                    title = SessionItem.displayLabel(path: renamedPath ?? sessionPath ?? "", label: label)
                }
            }
        case "session_forked":
            if let path = event["path"] as? String, !path.isEmpty,
               let label = event["label"] as? String
            {
                onSessionForked?(SessionItem(path: path, label: label))
            }
        case "models":
            if let items = event["items"] as? [[String: Any]] {
                models = items.compactMap { item in
                    guard let id = item["id"] as? String else { return nil }
                    return ModelItem(id: id, provider: item["provider"] as? String ?? "")
                }
            }
            model = event["current"] as? String ?? model
        case "skills":
            skills = BridgeEventDecoder.skills(from: event)
        case "model":
            model = event["model"] as? String ?? model
        case "credentials_reloaded":
            model = event["model"] as? String ?? model
        default:
            break
        }
    }

    private func append(_ entry: ChatEntry) {
        guard !entry.text.isEmpty || !entry.imagePaths.isEmpty else { return }
        messages.append(entry)
        if messages.count > 3_000 {
            messages.removeFirst(messages.count - 3_000)
        }
    }

    /// Rebuilds the full transcript from a backend conversation payload, used
    /// by both ``session_resumed`` and ``rewound`` so a rewind's replacement
    /// transcript is built identically to a normal resume.
    private func replaceMessages(withConversation conversation: [[String: Any]]) {
        messages = []
        for item in conversation {
            let kind: ChatEntryKind = item["role"] as? String == "user" ? .user : .assistant
            append(.init(
                kind: kind,
                text: item["text"] as? String ?? "",
                backendTurnID: item["turn_id"] as? Int,
                checkpointAvailable: item["checkpoint_available"] as? Bool ?? false,
                imagePaths: BridgeEventDecoder.imagePaths(from: item)
            ))
        }
    }

    private func stopBridge() {
        guard let process = bridge else { return }
        bridge = nil
        didRequestResume = false
        process.onEvent = nil
        process.onError = nil
        process.onExit = nil
        process.shutdown()
    }

    private func ensureActiveTurnID() -> UUID {
        if let activeTurnID { return activeTurnID }
        let id = UUID()
        activeTurnID = id
        return id
    }

    private static func makeTitle(_ text: String) -> String {
        let firstLine = text.split(separator: "\n", maxSplits: 1).first.map(String.init) ?? text
        return firstLine.count > 52 ? String(firstLine.prefix(49)) + "…" : firstLine
    }
}
