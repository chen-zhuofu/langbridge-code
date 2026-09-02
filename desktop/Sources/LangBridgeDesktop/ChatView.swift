import AppKit
import SwiftUI

extension View {
    /// `.textSelection(.enabled)` and `.textSelection(.disabled)` are
    /// distinct concrete `TextSelectability` types, so a ternary between
    /// them does not type-check inline. This centralizes the toggle so
    /// selection can be suspended for finalized transcript content while a
    /// live stream update is in flight, without rebuilding a selectable
    /// overlay on every streaming layout pass.
    @ViewBuilder
    func selectableText(_ isSelectable: Bool) -> some View {
        if isSelectable {
            textSelection(.enabled)
        } else {
            textSelection(.disabled)
        }
    }
}

struct ChatView: View {
    @ObservedObject var task: TaskSession
    let isActiveColumn: Bool
    let onActivate: () -> Void
    let onCloseColumn: () -> Void
    @Environment(\.wallpaperActive) private var wallpaperActive
    @State private var draft = ""
    @State private var attachments: [ImageAttachment] = []
    @State private var pendingRewindTurnID: Int?
    @State private var isPinnedToBottom = true
    @State private var pendingFollowTask: Task<Void, Never>?
    @State private var pendingFollowToken: UUID?
    @State private var topMarkerOffset: CGFloat = 0

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()

            conversationContent

            if let approval = task.approval {
                ApprovalCard(request: approval) { approved in
                    task.resolveApproval(approved)
                }
                .padding(.horizontal, 24)
                .padding(.bottom, 8)
            }

            if let question = task.question {
                QuestionCard(question: question) { answer in
                    task.answerQuestion(answer)
                }
                .padding(.horizontal, 24)
                .padding(.bottom, 8)
            }

