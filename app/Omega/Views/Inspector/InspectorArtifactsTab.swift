import SwiftUI
import OmegaCore

struct InspectorArtifactsTab: View {
    let appState: AppState
    let viewModel: TaskViewModel

    @State private var artifacts: [Artifact] = []
    @State private var isLoading = false
    @State private var errorMessage: String?
    @State private var selected: Artifact?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                if isLoading {
                    ProgressView()
                        .frame(maxWidth: .infinity)
                        .padding()
                } else if let errorMessage {
                    Text(errorMessage)
                        .font(.caption)
                        .foregroundStyle(.red)
                        .padding()
                } else if artifacts.isEmpty {
                    Text("No artifacts")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .padding()
                } else {
                    ForEach(artifacts) { artifact in
                        Button {
                            Task { await openArtifact(artifact) }
                        } label: {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(artifact.title)
                                    .font(.body)
                                Text(artifact.kind)
                                    .font(.caption2)
                                    .foregroundStyle(.secondary)
                            }
                            .frame(maxWidth: .infinity, alignment: .leading)
                        }
                        .buttonStyle(.plain)
                        .padding(.vertical, 6)
                        .padding(.horizontal, 12)
                        Divider()
                    }
                }
            }
        }
        .task { await load() }
        .sheet(item: $selected) { artifact in
            ArtifactPreviewView(artifact: artifact)
        }
    }

    private func load() async {
        guard let client = appState.client else { return }
        isLoading = true
        defer { isLoading = false }
        do {
            artifacts = try await client.artifacts(taskId: viewModel.taskId)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func openArtifact(_ artifact: Artifact) async {
        guard let client = appState.client else { return }
        do {
            selected = try await client.artifact(taskId: viewModel.taskId, artifactId: artifact.id)
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}

private struct ArtifactPreviewView: View {
    let artifact: Artifact
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Text(artifact.title)
                    .font(.headline)
                Spacer()
                Button("Done") { dismiss() }
            }
            .padding(12)

            Divider()

            ScrollView {
                Text(artifact.content ?? "No content")
                    .font(.system(.caption, design: .monospaced))
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(12)
                    .textSelection(.enabled)
            }
        }
        .frame(minWidth: 480, minHeight: 360)
    }
}
