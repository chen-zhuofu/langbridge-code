import SwiftUI

enum MarkdownBlock: Equatable {
    case markdown(String)
    case table(MarkdownTable)
}

struct MarkdownTable: Equatable {
    enum Alignment: Equatable {
        case leading
        case center
        case trailing
    }

    let header: [String]
    let alignments: [Alignment]
    let rows: [[String]]
}

enum MarkdownBlockParser {
    static func parse(_ source: String) -> [MarkdownBlock] {
        let lines = source.components(separatedBy: "\n")
        var blocks: [MarkdownBlock] = []
        var markdownLines: [String] = []
        var fenceMarker: Character?
        var index = 0

        func flushMarkdown() {
            guard !markdownLines.isEmpty else { return }
            blocks.append(.markdown(markdownLines.joined(separator: "\n")))
            markdownLines.removeAll(keepingCapacity: true)
        }

        while index < lines.count {
            let line = lines[index]
            let trimmed = line.trimmingCharacters(in: .whitespaces)

            if let marker = fenceMarker {
                markdownLines.append(line)
                if trimmed.hasPrefix(String(repeating: marker, count: 3)) {
                    fenceMarker = nil
                }
                index += 1
                continue
            }

            if trimmed.hasPrefix("```") {
                fenceMarker = "`"
                markdownLines.append(line)
                index += 1
                continue
            }
            if trimmed.hasPrefix("~~~") {
                fenceMarker = "~"
                markdownLines.append(line)
                index += 1
                continue
            }

            if trimmed.isEmpty {
                flushMarkdown()
                index += 1
                continue
            }

            if index + 1 < lines.count,
               let header = cells(in: line),
               let alignments = separatorAlignments(in: lines[index + 1]),
               header.count == alignments.count
            {
                flushMarkdown()
                index += 2
                var rows: [[String]] = []
                while index < lines.count,
                      !lines[index].trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
                      let row = cells(in: lines[index])
                {
                    rows.append(normalize(row, count: header.count))
                    index += 1
                }
                blocks.append(.table(.init(
                    header: header,
                    alignments: alignments,
                    rows: rows
                )))
                continue
            }

            markdownLines.append(line)
            index += 1
        }

        flushMarkdown()
        return blocks
    }

    private static func cells(in line: String) -> [String]? {
        guard line.contains("|") else { return nil }
        var value = line.trimmingCharacters(in: .whitespaces)
        if value.first == "|" { value.removeFirst() }
        if value.last == "|", !value.hasSuffix("\\|") { value.removeLast() }

        var result: [String] = []
        var cell = ""
        var escaped = false
        for character in value {
            if escaped {
                if character != "|" { cell.append("\\") }
                cell.append(character)
                escaped = false
            } else if character == "\\" {
                escaped = true
            } else if character == "|" {
                result.append(cell.trimmingCharacters(in: .whitespaces))
                cell = ""
            } else {
                cell.append(character)
            }
        }
        if escaped { cell.append("\\") }
        result.append(cell.trimmingCharacters(in: .whitespaces))
        return result
    }

    private static func separatorAlignments(in line: String) -> [MarkdownTable.Alignment]? {
        guard let parts = cells(in: line), !parts.isEmpty else { return nil }
        var result: [MarkdownTable.Alignment] = []
        for part in parts {
            var marker = part.trimmingCharacters(in: .whitespaces)
            let leadingColon = marker.first == ":"
            let trailingColon = marker.last == ":"
            if leadingColon { marker.removeFirst() }
            if trailingColon, !marker.isEmpty { marker.removeLast() }
            guard marker.count >= 3, marker.allSatisfy({ $0 == "-" }) else { return nil }
            if leadingColon && trailingColon {
                result.append(.center)
            } else if trailingColon {
                result.append(.trailing)
            } else {
                result.append(.leading)
            }
        }
        return result
    }

    private static func normalize(_ cells: [String], count: Int) -> [String] {
        var normalized = Array(cells.prefix(count))
        if normalized.count < count {
            normalized.append(contentsOf: repeatElement("", count: count - normalized.count))
        }
        return normalized
    }
}

struct MarkdownContentView: View {
    let source: String
    var isSelectable: Bool = true

    var body: some View {
        let blocks = MarkdownBlockParser.parse(source)

        Group {
            if blocks.contains(where: isTable) {
                VStack(alignment: .leading, spacing: 12) {
                    ForEach(Array(blocks.enumerated()), id: \.offset) { _, block in
                        switch block {
                        case let .markdown(value):
                            markdownText(value)
                                .frame(maxWidth: .infinity, alignment: .leading)
                        case let .table(table):
                            MarkdownTableView(table: table)
                        }
                    }
                }
            } else {
                continuousMarkdownText(blocks)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .selectableText(isSelectable)
    }

    private func isTable(_ block: MarkdownBlock) -> Bool {
        if case .table = block {
            return true
        }
        return false
    }

    private func continuousMarkdownText(_ blocks: [MarkdownBlock]) -> Text {
        var result = Text("")
        var hasContent = false

        for block in blocks {
            guard case let .markdown(value) = block else {
                continue
            }
            if hasContent {
                result = result + Text("\n\n")
            }
            result = result + markdownText(value)
            hasContent = true
        }

        return result
    }

    private func markdownText(_ value: String) -> Text {
        if let attributed = try? AttributedString(markdown: value) {
            return Text(attributed)
        }
        return Text(value)
    }
}

private struct MarkdownTableView: View {
    let table: MarkdownTable

    var body: some View {
        ScrollView(.horizontal) {
            Grid(horizontalSpacing: 0, verticalSpacing: 0) {
                GridRow {
                    ForEach(table.header.indices, id: \.self) { column in
                        cell(table.header[column], column: column, isHeader: true)
                    }
                }
                ForEach(table.rows.indices, id: \.self) { row in
                    GridRow {
                        ForEach(table.header.indices, id: \.self) { column in
                            cell(table.rows[row][column], column: column, isHeader: false)
                        }
                    }
                }
            }
            .clipShape(RoundedRectangle(cornerRadius: 8))
            .overlay {
                RoundedRectangle(cornerRadius: 8)
                    .stroke(Color.secondary.opacity(0.28), lineWidth: 1)
            }
        }
        .scrollIndicators(.visible)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func cell(_ value: String, column: Int, isHeader: Bool) -> some View {
        Text(inlineMarkdown(value))
            .font(isHeader ? .callout.weight(.semibold) : .callout)
            .padding(.horizontal, 10)
            .padding(.vertical, 8)
            .frame(
                minWidth: 96,
                maxWidth: 280,
                alignment: swiftUIAlignment(table.alignments[column])
            )
            .background(isHeader ? Color.primary.opacity(0.08) : Color.clear)
            .overlay {
                Rectangle().stroke(Color.secondary.opacity(0.18), lineWidth: 0.5)
            }
    }

    private func inlineMarkdown(_ value: String) -> AttributedString {
        (try? AttributedString(
            markdown: value,
            options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)
        )) ?? AttributedString(value)
    }

    private func swiftUIAlignment(_ alignment: MarkdownTable.Alignment) -> Alignment {
        switch alignment {
        case .leading: .leading
        case .center: .center
        case .trailing: .trailing
        }
    }
}
