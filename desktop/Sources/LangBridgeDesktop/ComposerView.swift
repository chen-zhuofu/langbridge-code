import AppKit
import SwiftUI

struct ComposerView: View {
    @Binding var text: String
    @Binding var attachments: [ImageAttachment]
    let isRunning: Bool
    let isAnsweringQuestion: Bool
    let statusText: String
    let statusColor: Color
    let cacheText: String
    let contextText: String
    let contextDetails: String
    let skills: [SkillItem]
    let onSend: () -> Void
    let onStop: () -> Void
    let onSkillsRequested: () -> Void
    @FocusState private var focused: Bool
    @Environment(\.wallpaperActive) private var wallpaperActive
    @State private var attachmentError = ""
    @State private var editorWidth: CGFloat = 600
    @State private var pasteMonitor: Any?
    @State private var selectedSkillIndex = 0

    private let minimumEditorHeight: CGFloat = 44
    private let maximumEditorHeight: CGFloat = 286

    var body: some View {
        VStack(spacing: 8) {
            if !attachments.isEmpty {
                attachmentStrip
            }

            if skillMenuVisible {
                skillSuggestionMenu
            }

            editor

            toolbar
        }
        .padding(11)
        .background {
            if wallpaperActive {
                RoundedRectangle(cornerRadius: 16).fill(.ultraThinMaterial)
            } else {
                RoundedRectangle(cornerRadius: 16).fill(Color(nsColor: .controlBackgroundColor))
            }
        }
        .overlay(RoundedRectangle(cornerRadius: 16).stroke(Color.primary.opacity(0.10)))
        .shadow(color: .black.opacity(0.06), radius: 12, y: 4)
        .onAppear {
            focused = true
            installPasteMonitor()
        }
        .onChange(of: text) {
            selectedSkillIndex = 0
        }
        .onChange(of: skillMenuVisible) { wasVisible, isVisible in
            if isVisible && !wasVisible {
                onSkillsRequested()
            }
        }
        .onChange(of: skills) {
            selectedSkillIndex = min(selectedSkillIndex, max(0, suggestedSkills.count - 1))
        }
        .onDisappear {
            if let pasteMonitor {
                NSEvent.removeMonitor(pasteMonitor)
                self.pasteMonitor = nil
            }
        }
    }

    private var editor: some View {
        TextEditor(text: $text)
            .font(.body)
            .scrollContentBackground(.hidden)
            .frame(height: measuredTextHeight)
            .focused($focused)
            .background {
                GeometryReader { geometry in
                    Color.clear
                        .onAppear { editorWidth = geometry.size.width }
                        .onChange(of: geometry.size.width) {
                            editorWidth = geometry.size.width
                        }
                }
            }
            .overlay(alignment: .topLeading) {
                if text.isEmpty {
                    Text(isAnsweringQuestion ? "Type your answer…" : "Ask LangBridge to work on this project…")
                        .foregroundStyle(.tertiary)
                        .padding(.top, 1)
                        .padding(.leading, 5)
                        .allowsHitTesting(false)
                }
            }
            .onKeyPress(.downArrow) {
                moveSkillSelectionDown()
            }
            .onKeyPress(.upArrow) {
                moveSkillSelectionUp()
            }
            .onKeyPress(.tab) {
                chooseSelectedSkill()
            }
            .onKeyPress(.return) {
                chooseSelectedSkill()
            }
    }

    private var skillMenuVisible: Bool {
        !isAnsweringQuestion && SlashSkillSuggestions.query(in: text) != nil
    }

    private var suggestedSkills: [SkillItem] {
        SlashSkillSuggestions.matches(for: text, in: skills)
    }

