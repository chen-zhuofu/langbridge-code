import AppKit
import LangBridgeKeychain
import XCTest
@testable import LangBridgeDesktop

final class ProtocolTests: XCTestCase {
    @MainActor
    func testTaskSessionCacheRateIsAlwaysNumericAndDecodesUpdates() {
        let home = FileManager.default.temporaryDirectory
            .appendingPathComponent("langbridge-cache-rate-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: home) }
        let task = TaskSession(
            workspace: Workspace(path: home.path),
            runtime: RuntimeLocator(environment: [:]),
            settings: SettingsStore(homeDirectory: home)
        )

        XCTAssertEqual(task.cacheHitRate, 0)

        task.handle([
            "type": "context_line",
            "text": "LangBridge context 10%",
            "cache_available": true,
            "cache_hit_rate": 0.42,
        ])

        XCTAssertEqual(task.cacheHitRate, 0.42)
    }

    @MainActor
    func testTaskSessionDecodesPermissionModeFromBridgeState() {
        let home = FileManager.default.temporaryDirectory
            .appendingPathComponent("langbridge-permission-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: home) }
        let task = TaskSession(
            workspace: Workspace(path: home.path),
            runtime: RuntimeLocator(environment: [:]),
            settings: SettingsStore(homeDirectory: home)
        )

        task.handle([
            "type": "state",
            "state": "ready",
            "permission_mode": "auto",
            "turn_active": false,
        ])

        XCTAssertEqual(task.permissionMode, .auto)
    }

    @MainActor
    func testTaskSessionCreatesDistinctScrollToBottomRequests() {
        let home = FileManager.default.temporaryDirectory
            .appendingPathComponent("langbridge-scroll-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: home) }
        let task = TaskSession(
            workspace: Workspace(path: home.path),
            runtime: RuntimeLocator(environment: [:]),
            settings: SettingsStore(homeDirectory: home)
        )
        let firstRequest = task.scrollToBottomRequestID

        task.requestScrollToBottom()

        XCTAssertNotEqual(task.scrollToBottomRequestID, firstRequest)
    }

    @MainActor
    func testTurnActiveStaysTrueAcrossMidTurnAssistantAndTraceEventsThatClearLiveText() {
        let home = FileManager.default.temporaryDirectory
            .appendingPathComponent("langbridge-turn-active-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: home) }
        let task = TaskSession(
            workspace: Workspace(path: home.path),
            runtime: RuntimeLocator(environment: [:]),
            settings: SettingsStore(homeDirectory: home)
        )

        task.handle(["type": "state", "state": "thinking", "turn_active": true])
        XCTAssertTrue(task.turnActive)

        task.handle(["type": "stream", "role": "LangBridge", "text": "partial"])
        XCTAssertEqual(task.liveText, "partial")

        // Trace and assistant events clear liveText mid-turn even though
        // the turn itself is still running; UI that suspends selection
        // must key off turnActive, not liveText emptiness, to avoid
        // repeatedly rebuilding selection overlays across these gaps.
        task.handle(["type": "trace", "text": "Inspecting files", "tool": "bash"])
        XCTAssertEqual(task.liveText, "")
        XCTAssertTrue(task.turnActive)

        task.handle(["type": "assistant", "text": "partial answer"])
        XCTAssertEqual(task.liveText, "")
        XCTAssertTrue(task.turnActive)

        task.handle(["type": "state", "state": "ready", "turn_active": false])
        XCTAssertFalse(task.turnActive)
    }

    func testTranscriptAutoFollowCoalescesADenseBurstIntoASinglePendingFollow() {
        var hasPendingFollow = false
        var scheduledCount = 0

        // A dense run of stream mutations, none of which should schedule a
        // second follow while the first is still pending.
        for _ in 0..<200 {
            if TranscriptAutoFollow.shouldScheduleFollow(isPinnedToBottom: true, hasPendingFollow: hasPendingFollow) {
                scheduledCount += 1
                hasPendingFollow = true
            }
        }
        XCTAssertEqual(scheduledCount, 1)

        // Once the pending follow finishes, the next mutation may schedule
        // a fresh one; this is the coalescing window repeating, not a
        // one-shot latch.
        hasPendingFollow = false
        XCTAssertTrue(TranscriptAutoFollow.shouldScheduleFollow(isPinnedToBottom: true, hasPendingFollow: hasPendingFollow))
    }

    func testTranscriptAutoFollowStopsSchedulingOnceUserScrollsAwayAndResumesAfterJumpToLatest() {
        // A real user drag moves the content container's top offset toward
        // the viewport's top; this must be flagged as a deliberate scroll
        // away from the bottom.
        XCTAssertTrue(TranscriptAutoFollow.didUserScrollAway(previousTopOffset: -640, newTopOffset: -580))

        var isPinnedToBottom = false // the view unpins on that signal
        XCTAssertFalse(TranscriptAutoFollow.shouldScheduleFollow(isPinnedToBottom: isPinnedToBottom, hasPendingFollow: false))

        // Only an explicit jump-to-latest (or navigation) action, not an
        // ordinary trace/message/live update, may force pinning back on.
        isPinnedToBottom = true
        XCTAssertTrue(TranscriptAutoFollow.shouldScheduleFollow(isPinnedToBottom: isPinnedToBottom, hasPendingFollow: false))
    }

    func testTranscriptAutoFollowIgnoresContentGrowthAndProgrammaticFollowScrolling() {
        // Streamed text appended below the fold never repositions the
        // content container's top offset, so repeated no-op deltas across
        // a dense burst must never read as the user scrolling away.
        var topOffset: CGFloat = -1_200
        var flaggedAsScrolledAway = false
        for _ in 0..<200 {
            if TranscriptAutoFollow.didUserScrollAway(previousTopOffset: topOffset, newTopOffset: topOffset) {
                flaggedAsScrolledAway = true
            }
        }
        XCTAssertFalse(flaggedAsScrolledAway)

        // The coalesced auto-follow scroll itself moves the container
        // further down (offset becomes more negative); that must not be
        // mistaken for the user scrolling away either.
        let afterProgrammaticFollow = topOffset - 220
        XCTAssertFalse(TranscriptAutoFollow.didUserScrollAway(previousTopOffset: topOffset, newTopOffset: afterProgrammaticFollow))
        topOffset = afterProgrammaticFollow

        XCTAssertTrue(TranscriptAutoFollow.shouldScheduleFollow(isPinnedToBottom: true, hasPendingFollow: false))
    }

    func testTranscriptAutoFollowStaleOrSupersededCompletionsNeverApplyTheirEffect() {
        let firstToken = UUID()
        let secondToken = UUID()

        // A follow that is still the current one when it wakes up applies.
        XCTAssertTrue(TranscriptAutoFollow.shouldApplyCompletedFollow(currentToken: firstToken, completedToken: firstToken))

        // Cancellation (e.g. onDisappear) clears the current token before
        // the sleeping task wakes up: it must not apply.
        XCTAssertFalse(TranscriptAutoFollow.shouldApplyCompletedFollow(currentToken: nil, completedToken: firstToken))

        // A newer follow superseded the stale one before it woke up: the
        // stale completion must not clear or replace the newer one.
        XCTAssertFalse(TranscriptAutoFollow.shouldApplyCompletedFollow(currentToken: secondToken, completedToken: firstToken))
    }

    func testTranscriptAutoFollowFinalizesOnlyWhenStillPinnedAtTurnEnd() {
        // liveText can flip empty and non-empty many times mid-turn as
        // trace/assistant events interleave with stream deltas; none of
        // those transitions alone should authorize a follow-scroll outside
        // the bounded/coalesced path, only the turn's true end does.
        var isPinnedToBottom = true

        // The turn settles while the user is still pinned: exactly one
        // final follow is warranted.
        XCTAssertTrue(TranscriptAutoFollow.shouldScheduleFollow(isPinnedToBottom: isPinnedToBottom, hasPendingFollow: false))

        // The user scrolled away before the turn settled: the final
        // settle must not force them back to the bottom.
        isPinnedToBottom = false
        XCTAssertFalse(TranscriptAutoFollow.shouldScheduleFollow(isPinnedToBottom: isPinnedToBottom, hasPendingFollow: false))
    }

    func testUTF8LineBufferPreservesLineSplitInsideChineseCharacter() throws {
        let line = #"{"type":"session_resumed","conversation":[{"text":"消息记录"}]}"#
        let payload = Data((line + "\n").utf8)
        let character = Data("消".utf8)
        let characterRange = try XCTUnwrap(payload.range(of: character))
        let split = characterRange.lowerBound + 1
        var buffer = UTF8LineBuffer()

        XCTAssertEqual(buffer.append(Data(payload[..<split])), [])
        let lines = buffer.append(Data(payload[split...]))

        XCTAssertEqual(lines, [line])
        XCTAssertEqual(BridgeEventDecoder.decode(line: lines[0])?["type"] as? String, "session_resumed")
    }

    func testKeychainRoundTripsCredentialLargerThanInteractivePasswordLimit() throws {
        let service = "com.langbridge.app.tests.\(UUID().uuidString)"
        let account = "long-oauth-credential"
        let payload = Data(repeating: 0x41, count: 2_048)
        defer { try? KeychainStore.delete(service: service, account: account) }

        try KeychainStore.set(payload, service: service, account: account)

        XCTAssertEqual(try KeychainStore.data(service: service, account: account), payload)
    }

    func testWindowFrameIsClampedToTheVisibleScreen() {
        let visible = NSRect(x: 0, y: 40, width: 1_312, height: 562)
        XCTAssertEqual(
            WindowFrameFitter.fit(
                NSRect(x: -20, y: -100, width: 1_500, height: 742),
                inside: visible
            ),
            visible
        )
        XCTAssertEqual(
            WindowFrameFitter.fit(
                NSRect(x: 300, y: 200, width: 900, height: 400),
                inside: visible
            ),
            NSRect(x: 300, y: 200, width: 900, height: 400)
        )
    }

    func testSessionColumnWidthsRespectMinimumAndFillAvailableSpace() {
        XCTAssertEqual(
            SessionColumnSizing.widths(total: 900, count: 3, dividers: [0.2, 0.8]),
            [255, 390, 255]
        )
        XCTAssertEqual(
            SessionColumnSizing.widths(total: 600, count: 3, dividers: [0.1, 0.9]),
            [200, 200, 200]
        )
        XCTAssertEqual(
            SessionColumnSizing.widths(total: 800, count: 2, dividers: [0.6]),
            [480, 320]
        )
    }

    func testWindowMinimumWidthIsOneSixthOfScreenWidth() {
        XCTAssertEqual(WindowSizing.minimumContentWidth(for: 1_512), 252)
        XCTAssertEqual(WindowSizing.minimumContentWidth(for: 3_456), 576)
    }

    func testParsesMarkdownTableAndPreservesSurroundingMarkdown() {
        let blocks = MarkdownBlockParser.parse("""
        Before **table**.

        | Name | Score | Note |
        | :--- | ---: | :---: |
        | Ada | 10 | A \\| B |
        | Lin | 9 |

        After table.
        """)

        XCTAssertEqual(blocks, [
            .markdown("Before **table**."),
            .table(.init(
                header: ["Name", "Score", "Note"],
                alignments: [.leading, .trailing, .center],
                rows: [
                    ["Ada", "10", "A | B"],
                    ["Lin", "9", ""],
                ]
            )),
            .markdown("After table."),
        ])
    }

    func testMarkdownBlankLineCreatesSeparateParagraphBlocks() {
        XCTAssertEqual(
            MarkdownBlockParser.parse("First **paragraph**.\n\nSecond paragraph."),
            [
                .markdown("First **paragraph**."),
                .markdown("Second paragraph."),
            ]
        )
    }

    func testDoesNotParseTableSyntaxInsideCodeFence() {
        let source = """
        ```markdown
        | A | B |
        | --- | --- |
        ```
        """
        XCTAssertEqual(MarkdownBlockParser.parse(source), [.markdown(source)])
    }

    func testPipeTextWithoutSeparatorStaysMarkdown() {
        let source = "alpha | beta\nnot a separator"
        XCTAssertEqual(MarkdownBlockParser.parse(source), [.markdown(source)])
    }

    func testTranscriptGroupsConsecutiveTraceEntriesByTurn() throws {
        let firstTurn = UUID()
        let secondTurn = UUID()
        let entries = [
            ChatEntry(kind: .user, text: "First", turnID: firstTurn),
            ChatEntry(kind: .trace, text: "Inspect", turnID: firstTurn),
            ChatEntry(kind: .trace, text: "Run tests", turnID: firstTurn),
            ChatEntry(kind: .assistant, text: "Done", turnID: firstTurn),
            ChatEntry(kind: .trace, text: "Next turn", turnID: secondTurn),
        ]

        let grouped = TranscriptGrouper.group(entries)

        XCTAssertEqual(grouped.count, 4)
        guard case let .activity(firstActivity) = grouped[1],
              case let .activity(secondActivity) = grouped[3]
        else {
            return XCTFail("Expected trace activity groups")
        }
        XCTAssertEqual(firstActivity.turnID, firstTurn)
        XCTAssertEqual(firstActivity.entries.map(\.text), ["Inspect", "Run tests"])
        XCTAssertEqual(secondActivity.turnID, secondTurn)
        XCTAssertEqual(secondActivity.entries.map(\.text), ["Next turn"])
    }

    func testSessionColumnsAreCappedAtThreeAndReplaceExplicitly() {
        let first = UUID()
        let second = UUID()
        let third = UUID()
        let fourth = UUID()
        var layout = SessionColumnLayout()

        layout.show(first)
        XCTAssertTrue(layout.openToRight(second))
        XCTAssertTrue(layout.openToRight(third))
        XCTAssertEqual(layout.ids, [first, second, third])
        XCTAssertFalse(layout.openToRight(fourth))
        XCTAssertEqual(layout.ids, [first, second, third])

        layout.replaceColumn(at: 1, with: fourth)
        XCTAssertEqual(layout.ids, [first, fourth, third])
        XCTAssertEqual(layout.activeID, fourth)
    }

    func testForkedSessionOpensImmediatelyRightOfSourceEvenWhenAnotherColumnIsActive() {
        let source = UUID()
        let otherActive = UUID()
        let fork = UUID()
        var layout = SessionColumnLayout()

        // Two columns are visible; the user forks from "source" while a
        // different column ("otherActive") is the one currently active.
        layout.show(source)
        XCTAssertTrue(layout.openToRight(otherActive))
        XCTAssertEqual(layout.activeID, otherActive)

        // Regression guard: opening to the right without first re-anchoring
        // on the source column places the fork beside whichever column
        // happens to be active, not beside its actual source.
        var unanchored = layout
        XCTAssertTrue(unanchored.openToRight(fork))
        XCTAssertEqual(unanchored.ids, [source, otherActive, fork])

        // The fix re-activates the task that emitted the fork immediately
        // before opening to the right, so the fork lands beside its source
        // regardless of which column was active.
        layout.show(source)
        XCTAssertTrue(layout.openToRight(fork))
        XCTAssertEqual(layout.ids, [source, fork, otherActive])
        XCTAssertEqual(layout.activeID, fork)
    }

    func testShowingHiddenTaskReplacesOnlyTheActiveColumn() {
        let first = UUID()
        let second = UUID()
        let hidden = UUID()
        var layout = SessionColumnLayout()

        layout.show(first)
        XCTAssertTrue(layout.openToRight(second))
        layout.show(hidden)

        XCTAssertEqual(layout.ids, [first, hidden])
        XCTAssertEqual(layout.activeID, hidden)
        layout.hide(hidden)
        XCTAssertEqual(layout.ids, [first])
        XCTAssertEqual(layout.activeID, first)
    }

    func testOpeningSessionDoesNotChangeSidebarEntryOrder() {
        let first = SessionItem(path: "/sessions/first", label: "First")
        let second = SessionItem(path: "/sessions/second", label: "Second")
        let taskID = UUID()

        let before = SidebarSessionLayout.entryIDs(tasks: [], sessions: [first, second])
        let after = SidebarSessionLayout.entryIDs(
            tasks: [.init(id: taskID, sessionPath: first.path)],
            sessions: [first, second]
        )

        XCTAssertEqual(before, [.session(first.path), .session(second.path)])
        XCTAssertEqual(after, before)
    }

    func testBlankDraftIsHiddenUntilItsSessionPathExists() {
        let taskID = UUID()
        let session = SessionItem(path: "/sessions/new", label: "New")

        XCTAssertEqual(
            SidebarSessionLayout.entryIDs(
                tasks: [.init(id: taskID, sessionPath: nil)],
                sessions: []
            ),
            []
        )
        XCTAssertEqual(
            SidebarSessionLayout.entryIDs(
                tasks: [.init(id: taskID, sessionPath: session.path)],
                sessions: []
            ),
            [.draft(taskID)]
        )
        XCTAssertEqual(
            SidebarSessionLayout.entryIDs(
                tasks: [.init(id: taskID, sessionPath: session.path)],
                sessions: [session]
            ),
            [.session(session.path)]
        )
    }

    @MainActor
    func testOnlyIdleTaskCanBeReleased() {
        let home = FileManager.default.temporaryDirectory
            .appendingPathComponent("langbridge-idle-task-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: home) }
        let task = TaskSession(
            workspace: Workspace(path: home.path),
            runtime: RuntimeLocator(environment: [:]),
            settings: SettingsStore(homeDirectory: home)
        )

        task.handle(["type": "state", "state": "thinking", "turn_active": true])
        XCTAssertFalse(task.deactivateIfIdle())

        task.handle([
            "type": "state",
            "state": "ready",
            "turn_active": false,
            "session_path": home.appendingPathComponent("session-test").path,
        ])
        XCTAssertTrue(task.deactivateIfIdle())
    }

    @MainActor
    func testForkIsOnlyAvailableForPersistedFullyIdleSession() {
        let home = FileManager.default.temporaryDirectory
            .appendingPathComponent("langbridge-fork-eligibility-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: home) }
        let task = TaskSession(
            workspace: Workspace(path: home.path),
            runtime: RuntimeLocator(environment: [:]),
            settings: SettingsStore(homeDirectory: home)
        )

        // Blank: no persisted session path yet.
        task.handle(["type": "state", "state": "ready", "turn_active": false])
        XCTAssertFalse(task.canFork)

        // Busy: turn active on a persisted session.
        task.handle([
            "type": "state",
            "state": "thinking",
            "turn_active": true,
            "session_path": home.appendingPathComponent("session-fork-test").path,
        ])
        XCTAssertFalse(task.canFork)

        // Persisted and fully idle: eligible.
        task.handle(["type": "state", "state": "ready", "turn_active": false])
        XCTAssertTrue(task.canFork)
    }

    @MainActor
    func testSessionForkedEventInvokesCallbackWithNewSessionItem() {
        let home = FileManager.default.temporaryDirectory
            .appendingPathComponent("langbridge-fork-event-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: home) }
        let task = TaskSession(
            workspace: Workspace(path: home.path),
            runtime: RuntimeLocator(environment: [:]),
            settings: SettingsStore(homeDirectory: home)
        )
        var forked: SessionItem?
        task.onSessionForked = { forked = $0 }

        let forkPath = home.appendingPathComponent("session-fork-test-fork-2026-08-30T120000").path
        task.handle(["type": "session_forked", "path": forkPath, "label": "fork-test (fork)"])

        XCTAssertEqual(forked?.path, forkPath)
        XCTAssertEqual(forked?.label, "fork-test (fork)")
    }

    @MainActor
    func testTaskCompletionReachesInboxOnlyAfterQueuedWorkBecomesIdle() {
        let home = FileManager.default.temporaryDirectory
            .appendingPathComponent("langbridge-inbox-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: home) }
        let task = TaskSession(
            workspace: Workspace(path: home.path),
            runtime: RuntimeLocator(environment: [:]),
            settings: SettingsStore(homeDirectory: home)
        )
        var statuses: [String] = []
        task.onTurnFinished = { statuses.append($0) }

        task.handle([
            "type": "state",
            "state": "thinking",
            "turn_active": true,
            "session_path": home.appendingPathComponent("session-inbox").path,
        ])
        task.handle(["type": "turn_end", "status": "ok"])
        XCTAssertTrue(statuses.isEmpty)

        // A queued turn starts before the bridge becomes idle. Only its final
        // outcome should be delivered when the whole task settles.
        task.handle(["type": "state", "state": "thinking", "turn_active": true])
        task.handle(["type": "turn_end", "status": "error"])
        XCTAssertTrue(statuses.isEmpty)

        task.handle(["type": "state", "state": "ready", "turn_active": false])
        XCTAssertEqual(statuses, ["error"])

        task.handle(["type": "state", "state": "ready", "turn_active": false])
        XCTAssertEqual(statuses, ["error"])
    }

    func testInboxItemKeepsOriginalTaskAndSessionNavigation() {
        let taskID = UUID()
        let workspaceID = UUID()
        let item = TaskInboxItem(
            taskID: taskID,
            workspaceID: workspaceID,
            sessionPath: "/sessions/original",
            title: "Original task",
            status: "ok"
        )

        XCTAssertEqual(item.taskID, taskID)
        XCTAssertEqual(item.workspaceID, workspaceID)
        XCTAssertEqual(item.sessionPath, "/sessions/original")
        XCTAssertEqual(item.title, "Original task")
        XCTAssertTrue(item.isUnread)

        let readItem = TaskInboxItem(
            taskID: taskID,
            workspaceID: workspaceID,
            sessionPath: "/sessions/original",
            title: "Original task",
            status: "ok",
            isUnread: false
        )
        XCTAssertFalse(readItem.isUnread)
    }

    func testInboxOnlyShowsUnreadItemsAndMarkAsUnreadRestoresThem() {
        let unread = TaskInboxItem(
            taskID: UUID(),
            workspaceID: UUID(),
            sessionPath: "/sessions/unread",
            title: "Unread task",
            status: "ok"
        )
        var read = TaskInboxItem(
            taskID: UUID(),
            workspaceID: UUID(),
            sessionPath: "/sessions/read",
            title: "Read task",
            status: "ok",
            isUnread: false
        )
        let items = [unread, read]

        // The Inbox renders only unread items; the matching session row
        // exposes "Mark as Unread" for the rest.
        XCTAssertEqual(items.filter(\.isUnread).map(\.id), [unread.id])
        XCTAssertEqual(
            items.first { $0.sessionPath == "/sessions/read" && !$0.isUnread }?.id,
            read.id
        )

        read.isUnread = true
        let restored = [unread, read]
        XCTAssertEqual(Set(restored.filter(\.isUnread).map(\.id)), Set([unread.id, read.id]))
    }

    @MainActor
    func testExistingSessionCanBeMarkedUnreadWithoutAnInboxRecord() {
        let defaults = UserDefaults.standard
        let key = "langbridge.desktop.workspaces"
        let previousWorkspaces = defaults.data(forKey: key)
        defaults.removeObject(forKey: key)
        defer {
            if let previousWorkspaces {
                defaults.set(previousWorkspaces, forKey: key)
            } else {
                defaults.removeObject(forKey: key)
            }
        }

        let home = FileManager.default.temporaryDirectory
            .appendingPathComponent("langbridge-inbox-existing-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: home) }
        let settings = SettingsStore(homeDirectory: home)
        let model = AppModel(
            settings: settings,
            runtime: RuntimeLocator(environment: ["LANGBRIDGE_REPO_ROOT": home.path])
        )
        let workspace = try! XCTUnwrap(model.workspaces.first)

        XCTAssertTrue(model.canMarkSessionUnread("/sessions/existing"))
        model.markSessionUnread(
            sessionPath: "/sessions/existing",
            title: "Existing task",
            workspaceID: workspace.id
        )

        XCTAssertEqual(model.inboxItems.count, 1)
        XCTAssertTrue(model.inboxItems[0].isUnread)
        XCTAssertFalse(model.canMarkSessionUnread("/sessions/existing"))

        model.openInboxItem(model.inboxItems[0])
        XCTAssertFalse(model.inboxItems[0].isUnread)
        XCTAssertTrue(model.canMarkSessionUnread("/sessions/existing"))

        model.markSessionUnread(
            sessionPath: "/sessions/existing",
            title: "Existing task",
            workspaceID: workspace.id
        )
        XCTAssertEqual(model.inboxItems.count, 1)
        XCTAssertTrue(model.inboxItems[0].isUnread)
    }

    @MainActor
    func testBlankDraftIsKeptWhenItLeavesTheVisibleColumn() {
        let home = FileManager.default.temporaryDirectory
            .appendingPathComponent("langbridge-draft-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: home) }
        let task = TaskSession(
            workspace: Workspace(path: home.path),
            runtime: RuntimeLocator(environment: [:]),
            settings: SettingsStore(homeDirectory: home)
        )

        task.handle(["type": "state", "state": "ready", "turn_active": false])

        XCTAssertTrue(task.isBlankDraft)
        XCTAssertFalse(task.deactivateIfIdle())
    }

    func testSessionArchiveMarkerRoundTripsWithoutDeletingSession() throws {
        let session = FileManager.default.temporaryDirectory
            .appendingPathComponent("langbridge-archive-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: session, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: session) }

        try SessionArchive.setArchived(true, sessionPath: session.path)
        XCTAssertTrue(SessionItem(path: session.path, label: "Test").archived)
        XCTAssertTrue(FileManager.default.fileExists(atPath: session.path))

        try SessionArchive.setArchived(false, sessionPath: session.path)
        XCTAssertFalse(SessionItem(path: session.path, label: "Test").archived)
        XCTAssertTrue(FileManager.default.fileExists(atPath: session.path))
    }

    @MainActor
    func testDiscoversOnlyDownloadedSystemAerials() throws {
        let home = FileManager.default.temporaryDirectory
            .appendingPathComponent("langbridge-wallpapers-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: home) }
        let aerials = home.appendingPathComponent(
            "Library/Application Support/com.apple.wallpaper/aerials", isDirectory: true
        )
        try FileManager.default.createDirectory(
            at: aerials.appendingPathComponent("manifest", isDirectory: true),
            withIntermediateDirectories: true
        )
        try FileManager.default.createDirectory(
            at: aerials.appendingPathComponent("videos", isDirectory: true),
            withIntermediateDirectories: true
        )
        let manifest: [String: Any] = ["assets": [
            ["id": "new-york", "accessibilityLabel": "New York"],
            ["id": "missing", "accessibilityLabel": "Tahoe"],
        ]]
        try JSONSerialization.data(withJSONObject: manifest).write(
            to: aerials.appendingPathComponent("manifest/entries.json")
        )
        FileManager.default.createFile(
            atPath: aerials.appendingPathComponent("videos/new-york.mov").path,
            contents: Data("movie".utf8)
        )

        let options = SettingsStore.discoverWallpaperOptions(homeDirectory: home)

        XCTAssertEqual(options.map(\.name), ["Default", "New York"])
    }

    @MainActor
    func testConnectGmailWithoutOAuthConfigShowsSetupMessageInsteadOfOpeningPicker() {
        let home = FileManager.default.temporaryDirectory
            .appendingPathComponent("langbridge-gmail-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: home) }

        let store = SettingsStore(homeDirectory: home)
        XCTAssertFalse(store.gmailOAuthConfigured)

        store.connectGmail()

        XCTAssertEqual(
            store.gmailStatus,
            "Select the Google Desktop OAuth JSON in Settings before signing in."
        )
    }

    func testDecodesHelloEvent() throws {
        let line = #"{"type":"hello","model":"gpt-5.6","cwd":"~/repo","git_branch":"main","sessions":[]}"#
        let event = try XCTUnwrap(BridgeEventDecoder.decode(line: line))
        XCTAssertEqual(event["type"] as? String, "hello")
        XCTAssertEqual(event["model"] as? String, "gpt-5.6")
    }

    @MainActor
    func testTaskSessionTracksBackendCreatedSessionPath() {
        let home = FileManager.default.temporaryDirectory
            .appendingPathComponent("langbridge-session-path-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: home) }
        let task = TaskSession(
            workspace: Workspace(path: home.path),
            runtime: RuntimeLocator(environment: [:]),
            settings: SettingsStore(homeDirectory: home)
        )

        task.handle([
            "type": "state",
            "state": "thinking",
            "turn_active": true,
            "session_path": "/tmp/session-current",
        ])

        XCTAssertEqual(task.sessionPath, "/tmp/session-current")
    }

    func testSlashSkillSuggestionsOpenOnSlashAndFilterNames() {
        let skills = [
            SkillItem(name: "creating-skills", description: "Guided discovery"),
            SkillItem(name: "grilling", description: "Stress-test a plan"),
        ]

        XCTAssertEqual(
            SlashSkillSuggestions.matches(for: "/", in: skills).map(\.name),
            ["creating-skills", "grilling"]
        )
        XCTAssertEqual(
            SlashSkillSuggestions.matches(for: "/creat", in: skills).map(\.name),
            ["creating-skills"]
        )
        XCTAssertEqual(
            SlashSkillSuggestions.matches(for: "/grill", in: skills).map(\.name),
            ["grilling"]
        )
        XCTAssertTrue(SlashSkillSuggestions.matches(for: "/stress", in: skills).isEmpty)
    }

    func testSlashSkillSuggestionsCloseAfterCommandArgumentsBegin() {
        let skills = [SkillItem(name: "grilling", description: "Stress-test a plan")]

        XCTAssertNil(SlashSkillSuggestions.query(in: "plain text"))
        XCTAssertNil(SlashSkillSuggestions.query(in: "/grilling auth"))
        XCTAssertTrue(SlashSkillSuggestions.matches(for: "/grilling auth", in: skills).isEmpty)
    }

    func testDecodesSkillCatalogEvent() {
        let skills = BridgeEventDecoder.skills(from: [
            "items": [["name": "creating-skills", "description": "Guided discovery"]],
        ])

        XCTAssertEqual(skills, [
            SkillItem(name: "creating-skills", description: "Guided discovery"),
        ])
    }

    func testExtractsSessionItems() throws {
        let event: [String: Any] = [
            "type": "sessions",
            "items": [["path": "/tmp/session-a", "label": "Fix tests"]],
        ]
        XCTAssertEqual(BridgeEventDecoder.sessions(from: event), [
            SessionItem(path: "/tmp/session-a", label: "Fix tests"),
        ])
    }

    func testSessionItemHidesGeneratedTimestampButPreservesCustomDateTitle() {
        let path = "/tmp/session-Review-email-2026-08-28T191645"

        XCTAssertEqual(
            SessionItem(path: path, label: "Review-email-2026-08-28T191645").label,
            "Review-email"
        )
        XCTAssertEqual(
            SessionItem(path: path, label: "Review email from 2026-08-28").label,
            "Review email from 2026-08-28"
        )
    }

    func testRejectsNonProtocolJSON() {
        XCTAssertNil(BridgeEventDecoder.decode(line: #"{"message":"missing type"}"#))
        XCTAssertNil(BridgeEventDecoder.decode(line: "not json"))
    }

    func testExtractsImagePathsFromStringsAndObjects() {
        let event: [String: Any] = [
            "type": "turn_started",
            "images": ["/tmp/one.png", ["path": "/tmp/two.jpg"]],
        ]
        XCTAssertEqual(
            BridgeEventDecoder.imagePaths(from: event),
            ["/tmp/one.png", "/tmp/two.jpg"]
        )
    }

    func testCopiedImageFileCanBeImportedFromPasteboard() throws {
        let source = FileManager.default.temporaryDirectory
            .appendingPathComponent("langbridge-paste-\(UUID().uuidString).png")
        let bitmap = try XCTUnwrap(NSBitmapImageRep(
            bitmapDataPlanes: nil,
            pixelsWide: 8,
            pixelsHigh: 8,
            bitsPerSample: 8,
            samplesPerPixel: 4,
            hasAlpha: true,
            isPlanar: false,
            colorSpaceName: .deviceRGB,
            bytesPerRow: 0,
            bitsPerPixel: 0
        ))
        try XCTUnwrap(bitmap.representation(using: .png, properties: [:])).write(to: source)
        defer { try? FileManager.default.removeItem(at: source) }

        let pasteboard = NSPasteboard(name: .init("langbridge-test-\(UUID().uuidString)"))
        pasteboard.clearContents()
        XCTAssertTrue(pasteboard.writeObjects([source as NSURL]))
        XCTAssertTrue(ImageAttachmentStore.pasteboardContainsImage(pasteboard))

        let attachments = try ImageAttachmentStore.importPasteboard(pasteboard, limit: 1)
        XCTAssertEqual(attachments.count, 1)
        XCTAssertTrue(FileManager.default.fileExists(atPath: attachments[0].path))
        attachments.forEach(ImageAttachmentStore.remove)
    }

    func testPlainTextPasteboardIsNotTreatedAsImage() {
        let pasteboard = NSPasteboard(name: .init("langbridge-test-\(UUID().uuidString)"))
        pasteboard.clearContents()
        pasteboard.setString("ordinary text", forType: .string)
        XCTAssertFalse(ImageAttachmentStore.pasteboardContainsImage(pasteboard))
    }

    func testDecodesScheduleItem() throws {
        let item = try XCTUnwrap(ScheduleItem(dictionary: [
            "id": "f482945c-1234-4321-9999-f482945c1234",
            "name": "X Daily",
            "prompt": "Summarize X",
            "workspace": "/tmp/project",
            "recurrence": "weekly",
            "schedule_spec": ["time": "21:00", "weekday": 2],
            "tools": ["browser"],
            "output_subdirectory": "LangBridge/X Daily",
            "enabled": true,
            "last_status": "never",
        ]))
        XCTAssertEqual(item.name, "X Daily")
        XCTAssertEqual(item.weekday, 2)
        XCTAssertEqual(item.recurrenceLabel, "Every Wednesday at 21:00")
        XCTAssertEqual(item.tools, ["browser"])
    }

    func testParsesGoogleDesktopOAuthClientAndRejectsWebClient() throws {
        let installed = Data(#"{"installed":{"client_id":"client.apps.googleusercontent.com","client_secret":"secret","auth_uri":"https://accounts.google.com/o/oauth2/v2/auth","token_uri":"https://oauth2.googleapis.com/token"}}"#.utf8)
        let client = try GoogleDesktopOAuthClient(data: installed)
        XCTAssertEqual(client.clientID, "client.apps.googleusercontent.com")
        XCTAssertEqual(client.clientSecret, "secret")

        let web = Data(#"{"web":{"client_id":"wrong-kind"}}"#.utf8)
        XCTAssertThrowsError(try GoogleDesktopOAuthClient(data: web))

        let attacker = Data(#"{"installed":{"client_id":"client.apps.googleusercontent.com","auth_uri":"https://accounts.google.com.evil.example/auth","token_uri":"https://evil.example/token"}}"#.utf8)
        XCTAssertThrowsError(try GoogleDesktopOAuthClient(data: attacker))
    }

    @MainActor
    func testSettingsMigratesLegacyPreviewGmailConfigWithoutDroppingOAuth() throws {
        let home = FileManager.default.temporaryDirectory
            .appendingPathComponent("langbridge-gmail-migration-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: home) }
        let support = home.appendingPathComponent(
            "Library/Application Support/LangBridge", isDirectory: true
        )
        try FileManager.default.createDirectory(at: support, withIntermediateDirectories: true)
        let configURL = support.appendingPathComponent("mcp.json")
        let config: [String: Any] = [
            "version": 1,
            "servers": [
                "gmail": [
                    "enabled": true,
                    "transport": "streamable-http",
                    "url": "https://gmailmcp.googleapis.com/mcp/v1",
                    "oauth": ["client_id": "kept.apps.googleusercontent.com"],
                ],
            ],
        ]
        try JSONSerialization.data(withJSONObject: config).write(to: configURL)

        _ = SettingsStore(homeDirectory: home)

        let migrated = try XCTUnwrap(
            try JSONSerialization.jsonObject(with: Data(contentsOf: configURL)) as? [String: Any]
        )
        let servers = try XCTUnwrap(migrated["servers"] as? [String: Any])
        let gmail = try XCTUnwrap(servers["gmail"] as? [String: Any])
        XCTAssertEqual(gmail["transport"] as? String, "gmail-api")
        XCTAssertNil(gmail["url"])
        XCTAssertEqual(
            (gmail["oauth"] as? [String: Any])?["client_id"] as? String,
            "kept.apps.googleusercontent.com"
        )
    }

    func testOAuthFormEncodingIsStableAndEscapesRedirectURI() throws {
        let body = try XCTUnwrap(String(data: GmailOAuthCoordinator.formBody([
            "redirect_uri": "http://127.0.0.1:4321",
            "grant_type": "authorization_code",
        ]), encoding: .utf8))
        XCTAssertEqual(
            body,
            "grant_type=authorization_code&redirect_uri=http%3A%2F%2F127.0.0.1%3A4321"
        )
    }

    @MainActor
    func testResumedConversationItemsExposeStableBackendTurnIDAndCheckpointAvailability() {
        let home = FileManager.default.temporaryDirectory
            .appendingPathComponent("langbridge-resume-turnid-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: home) }
        let task = TaskSession(
            workspace: Workspace(path: home.path),
            runtime: RuntimeLocator(environment: [:]),
            settings: SettingsStore(homeDirectory: home)
        )

        task.handle([
            "type": "session_resumed",
            "conversation": [
                ["role": "user", "text": "fix the bug", "turn_id": 3, "checkpoint_available": true],
                ["role": "assistant", "text": "done"],
            ],
        ])

        XCTAssertEqual(task.messages.count, 2)
        XCTAssertEqual(task.messages[0].backendTurnID, 3)
        XCTAssertTrue(task.messages[0].checkpointAvailable)
        XCTAssertNil(task.messages[1].backendTurnID)
    }

    @MainActor
    func testSessionResumedEventDoesNotReintroduceGeneratedTimestampIntoCleanedTitle() {
        let home = FileManager.default.temporaryDirectory
            .appendingPathComponent("langbridge-resume-title-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: home) }
        let sessionPath = "/tmp/session-你现在在哪个目录下-2026-08-28T172249"
        let rawLabel = "你现在在哪个目录下-2026-08-28T172249"
        let resume = SessionItem(path: sessionPath, label: rawLabel)
        XCTAssertEqual(resume.label, "你现在在哪个目录下")

        let task = TaskSession(
            workspace: Workspace(path: home.path),
            runtime: RuntimeLocator(environment: [:]),
            settings: SettingsStore(homeDirectory: home),
            resume: resume
        )
        XCTAssertEqual(task.title, "你现在在哪个目录下")

        task.handle([
            "type": "session_resumed",
            "path": sessionPath,
            "label": rawLabel,
        ])

        XCTAssertEqual(task.title, "你现在在哪个目录下")
    }

    @MainActor
    func testTurnIDAssignedBackfillsExistingBubbleWithoutAddingANewOne() {
        let home = FileManager.default.temporaryDirectory
            .appendingPathComponent("langbridge-turnid-assigned-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: home) }
        let task = TaskSession(
            workspace: Workspace(path: home.path),
            runtime: RuntimeLocator(environment: [:]),
            settings: SettingsStore(homeDirectory: home)
        )

        task.sendPrompt("Investigate the crash")
        XCTAssertEqual(task.messages.count, 1)

        task.handle(["type": "turn_id_assigned", "turn_id": 7, "checkpoint_available": true])

        XCTAssertEqual(task.messages.count, 1)
        XCTAssertEqual(task.messages[0].backendTurnID, 7)
        XCTAssertTrue(task.messages[0].checkpointAvailable)
    }

    @MainActor
    func testImageOnlyPromptHasNoSyntheticText() {
        let home = FileManager.default.temporaryDirectory
            .appendingPathComponent("langbridge-image-only-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: home) }
        let task = TaskSession(
            workspace: Workspace(path: home.path),
            runtime: RuntimeLocator(environment: [:]),
            settings: SettingsStore(homeDirectory: home)
        )

        task.sendPrompt("", images: [ImageAttachment(path: "/tmp/image.png")])

        XCTAssertEqual(task.messages.count, 1)
        XCTAssertEqual(task.messages[0].text, "")
        XCTAssertEqual(task.messages[0].imagePaths, ["/tmp/image.png"])
        XCTAssertEqual(task.title, "New task")
    }

    @MainActor
    func testRewoundEventReplacesTranscriptWithEarlierConversationOnly() {
        let home = FileManager.default.temporaryDirectory
            .appendingPathComponent("langbridge-rewound-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: home) }
        let task = TaskSession(
            workspace: Workspace(path: home.path),
            runtime: RuntimeLocator(environment: [:]),
            settings: SettingsStore(homeDirectory: home)
        )

        task.handle([
            "type": "session_resumed",
            "conversation": [
                ["role": "user", "text": "first", "turn_id": 1, "checkpoint_available": true],
                ["role": "assistant", "text": "ok"],
                ["role": "user", "text": "second", "turn_id": 2, "checkpoint_available": true],
            ],
        ])
        XCTAssertEqual(task.messages.count, 3)

        task.handle([
            "type": "rewound",
            "turn_id": 2,
            "conversation": [
                ["role": "user", "text": "first", "turn_id": 1, "checkpoint_available": true],
            ],
        ])

        XCTAssertEqual(task.messages.count, 1)
        XCTAssertEqual(task.messages[0].text, "first")
        XCTAssertEqual(task.messages[0].backendTurnID, 1)
    }

    func testRewindAvailabilityRequiresEligibleEntryAndFullyIdleTask() {
        let eligible = ChatEntry(kind: .user, text: "hi", backendTurnID: 1, checkpointAvailable: true)
        XCTAssertTrue(RewindAvailability.isAvailable(entry: eligible, taskIsIdle: true))
        XCTAssertFalse(RewindAvailability.isAvailable(entry: eligible, taskIsIdle: false))

        let noCheckpoint = ChatEntry(kind: .user, text: "hi", backendTurnID: 1, checkpointAvailable: false)
        XCTAssertFalse(RewindAvailability.isAvailable(entry: noCheckpoint, taskIsIdle: true))

        let noTurnID = ChatEntry(kind: .user, text: "hi", checkpointAvailable: true)
        XCTAssertFalse(RewindAvailability.isAvailable(entry: noTurnID, taskIsIdle: true))

        let assistant = ChatEntry(kind: .assistant, text: "hi", backendTurnID: 1, checkpointAvailable: true)
        XCTAssertFalse(RewindAvailability.isAvailable(entry: assistant, taskIsIdle: true))
    }

    @MainActor
    func testTaskIsFullyIdleRequiresReadyStateWithNoActiveTurnApprovalOrQuestion() {
        let home = FileManager.default.temporaryDirectory
            .appendingPathComponent("langbridge-fully-idle-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: home) }
        let task = TaskSession(
            workspace: Workspace(path: home.path),
            runtime: RuntimeLocator(environment: [:]),
            settings: SettingsStore(homeDirectory: home)
        )

        task.handle(["type": "state", "state": "ready", "turn_active": false])
        XCTAssertTrue(task.isFullyIdle)

        task.handle(["type": "approval_request", "summary": "approve?", "details": ""])
        XCTAssertFalse(task.isFullyIdle)

        task.handle(["type": "approval_resolved", "approved": true])
        task.handle(["type": "state", "state": "thinking", "turn_active": true])
        XCTAssertFalse(task.isFullyIdle)
    }
}