            composer
        }
        .background {
            if wallpaperActive {
                Color.black.opacity(0.46)
            } else {
                Color(nsColor: .textBackgroundColor)
            }
        }
        .overlay {
            Rectangle()
                .stroke(isActiveColumn ? Brand.accent.opacity(0.75) : .clear, lineWidth: 1)
                .allowsHitTesting(false)
        }
        .onDisappear {
            attachments.forEach(ImageAttachmentStore.remove)
        }
    }

    @ViewBuilder
    private var conversationContent: some View {
        if task.messages.isEmpty && task.liveText.isEmpty {
            emptyState
        } else {
            transcript
        }
    }

    private var header: some View {
        ViewThatFits(in: .horizontal) {
            headerContent(compact: false, showsMetadata: true)
                .frame(minWidth: 500)
            headerContent(compact: true, showsMetadata: true)
                .frame(minWidth: 360)
            headerContent(compact: true, showsMetadata: false)
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 11)
        .background {
            if wallpaperActive {
                Rectangle().fill(.ultraThinMaterial)
            } else {
                Rectangle().fill(.bar)
            }
        }
    }

    private func headerContent(compact: Bool, showsMetadata: Bool) -> some View {
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 3) {
                Text(task.title)
                    .font(.headline)
                    .lineLimit(1)
                if showsMetadata {
                    HStack(spacing: 6) {
                        Image(systemName: "folder")
                        Text(task.workspace.name)
                            .lineLimit(1)
                            .truncationMode(.middle)
                        if !task.gitBranch.isEmpty {
                            Text("·")
                            Image(systemName: "arrow.triangle.branch")
                            Text(task.gitBranch)
                                .lineLimit(1)
                        }
                    }
                    .lineLimit(1)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                }
            }
            .contentShape(Rectangle())
            .onTapGesture(perform: onActivate)
            Spacer()

            if task.turnActive {
                Button {
                    task.togglePause()
                } label: {
                    Image(systemName: task.state == .waiting ? "play.fill" : "pause.fill")
                }
                .buttonStyle(.borderless)
                .help("Pause or resume")
            }

            Menu {
                ForEach(PermissionMode.allCases, id: \.self) { mode in
                    Button {
                        task.setPermissionMode(mode)
                    } label: {
                        if task.permissionMode == mode {
                            Label(mode.label, systemImage: "checkmark")
                        } else {
                            Label(mode.label, systemImage: mode.icon)
                        }
                    }
                    .help(mode.help)
                }
            } label: {
                if compact {
                    Image(systemName: task.permissionMode.icon)
                        .foregroundStyle(task.permissionMode == .manual ? .secondary : Brand.accent)
                } else {
                    Label(task.permissionMode.label, systemImage: task.permissionMode.icon)
                        .foregroundStyle(task.permissionMode == .manual ? .secondary : Brand.accent)
                }
            }
            .buttonStyle(.borderless)
            .help(task.permissionMode.help)

            Button {
                task.forkSession()
            } label: {
                if compact {
                    Image(systemName: "arrow.triangle.branch")
                } else {
                    Label("Fork", systemImage: "arrow.triangle.branch")
                }
            }
            .buttonStyle(.borderless)
            .disabled(!task.canFork)
            .help(task.canFork ? "Fork this session into a new task" : "Fork requires an idle, saved session")

            Menu {
                ForEach(task.models) { item in
                    Button {
                        task.selectModel(item)
                    } label: {
                        if item.id == task.model {
                            Label(item.id, systemImage: "checkmark")
                        } else {
                            Text(item.id)
                        }
                    }
                }
            } label: {
                HStack(spacing: 5) {
                    Text(task.model.isEmpty ? "Model" : task.model)
                        .lineLimit(1)
                    Image(systemName: "chevron.down")
                        .font(.caption2)
                }
            }
            .menuStyle(.borderlessButton)
            .fixedSize()

            Button(action: onCloseColumn) {
                Image(systemName: "xmark")
            }
            .buttonStyle(.borderless)
            .help("Close this column (the task keeps running)")
        }
    }

    private static let transcriptScrollSpace = "chatTranscriptScroll"

    /// Whether completed transcript content should render as selectable
    /// text. Selection is suspended for the whole active-turn lifecycle,
    /// not just while ``TaskSession/liveText`` happens to be non-empty:
    /// ``TaskSession`` clears `liveText` on every assistant/trace event
    /// even mid-turn, so keying off it would still rebuild every
    /// SelectionOverlay on each such transition. Keying off `turnActive`
    /// keeps selection suspended through those trace/live gaps and
    /// restores it only once the turn is truly idle.
    private var isTranscriptSelectable: Bool {
        !task.turnActive
    }

    private var transcript: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 18) {
                    ForEach(TranscriptGrouper.group(task.messages)) { item in
                        switch item {
                        case let .entry(entry):
                            ChatEntryView(
                                entry: entry,
                                canRewind: RewindAvailability.isAvailable(entry: entry, taskIsIdle: task.isFullyIdle),
                                isSelectable: isTranscriptSelectable
                            ) {
                                pendingRewindTurnID = entry.backendTurnID
                            }
                        case let .activity(activity):
                            TraceActivityView(
                                activity: activity,
                                isActive: activity.turnID != nil && activity.turnID == task.activeTurnID,
                                isSelectable: isTranscriptSelectable
                            )
                        }
                    }
                    if !task.liveText.isEmpty {
                        LiveActivityView(role: task.liveRole, text: task.liveText)
                    }
                    Color.clear.frame(height: 1).id("bottom")
                }
                .frame(maxWidth: 820)
                .frame(maxWidth: .infinity)
                .padding(.horizontal, 28)
                .padding(.vertical, 24)
                .background(
                    // Measures the content container's own top offset, not a
                    // lazily-virtualized child, so it stays accurate even
                    // when the scroll position is far from the transcript's
                    // start and rows near the top aren't instantiated.
                    GeometryReader { geometry in
                        Color.clear.preference(
                            key: TopMarkerOffsetPreferenceKey.self,
                            value: geometry.frame(in: .named(Self.transcriptScrollSpace)).minY
                        )
                    }
                )
            }
            .coordinateSpace(name: Self.transcriptScrollSpace)
            .onPreferenceChange(TopMarkerOffsetPreferenceKey.self) { value in
                if TranscriptAutoFollow.didUserScrollAway(previousTopOffset: topMarkerOffset, newTopOffset: value) {
                    isPinnedToBottom = false
                    cancelPendingFollow()
                }
                topMarkerOffset = value
            }
            .overlay(alignment: .bottomTrailing) {
                if !isPinnedToBottom {
                    jumpToLatestButton(proxy)
                }
            }
            .onChange(of: task.messages.count) {
                scheduleAutoFollow(proxy)
            }
            .onChange(of: task.liveText) {
                // liveText also goes empty between trace/assistant events
                // mid-turn, not just when the turn truly ends, so every
                // transition (empty or not) routes through the same
                // bounded/coalesced follow rather than scrolling directly.
                scheduleAutoFollow(proxy)
            }
            .onChange(of: task.activeTurnID) {
                scheduleAutoFollow(proxy)
            }
            .onChange(of: task.turnActive) {
                finalizeFollowAtTurnEnd(proxy)
            }
            .onChange(of: task.scrollToBottomRequestID) {
                isPinnedToBottom = true
                cancelPendingFollow()
                DispatchQueue.main.async {
                    proxy.scrollTo("bottom", anchor: .bottom)
                }
            }
            .onAppear {
                isPinnedToBottom = true
                DispatchQueue.main.async {
                    proxy.scrollTo("bottom", anchor: .bottom)
                }
            }
            .onDisappear {
                cancelPendingFollow()
            }
        }
        .simultaneousGesture(TapGesture().onEnded(onActivate))
        .alert(
            "Restore workspace to before this message?",
            isPresented: rewindConfirmationIsPresented,
            actions: rewindConfirmationActions,
            message: rewindConfirmationMessage
        )
    }

    /// Bounds live-stream auto-scroll to one pending scroll at a time so a
    /// dense run of ``TaskSession/liveText`` mutations (and bursts of
    /// completed trace/assistant messages) coalesce into a single scrollTo
    /// per ``TranscriptAutoFollow/followInterval`` instead of one
    /// synchronous ScrollViewReader scroll per mutation. A token identifies
    /// the in-flight follow so cancelled or superseded work can neither
    /// clear a newer pending follow nor perform a stale scroll.
    private func scheduleAutoFollow(_ proxy: ScrollViewProxy) {
        guard TranscriptAutoFollow.shouldScheduleFollow(
            isPinnedToBottom: isPinnedToBottom,
            hasPendingFollow: pendingFollowTask != nil
        ) else { return }
        let token = UUID()
        pendingFollowToken = token
        pendingFollowTask = Task { @MainActor in
            do {
                try await Task.sleep(for: TranscriptAutoFollow.followInterval)
            } catch {
                return
            }
            guard !Task.isCancelled,
                  TranscriptAutoFollow.shouldApplyCompletedFollow(currentToken: pendingFollowToken, completedToken: token)
            else { return }
            pendingFollowTask = nil
            pendingFollowToken = nil
            guard isPinnedToBottom else { return }
            proxy.scrollTo("bottom", anchor: .bottom)
        }
    }

    private func cancelPendingFollow() {
        pendingFollowTask?.cancel()
        pendingFollowTask = nil
        pendingFollowToken = nil
    }

    /// Once a turn is truly idle (not merely between trace/assistant
    /// chunks), any still-pending coalesced follow is superseded by one
    /// definitive settle: scroll to bottom if and only if the user is
    /// still pinned, so someone who deliberately scrolled away mid-turn
    /// is not yanked back once streaming stops.
    private func finalizeFollowAtTurnEnd(_ proxy: ScrollViewProxy) {
        guard !task.turnActive else { return }
        cancelPendingFollow()
        guard isPinnedToBottom else { return }
        withAnimation(.easeOut(duration: 0.18)) { proxy.scrollTo("bottom", anchor: .bottom) }
    }

    private func jumpToLatestButton(_ proxy: ScrollViewProxy) -> some View {
        Button {
            isPinnedToBottom = true
            cancelPendingFollow()
            withAnimation(.easeOut(duration: 0.18)) {
                proxy.scrollTo("bottom", anchor: .bottom)
            }
        } label: {
            Image(systemName: "arrow.down.circle.fill")
                .font(.title2)
                .foregroundStyle(Brand.accent)
        }
        .buttonStyle(.borderless)
        .padding(8)
        .background(.thinMaterial, in: Circle())
        .shadow(radius: 3)
        .padding(16)
        .help("Jump to latest output")
        .transition(.opacity)
    }

    private var rewindConfirmationIsPresented: Binding<Bool> {
        Binding(
            get: { pendingRewindTurnID != nil },
            set: { isPresented in
                if !isPresented { pendingRewindTurnID = nil }
            }
        )
    }

    @ViewBuilder
    private func rewindConfirmationActions() -> some View {
        Button("Restore", role: .destructive, action: confirmPendingRewind)
        Button("Cancel", role: .cancel) { pendingRewindTurnID = nil }
    }

    private func rewindConfirmationMessage() -> some View {
        Text("This discards workspace changes and conversation after this point. This cannot be undone from the app.")
    }

    private func confirmPendingRewind() {
        guard let turnID = pendingRewindTurnID else { return }
        task.rewind(toBackendTurnID: turnID)
        pendingRewindTurnID = nil
    }

    private var emptyState: some View {
        VStack(spacing: 0) {
            Spacer(minLength: 8)
            ViewThatFits(in: .vertical) {
                regularEmptyState
                    .fixedSize(horizontal: false, vertical: true)
                compactEmptyState
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 8)
        }
        .padding(.horizontal, 18)
        .padding(.vertical, 10)
        .contentShape(Rectangle())
        .onTapGesture(perform: onActivate)
    }

    private var regularEmptyState: some View {
        VStack(spacing: 18) {
            LangbridgeMark(size: 74)
            VStack(spacing: 7) {
                Text("What should we build?")
                    .font(.system(size: 25, weight: .semibold, design: .rounded))
                Text("LangBridge can inspect this project, plan work, edit files, and run checks.")
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
            }
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 10) { suggestionButtons }
                VStack(spacing: 8) { suggestionButtons }
            }
        }
    }

    private var compactEmptyState: some View {
        VStack(spacing: 10) {
            LangbridgeMark(size: 42)
            Text("What should we build?")
                .font(.system(size: 18, weight: .semibold, design: .rounded))
                .multilineTextAlignment(.center)
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 6) { suggestionButtons }
                VStack(spacing: 5) { suggestionButtons }
            }
        }
    }

    @ViewBuilder
    private var suggestionButtons: some View {
        SuggestionButton("Fix a bug") { draft = "Find and fix the most important failing test." }
        SuggestionButton("Explain the repo") { draft = "Explain how this repository is structured." }
        SuggestionButton("Build a feature") { draft = "Help me design and build a new feature." }
    }

    private var composer: some View {
        ComposerView(
            text: $draft,
            attachments: $attachments,
            isRunning: task.turnActive,
            isAnsweringQuestion: task.question != nil,
            statusText: composerStatusText,
            statusColor: task.state.color,
            cacheText: cacheLabel,
            contextText: compactContextLabel,
            contextDetails: task.contextLine,
            skills: task.skills,
            onSend: send,
            onStop: task.stop,
            onSkillsRequested: task.refreshSkills
        )
        .simultaneousGesture(TapGesture().onEnded(onActivate))
        .padding(.horizontal, 24)
        .padding(.bottom, 12)
        .fixedSize(horizontal: false, vertical: true)
        .layoutPriority(1)
    }

    private var composerStatusText: String {
        let status = task.workflow.isEmpty ? task.state.label : task.workflow
        guard task.queuedCount > 0 else { return status }
        return "\(status) · \(task.queuedCount) queued"
    }

    private var cacheLabel: String {
        "Cache \(Int((task.cacheHitRate * 100).rounded()))%"
    }

    private var compactContextLabel: String {
        let parts = task.contextLine.split(whereSeparator: \Character.isWhitespace)
        guard let contextIndex = parts.firstIndex(where: { $0.lowercased() == "context" }),
              parts.indices.contains(contextIndex + 1)
        else { return "Context —" }
        return "Context \(parts[contextIndex + 1])"
    }

    private func send() {
        let message = draft
        guard !message.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                || !attachments.isEmpty
        else { return }
        let sentAttachments = attachments
        draft = ""
        attachments = []
        task.sendPrompt(message, images: sentAttachments)
    }
}