    private var skillSuggestionMenu: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Label("Skills", systemImage: "sparkles")
                    .font(.caption.weight(.semibold))
                Spacer()
                Text("↑↓ choose · ↩ insert")
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 7)

            Divider()

            if suggestedSkills.isEmpty {
                Text(skills.isEmpty ? "Loading skills…" : "No matching skills")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(10)
            } else {
                ScrollView {
                    LazyVStack(spacing: 2) {
                        ForEach(Array(suggestedSkills.enumerated()), id: \.element.id) { index, skill in
                            Button {
                                chooseSkill(skill)
                            } label: {
                                VStack(alignment: .leading, spacing: 3) {
                                    Text("/\(skill.name)")
                                        .font(.body.monospaced().weight(.medium))
                                        .foregroundStyle(.primary)
                                    if !skill.description.isEmpty {
                                        Text(skill.description)
                                            .font(.caption)
                                            .foregroundStyle(.secondary)
                                            .lineLimit(1)
                                    }
                                }
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .padding(.horizontal, 9)
                                .padding(.vertical, 7)
                                .background {
                                    RoundedRectangle(cornerRadius: 7)
                                        .fill(index == selectedSkillIndex ? Brand.accentSoft : .clear)
                                }
                            }
                            .buttonStyle(.plain)
                        }
                    }
                    .padding(4)
                }
                .frame(height: min(CGFloat(suggestedSkills.count) * 56, 224))
            }
        }
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 10))
        .overlay(RoundedRectangle(cornerRadius: 10).stroke(Color.primary.opacity(0.12)))
    }

    private func chooseSkill(_ skill: SkillItem) {
        text = "/\(skill.name) "
        focused = true
    }

    private func chooseSelectedSkill() -> KeyPress.Result {
        guard skillMenuVisible, suggestedSkills.indices.contains(selectedSkillIndex) else {
            return .ignored
        }
        chooseSkill(suggestedSkills[selectedSkillIndex])
        return .handled
    }

    private func moveSkillSelectionDown() -> KeyPress.Result {
        let count = suggestedSkills.count
        guard skillMenuVisible, count > 0 else { return .ignored }
        selectedSkillIndex = selectedSkillIndex + 1 == count ? 0 : selectedSkillIndex + 1
        return .handled
    }

    private func moveSkillSelectionUp() -> KeyPress.Result {
        let count = suggestedSkills.count
        guard skillMenuVisible, count > 0 else { return .ignored }
        if selectedSkillIndex == 0 {
            selectedSkillIndex = count - 1
        } else {
            selectedSkillIndex -= 1
        }
        return .handled
    }

    @ViewBuilder
    private var toolbar: some View {
        if veryCompactToolbar {
            VStack(spacing: 6) {
                HStack(spacing: 7) {
                    metrics
                    Spacer(minLength: 0)
                }
                HStack(spacing: 7) {
                    attachmentErrorIndicator
                    Spacer(minLength: 3)
                    stopButton
                    sendButton
                }
            }
        } else {
            HStack(spacing: 7) {
                attachmentErrorIndicator
                Spacer(minLength: 3)
                metrics
                Spacer(minLength: 3)
                stopButton
                sendButton
            }
        }
    }

    @ViewBuilder
    private var attachmentErrorIndicator: some View {
        if !attachmentError.isEmpty {
            if compactToolbar {
                Image(systemName: "exclamationmark.triangle.fill")
                    .font(.caption2)
                    .foregroundStyle(.red)
                    .help(attachmentError)
            } else {
                Text(attachmentError)
                    .font(.caption2)
                    .foregroundStyle(.red)
                    .lineLimit(1)
            }
        }
    }

    @ViewBuilder
    private var stopButton: some View {
        if isRunning {
            Button(action: onStop) {
                if compactToolbar {
                    Image(systemName: "stop.fill")
                } else {
                    Label("Stop", systemImage: "stop.fill")
                }
            }
            .buttonStyle(.bordered)
            .controlSize(.small)
            .help("Stop the current turn")
        }
    }

    private var sendButton: some View {
        Button(action: onSend) {
            Image(systemName: "arrow.up")
                .font(.system(size: 13, weight: .bold))
                .frame(width: 20, height: 20)
        }
        .buttonStyle(.borderedProminent)
        .buttonBorderShape(.circle)
        .tint(Brand.accent)
        .keyboardShortcut(.return, modifiers: .command)
        .disabled(
            text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                && attachments.isEmpty
        )
    }

    private var metrics: some View {
        HStack(spacing: 4) {
            Circle()
                .fill(statusColor)
                .frame(width: 6, height: 6)
            if !compactToolbar {
                Text(statusText)
                    .lineLimit(1)
            }
            Text(cacheText)
            Text("·")
            Text(compactToolbar ? compactContextText : contextText)
        }
        .font(.caption2)
        .foregroundStyle(.secondary)
        .fixedSize(horizontal: true, vertical: false)
        .layoutPriority(2)
        .help(contextDetails.isEmpty ? "Context window usage for this task" : contextDetails)
    }

    private var compactToolbar: Bool {
        editorWidth < 390
    }

    private var veryCompactToolbar: Bool {
        editorWidth < 285
    }

    private var compactContextText: String {
        contextText.replacingOccurrences(of: "Context ", with: "Ctx ")
    }

    private var measuredTextHeight: CGFloat {
        let value = text.isEmpty ? " " : text + "\n"
        let bounds = (value as NSString).boundingRect(
            with: CGSize(
                width: max(40, editorWidth - 12),
                height: CGFloat.greatestFiniteMagnitude
            ),
            options: [.usesLineFragmentOrigin, .usesFontLeading],
            attributes: [.font: NSFont.preferredFont(forTextStyle: .body)]
        )
        return min(maximumEditorHeight, max(minimumEditorHeight, ceil(bounds.height) + 12))
    }

    private var attachmentStrip: some View {
        ScrollView(.horizontal) {
            HStack(spacing: 8) {
                ForEach(attachments) { attachment in
                    ZStack(alignment: .topTrailing) {
                        if let image = NSImage(contentsOfFile: attachment.path) {
                            Image(nsImage: image)
                                .resizable()
                                .scaledToFill()
                                .frame(width: 86, height: 64)
                                .clipShape(RoundedRectangle(cornerRadius: 9))
                        }
                        Button {
                            ImageAttachmentStore.remove(attachment)
                            attachments.removeAll { $0.id == attachment.id }
                        } label: {
                            Image(systemName: "xmark.circle.fill")
                                .symbolRenderingMode(.palette)
                                .foregroundStyle(.white, .black.opacity(0.65))
                        }
                        .buttonStyle(.plain)
                        .offset(x: 5, y: -5)
                    }
                }
            }
            .padding(.top, 5)
            .padding(.trailing, 5)
        }
        .scrollIndicators(.hidden)
        .frame(height: 70)
    }

    private func pasteImage() {
        guard attachments.count < ImageAttachmentStore.maximumCount else {
            attachmentError = "Attach at most \(ImageAttachmentStore.maximumCount) images."
            return
        }
        do {
            let available = ImageAttachmentStore.maximumCount - attachments.count
            attachments.append(
                contentsOf: try ImageAttachmentStore.importPasteboard(limit: available)
            )
            attachmentError = ""
        } catch {
            attachmentError = error.localizedDescription
        }
    }

    private func installPasteMonitor() {
        guard pasteMonitor == nil else { return }
        pasteMonitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { event in
            let flags = event.modifierFlags.intersection(.deviceIndependentFlagsMask)
            guard focused,
                  !isAnsweringQuestion,
                  flags == .command,
                  event.charactersIgnoringModifiers?.lowercased() == "v",
                  ImageAttachmentStore.pasteboardContainsImage()
            else { return event }
            pasteImage()
            return nil
        }
    }
}
