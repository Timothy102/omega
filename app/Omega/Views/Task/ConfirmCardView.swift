import SwiftUI
import OmegaCore

struct ConfirmCardView: View {
    let block: ConfirmCardBlock
    let viewModel: TaskViewModel

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 6) {
                Image(systemName: "exclamationmark.shield")
                    .foregroundStyle(.orange)
                Text(block.request.toolName)
                    .font(.system(.body, design: .monospaced))
                    .fontWeight(.semibold)
            }

            if !block.request.argsPreview.isEmpty {
                Text(block.request.argsPreview)
                    .font(.system(.caption, design: .monospaced))
                    .foregroundStyle(.secondary)
            }

            if let risk = block.request.risk {
                Text("Risk: \(risk)")
                    .font(.caption)
                    .foregroundStyle(.orange)
            }

            if block.resolved {
                Label(
                    block.approved == true ? "Approved" : "Denied",
                    systemImage: block.approved == true ? "checkmark.circle.fill" : "xmark.circle.fill"
                )
                .font(.callout)
                .foregroundStyle(.secondary)
            } else {
                HStack {
                    Button("Deny", role: .destructive) {
                        Task { await viewModel.resolveConfirm(requestId: block.request.requestId, approved: false) }
                    }
                    Button("Approve") {
                        Task { await viewModel.resolveConfirm(requestId: block.request.requestId, approved: true) }
                    }
                    .buttonStyle(.borderedProminent)
                }
            }
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.orange.opacity(0.08), in: RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.orange.opacity(0.35), lineWidth: 1))
    }
}
