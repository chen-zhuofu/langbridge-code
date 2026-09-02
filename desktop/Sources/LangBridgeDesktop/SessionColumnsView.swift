import SwiftUI

enum SessionColumnSizing {
    static func defaultDividers(count: Int) -> [CGFloat] {
        guard count > 1 else { return [] }
        return (1..<count).map { CGFloat($0) / CGFloat(count) }
    }

    static func widths(
        total: CGFloat,
        count: Int,
        dividers: [CGFloat],
        minimum: CGFloat = 255
    ) -> [CGFloat] {
        guard count > 0 else { return [] }
        let available = max(0, total)
        let effectiveMinimum = min(minimum, available / CGFloat(count))
        var boundaries: [CGFloat] = [0]

        for index in 0..<(count - 1) {
            let proposed = (dividers.indices.contains(index)
                ? dividers[index]
                : CGFloat(index + 1) / CGFloat(count)) * available
            let lower = boundaries[index] + effectiveMinimum
            let upper = available - effectiveMinimum * CGFloat(count - index - 1)
            boundaries.append(min(max(proposed, lower), upper))
        }
        boundaries.append(available)

        return (0..<count).map { boundaries[$0 + 1] - boundaries[$0] }
    }
}

struct SessionColumnsView: View {
    @ObservedObject var model: AppModel
    @State private var dividerFractions: [CGFloat] = []
    @State private var dragStarts: [Int: CGFloat] = [:]

    private let dividerWidth: CGFloat = 7
    private let minimumColumnWidth: CGFloat = 255

    var body: some View {
        GeometryReader { geometry in
            let tasks = model.visibleTasks
            let available = max(
                0,
                geometry.size.width - dividerWidth * CGFloat(max(0, tasks.count - 1))
            )
            let widths = SessionColumnSizing.widths(
                total: available,
                count: tasks.count,
                dividers: dividerFractions,
                minimum: minimumColumnWidth
            )

            HStack(spacing: 0) {
                ForEach(Array(tasks.enumerated()), id: \.element.id) { index, task in
                    ChatView(
                        task: task,
                        isActiveColumn: model.selectedTaskID == task.id,
                        onActivate: { model.showTask(task) },
                        onCloseColumn: { model.hideColumn(task) }
                    )
                    .id(task.id)
                    .frame(width: widths[index], height: geometry.size.height)
                    .clipped()

                    if index < tasks.count - 1 {
                        ColumnDivider(
                            onChanged: { translation in
                                resizeDivider(
                                    at: index,
                                    translation: translation,
                                    available: available,
                                    count: tasks.count
                                )
                            },
                            onEnded: { dragStarts[index] = nil }
                        )
                        .frame(width: dividerWidth, height: geometry.size.height)
                    }
                }
            }
            .frame(width: geometry.size.width, height: geometry.size.height, alignment: .leading)
            .clipped()
            .onAppear { resetDividers(for: tasks.count) }
            .onChange(of: tasks.map(\.id)) {
                resetDividers(for: tasks.count)
            }
        }
    }

    private func resetDividers(for count: Int) {
        dividerFractions = SessionColumnSizing.defaultDividers(count: count)
        dragStarts = [:]
    }

    private func resizeDivider(
        at index: Int,
        translation: CGFloat,
        available: CGFloat,
        count: Int
    ) {
        guard available > 0, count > 1 else { return }
        let widths = SessionColumnSizing.widths(
            total: available,
            count: count,
            dividers: dividerFractions,
            minimum: minimumColumnWidth
        )
        var boundaries: [CGFloat] = [0]
        for width in widths { boundaries.append(boundaries.last! + width) }
        if dragStarts[index] == nil {
            dragStarts[index] = boundaries[index + 1]
        }

        let effectiveMinimum = min(minimumColumnWidth, available / CGFloat(count))
        let lower = boundaries[index] + effectiveMinimum
        let upper = boundaries[index + 2] - effectiveMinimum
        let proposed = (dragStarts[index] ?? boundaries[index + 1]) + translation
        dividerFractions[index] = min(max(proposed, lower), upper) / available
    }
}

private struct ColumnDivider: View {
    let onChanged: (CGFloat) -> Void
    let onEnded: () -> Void
    @State private var hovered = false

    var body: some View {
        Rectangle()
            .fill(Color.clear)
            .contentShape(Rectangle())
            .overlay {
                Rectangle()
                    .fill(Color.primary.opacity(hovered ? 0.28 : 0.12))
                    .frame(width: hovered ? 2 : 1)
            }
            .onHover { hovered = $0 }
            .gesture(
                DragGesture(minimumDistance: 1)
                    .onChanged { onChanged($0.translation.width) }
                    .onEnded { _ in onEnded() }
            )
            .help("Drag to resize columns")
    }
}
