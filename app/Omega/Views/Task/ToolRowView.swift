import SwiftUI
import OmegaCore

private extension ToolCategory {
    var color: Color {
        switch self {
        case .read: return .cyan
        case .write: return .yellow
        case .bash: return Color(nsColor: .magenta)
        case .subagent: return .green
        case .memory: return .blue
        case .artifact: return .cyan.opacity(0.6)
        case .askUser: return .yellow
        case .mcp: return .indigo
        case .error: return .red
        }
    }
}

struct ToolGroupBlockView: View {
    let block: ToolGroupBlock
    let viewModel: TaskViewModel

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            ForEach(block.group.visibleRows) { row in
                ToolRowView(row: row, viewModel: viewModel)
            }
            if block.group.hiddenCount > 0 {
                Button {
                    viewModel.transcript.toggleGroup(block.id)
                } label: {
                    Text("… +\(block.group.hiddenCount) more (tap to expand)")
                        .font(.system(.caption, design: .monospaced))
                        .foregroundStyle(.secondary)
                }
                .buttonStyle(.plain)
            }
        }
    }
}

struct ToolRowView: View {
    let row: ToolRow
    let viewModel: TaskViewModel
    var depth: Int = 0

    private var category: ToolCategory { ToolFormatting.category(forToolName: row.toolName) }
    private var glyphColor: Color { row.isError ? .red : category.color }

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack(alignment: .top, spacing: 4) {
                Text(row.isSubagent ? "▶" : "●")
                    .foregroundStyle(glyphColor)
                Text(row.line)
                    .foregroundStyle(row.isError ? .red : .primary)
                    .fontWeight(row.isSubagent ? .semibold : .regular)
                if row.repeatCount > 1 {
                    Text("×\(row.repeatCount)")
                        .foregroundStyle(.secondary)
                }
            }
            .font(.system(.callout, design: .monospaced))

            if let outcome = row.outcome {
                Text("└ \(strippedArrow(outcome))")
                    .font(.system(.caption, design: .monospaced))
                    .foregroundStyle(row.isError ? .red : .secondary)
                    .padding(.leading, 14)
            }

            if let children = row.children {
                VStack(alignment: .leading, spacing: 3) {
                    ForEach(children.visibleRows) { child in
                        ToolRowView(row: child, viewModel: viewModel, depth: depth + 1)
                    }
                    if children.hiddenCount > 0 {
                        Button {
                            viewModel.transcript.toggleSubagentChildren(subagentId: row.id)
                        } label: {
                            Text("… +\(children.hiddenCount) more (tap to expand)")
                                .font(.system(.caption2, design: .monospaced))
                                .foregroundStyle(.secondary)
                        }
                        .buttonStyle(.plain)
                    }
                }
                .padding(.leading, 16)
            }
        }
        .padding(.leading, CGFloat(depth) * 16)
    }

    private func strippedArrow(_ s: String) -> String {
        s.hasPrefix("→ ") ? String(s.dropFirst(2)) : s
    }
}