private struct TraceActivityView: View {
    let activity: TraceActivity
    let isActive: Bool
    var isSelectable: Bool = true
    @State private var expandedAfterCompletion = false
    @State private var collapsedWhileActive = false

    private var isExpanded: Bool {
        isActive ? !collapsedWhileActive : expandedAfterCompletion
    }

    var body: some View {
        VStack(alignment: .leading, spacing: isExpanded ? 10 : 0) {
            Button {
                withAnimation(.easeInOut(duration: 0.16)) {
                    if isActive {
                        collapsedWhileActive.toggle()
                    } else {
                        expandedAfterCompletion.toggle()
                    }
                }
            } label: {
                HStack(spacing: 8) {
                    if isActive {
                        ProgressView()
                            .controlSize(.small)
                            .tint(Brand.accent)
                    } else {
                        Image(systemName: "sparkles")
                            .foregroundStyle(Brand.accent)
                    }
                    Text(isExpanded ? "Hide activity" : "Show activity")
                        .font(.callout.weight(.semibold))
                    Text("\(activity.entries.count)")
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(.secondary)
                    Spacer()
                    Image(systemName: isExpanded ? "chevron.up" : "chevron.down")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(.secondary)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)

            if isExpanded {
                VStack(alignment: .leading, spacing: 10) {
                    ForEach(activity.entries) { entry in
                        ChatEntryView(entry: entry, isSelectable: isSelectable)
                    }
                }
                .transition(.opacity.combined(with: .move(edge: .top)))
            }
        }
        .padding(11)
        .background(Brand.accentSoft, in: RoundedRectangle(cornerRadius: 10))
        .onChange(of: isActive) {
            if !isActive {
                expandedAfterCompletion = false
                collapsedWhileActive = false
            }
        }
    }
}

private struct ChatEntryView: View {
    let entry: ChatEntry
    var canRewind: Bool = false
    var isSelectable: Bool = true
    var onRewind: (() -> Void)? = nil
    @State private var isHovering = false

