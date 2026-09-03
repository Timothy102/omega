import SwiftUI
import OmegaCore

struct DiffTarget: Identifiable, Equatable {
    let path: String
    var id: String { path }
}

struct DiffSheetView: View {
    let appState: AppState
    let taskId: String
    let path: String

    @Environment(\.dismiss) private var dismiss
    @State private var diff: String?
    @State private var errorMessage: String?
    @State private var isLoading = true

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Text(path)
                    .font(.system(.body, design: .monospaced))
                    .lineLimit(1)
                    .truncationMode(.middle)
                Spacer()
                Button("Done") { dismiss() }
            }
            .padding(12)

            Divider()

            ScrollView {
                if isLoading {
                    ProgressView()
                        .frame(maxWidth: .infinity)
                        .padding()
                } else if let errorMessage {
                    Text(errorMessage)
                        .foregroundStyle(.red)
                        .padding()
                } else if let diff {
                    DiffTextView(diff: diff)
                        .padding(12)
                }
            }
        }
        .frame(minWidth: 560, minHeight: 420)
        .task { await load() }
    }

    private func load() async {
        guard let client = appState.client else {
            errorMessage = "Not connected to the daemon."
            isLoading = false
            return
        }
        do {
            let response = try await client.diff(taskId: taskId, path: path)
            diff = response.diff
        } catch {
            errorMessage = error.localizedDescription
        }
        isLoading = false
    }
}

private struct DiffTextView: View {
    let diff: String

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            ForEach(Array(diff.split(separator: "\n", omittingEmptySubsequences: false).enumerated()), id: \.offset) { _, line in
                Text(String(line))
                    .font(.system(.caption, design: .monospaced))
                    .foregroundStyle(color(for: line))
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
    }

    private func color(for line: Substring) -> Color {
        if line.hasPrefix("+"), !line.hasPrefix("+++") { return .green }
        if line.hasPrefix("-"), !line.hasPrefix("---") { return .red }
        if line.hasPrefix("@@") { return .secondary }
        return .primary
    }
}
