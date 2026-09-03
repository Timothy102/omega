import SwiftUI
import OmegaCore

struct OverviewTaskCard: View {
    var task: OmegaTask
    var onSelect: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                StatusDot(status: task.status)
                Text(task.title)
                    .font(.headline)
                    .lineLimit(1)
                Spacer()
                ModelChip(text: task.model)
            }

            Text("\(task.repoName) · \(task.branch)")
                .font(.caption)
                .foregroundStyle(.secondary)
                .lineLimit(1)

            Text(statusLine)
                .font(.subheadline)
                .foregroundStyle(.secondary)

            if task.tokensUsed != nil || task.costUsd != nil {
                HStack(spacing: 12) {
                    if let tokens = task.tokensUsed {
                        Label("\(tokens)", systemImage: "number")
                    }
                    if let cost = task.costUsd {
                        Label(String(format: "$%.2f", cost), systemImage: "dollarsign.circle")
                    }
                }
                .font(.caption)
                .foregroundStyle(.secondary)
            }

            if task.status == .waitingInput {
                Button(action: onSelect) {
                    Label("Needs your input", systemImage: "arrow.right")
                        .font(.subheadline.weight(.medium))
                }
                .buttonStyle(.borderless)
                .tint(Color.accentColor)
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.secondary.opacity(0.06), in: RoundedRectangle(cornerRadius: 12))
        .contentShape(Rectangle())
        .onTapGesture(perform: onSelect)
    }

    private var statusLine: String {
        let elapsed = ElapsedFormatter.string(from: task.elapsedSeconds)
        switch task.status {
        case .running: return "Running · \(elapsed)"
        case .waitingInput: return "Waiting for you · \(elapsed)"
        case .idle: return "Idle · \(elapsed)"
        case .done: return "Done · \(elapsed)"
        case .failed: return "Failed · \(elapsed)"
        }
    }
}