    /// Default-safe: existing call sites (e.g. inside collapsed trace
    /// activity groups) can keep passing only ``entry``. ``onRewind`` is
    /// kept as the last parameter so trailing-closure call sites still
    /// bind it correctly.
    init(entry: ChatEntry, canRewind: Bool = false, isSelectable: Bool = true, onRewind: (() -> Void)? = nil) {
        self.entry = entry
        self.canRewind = canRewind
        self.isSelectable = isSelectable
        self.onRewind = onRewind
    }

    var body: some View {
        switch entry.kind {
        case .user:
            userBubble
        case .assistant:
            assistantBubble
        case .trace:
            traceBubble
        case .system:
            systemLabel
        }
    }

    private var userBubble: some View {
        HStack {
            Spacer(minLength: 80)
            userBubbleContent
                .padding(.horizontal, 14)
                .padding(.vertical, 10)
                .background(Brand.accentSoft, in: RoundedRectangle(cornerRadius: 14))
                .overlay(alignment: .topTrailing) { rewindButton }
                .onHover { hovering in isHovering = hovering }
        }
    }

    private var userBubbleContent: some View {
        VStack(alignment: .trailing, spacing: 8) {
            if !entry.imagePaths.isEmpty {
                userImages
            }
            if !entry.text.isEmpty {
                Text(entry.text)
                    .selectableText(isSelectable)
            }
        }
    }

