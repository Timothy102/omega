import SwiftUI
import OmegaCore

struct InspectorConnectionsTab: View {
    let appState: AppState

    var body: some View {
        ScrollView {
            VStack(spacing: 0) {
                if appState.connections.isEmpty {
                    Text("No connections")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .padding(12)
                } else {
                    ForEach(appState.connections) { connection in
                        HStack(spacing: 8) {
                            Circle()
                                .fill(ConnectionStyle.color(for: connection.state))
                                .frame(width: 8, height: 8)
                            VStack(alignment: .leading, spacing: 1) {
                                Text(connection.name)
                                    .font(.system(.body, design: .monospaced))
                                Text(stateLabel(connection))
                                    .font(.caption2)
                                    .foregroundStyle(.secondary)
                            }
                            Spacer()
                            if connection.tools > 0 {
                                Text("\(connection.tools) tools")
                                    .font(.caption2)
                                    .foregroundStyle(.secondary)
                            }
                        }
                        .padding(.vertical, 6)
                        .padding(.horizontal, 12)
                        Divider()
                    }
                }
            }
        }
    }

    private func stateLabel(_ connection: Connection) -> String {
        if let error = connection.error { return error }
        return connection.state.rawValue.replacingOccurrences(of: "_", with: " ")
    }
}
