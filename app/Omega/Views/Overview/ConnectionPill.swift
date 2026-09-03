import SwiftUI
import OmegaCore

struct ConnectionPill: View {
    var connection: Connection

    var body: some View {
        HStack(spacing: 6) {
            Circle()
                .fill(ConnectionStyle.color(for: connection.state))
                .frame(width: 6, height: 6)
            Text(connection.name)
                .font(.caption.weight(.medium))
            Text("×\(connection.tools)")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
        .background(Color.secondary.opacity(0.08), in: Capsule())
    }
}