    private var userImages: some View {
        ScrollView(.horizontal) {
            HStack(spacing: 7) {
                ForEach(entry.imagePaths, id: \.self) { path in
                    if let image = NSImage(contentsOfFile: path) {
                        Image(nsImage: image)
                            .resizable()
                            .scaledToFit()
                            .frame(maxWidth: 260, maxHeight: 190)
                            .clipShape(RoundedRectangle(cornerRadius: 10))
                    } else {
                        Label("Image unavailable", systemImage: "photo")
                            .foregroundStyle(.secondary)
                    }
                }
            }
        }
        .scrollIndicators(.hidden)
    }

    @ViewBuilder
    private var rewindButton: some View {
        if canRewind, isHovering {
            Button {
                onRewind?()
            } label: {
                Image(systemName: "arrow.uturn.backward.circle.fill")
                    .foregroundStyle(Brand.accent)
                    .background(Circle().fill(.background))
            }
            .buttonStyle(.plain)
            .help("Restore workspace to before this message")
            .padding(6)
        }
    }

    private var assistantBubble: some View {
        HStack(alignment: .top, spacing: 10) {
            LangbridgeMark(size: 23)
            MarkdownContentView(source: entry.text, isSelectable: isSelectable)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private var traceBubble: some View {
        HStack(alignment: .top, spacing: 9) {
            Image(systemName: entry.tool == "bash" ? "terminal" : "wrench.and.screwdriver")
                .foregroundStyle(Brand.accent)
                .frame(width: 18)
            VStack(alignment: .leading, spacing: 3) {
                Text(entry.tool ?? entry.role ?? "Agent activity")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.secondary)
                Text(entry.text)
                    .font(.system(.callout, design: entry.tool == "bash" ? .monospaced : .default))
                    .selectableText(isSelectable)
                    .lineLimit(12)
            }
        }
        .padding(11)
        .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 10))
    }

