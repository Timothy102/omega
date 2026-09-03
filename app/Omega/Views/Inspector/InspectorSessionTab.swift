import SwiftUI
import OmegaCore

struct InspectorSessionTab: View {
    let viewModel: TaskViewModel

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                section("Model") {
                    Text(viewModel.task?.model ?? viewModel.selectedModel)
                        .font(.system(.body, design: .monospaced))
                }

                if let tokens = viewModel.statusLine?.tokens, tokens > 0 {
                    section("Tokens (this turn)") {
                        Text(ToolFormatting.formatCount(tokens))
                            .font(.system(.body, design: .monospaced))
                    }
                }

                section("Tool calls this turn") {
                    if toolCounts.isEmpty {
                        Text("No tool calls yet")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    } else {
                        ToolCountChips(counts: toolCounts)
                    }
                }

                section("Files touched") {
                    if touchedFileLines.isEmpty {
                        Text("None yet")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    } else {
                        VStack(alignment: .leading, spacing: 4) {
                            ForEach(Array(touchedFileLines.enumerated()), id: \.offset) { _, line in
                                Text(line)
                                    .font(.system(.caption, design: .monospaced))
                                    .lineLimit(1)
                                    .truncationMode(.middle)
                            }
                        }
                    }
                }
            }
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private var latestToolRows: [ToolRow] {
        for block in viewModel.transcript.blocks.reversed() {
            if case .toolGroup(let g) = block { return g.group.rows }
        }
        return []
    }

    private var toolCounts: [(name: String, count: Int)] {
        var counts: [String: Int] = [:]
        for row in latestToolRows { counts[row.toolName, default: 0] += row.repeatCount }
        return counts.sorted { $0.key < $1.key }.map { (name: $0.key, count: $0.value) }
    }

    private var touchedFileLines: [String] {
        latestToolRows.filter { $0.toolName == "write" || $0.toolName == "edit" }.map { $0.line }
    }

    @ViewBuilder
    private func section<Content: View>(_ title: String, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title.uppercased())
                .font(.caption2)
                .fontWeight(.semibold)
                .foregroundStyle(.secondary)
            content()
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

private struct ToolCountChips: View {
    let counts: [(name: String, count: Int)]

    var body: some View {
        LazyVGrid(columns: [GridItem(.adaptive(minimum: 90), spacing: 6)], alignment: .leading, spacing: 6) {
            ForEach(counts, id: \.name) { entry in
                Text("\(entry.name) ×\(entry.count)")
                    .font(.caption2)
                    .padding(.horizontal, 6)
                    .padding(.vertical, 3)
                    .background(Color.secondary.opacity(0.12), in: Capsule())
            }
        }
    }
}
