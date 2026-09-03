import SwiftUI
import OmegaCore

struct StatusLineView: View {
    let statusLine: StatusLine

    var body: some View {
        HStack(spacing: 6) {
            ProgressView()
                .controlSize(.small)
            Text(statusLine.verb)
            Text(detailText)
        }
        .font(.system(.caption, design: .monospaced))
        .foregroundStyle(.secondary)
        .padding(.horizontal, 12)
        .padding(.vertical, 4)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var detailText: String {
        var text = "(\(Int(statusLine.elapsedSeconds))s"
        if statusLine.tokens > 0 {
            text += " · ↓ \(ToolFormatting.formatCount(statusLine.tokens)) tokens"
        }
        text += ")"
        return text
    }
}