    private var systemLabel: some View {
        Label(entry.text, systemImage: entry.style == "error" ? "exclamationmark.triangle" : "info.circle")
            .font(.callout)
            .foregroundStyle(entry.style == "error" ? Color.red : Color.secondary)
            .selectableText(isSelectable)
    }
}

/// Pure decision rules for coalescing transcript auto-follow scrolling
/// during dense streaming. Kept free of SwiftUI/AppKit types so the
/// bounded/coalesced behavior is directly unit-testable.
enum TranscriptAutoFollow {
    static let followInterval: Duration = .milliseconds(120)
    static let scrollAwayEpsilon: CGFloat = 1

    /// The top-of-transcript marker's offset only moves when the scroll
    /// view's actual content offset changes: appending streamed content
    /// below the fold never repositions it, since nothing earlier in the
    /// layout shifts. Its offset increasing (moving toward the viewport's
    /// top) beyond a small epsilon therefore isolates a real user drag
    /// away from the bottom from mere content growth, which would
    /// otherwise be confused for the user scrolling away once growth
    /// exceeded a fixed distance-from-bottom threshold.
    static func didUserScrollAway(
        previousTopOffset: CGFloat,
        newTopOffset: CGFloat,
        epsilon: CGFloat = scrollAwayEpsilon
    ) -> Bool {
        newTopOffset - previousTopOffset > epsilon
    }

    /// A new follow-scroll should only be scheduled when the user is
    /// pinned to the bottom and no follow is already pending, so dense
    /// stream mutations (or bursts of completed messages) coalesce into
    /// at most one pending scroll.
    static func shouldScheduleFollow(isPinnedToBottom: Bool, hasPendingFollow: Bool) -> Bool {
        isPinnedToBottom && !hasPendingFollow
    }

    /// Whether a follow task that finished sleeping should still apply its
    /// effect (clear pending state and scroll). Only true if it is still
    /// the currently tracked follow: if it was cancelled (`currentToken`
    /// cleared) or superseded by a newer follow (`currentToken` reassigned)
    /// before it woke up, it must not clear or replace that newer work.
    static func shouldApplyCompletedFollow(currentToken: UUID?, completedToken: UUID) -> Bool {
        currentToken == completedToken
    }
}

private struct TopMarkerOffsetPreferenceKey: PreferenceKey {
    static var defaultValue: CGFloat = 0
    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) {
        value = nextValue()
    }
}

private struct LiveActivityView: View {
    let role: String
    let text: String

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            ProgressView()
                .controlSize(.small)
                .tint(Brand.accent)
                .frame(width: 23, height: 23)
            VStack(alignment: .leading, spacing: 4) {
                Text(role)
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(Brand.accent)
                Text(text)
                    .foregroundStyle(.secondary)
                    .lineLimit(8)
            }
        }
    }
}

private struct SuggestionButton: View {
    let title: String
    let action: () -> Void

    init(_ title: String, action: @escaping () -> Void) {
        self.title = title
        self.action = action
    }

    var body: some View {
        Button(title, action: action)
            .buttonStyle(.bordered)
            .controlSize(.small)
    }
}

private struct ApprovalCard: View {
    let request: ApprovalRequest
    let resolve: (Bool) -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: "lock.shield")
                .foregroundStyle(.orange)
                .font(.title3)
            VStack(alignment: .leading, spacing: 5) {
                Text(request.summary)
                    .font(.headline)
                if !request.details.isEmpty {
                    Text(request.details)
                        .font(.system(.caption, design: .monospaced))
                        .lineLimit(6)
                        .textSelection(.enabled)
                }
            }
            Spacer()
            Button("Deny") { resolve(false) }
            Button("Approve") { resolve(true) }
                .buttonStyle(.borderedProminent)
                .tint(Brand.accent)
        }
        .padding(14)
        .background(Color.orange.opacity(0.09), in: RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(Color.orange.opacity(0.25)))
    }
}

private struct QuestionCard: View {
    let question: AgentQuestion
    let answer: (String) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label("LangBridge needs your input", systemImage: "questionmark.bubble")
                .font(.headline)
                .foregroundStyle(Brand.accent)
            Text(question.text)
                .textSelection(.enabled)
            if !question.options.isEmpty {
                HStack {
                    ForEach(question.options, id: \.self) { option in
                        Button(option) { answer(option) }
                            .buttonStyle(.bordered)
                    }
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(14)
        .background(Brand.accentSoft, in: RoundedRectangle(cornerRadius: 12))
    }
}
