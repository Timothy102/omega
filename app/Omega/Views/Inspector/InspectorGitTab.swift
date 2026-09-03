import SwiftUI
import OmegaCore

struct InspectorGitTab: View {
    let appState: AppState
    let viewModel: TaskViewModel

    @State private var gitState: GitRepoState?
    @State private var isLoading = false
    @State private var errorMessage: String?
    @State private var diffTarget: DiffTarget?
    @State private var prTitle: String = ""
    @State private var prBody: String = ""
    @State private var createdPR: PullRequest?
    @State private var isCreatingPR = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                if let gitState {
                    HStack(spacing: 6) {
                        Text(gitState.branch)
                            .font(.system(.body, design: .monospaced))
                            .fontWeight(.semibold)
                        if gitState.dirty {
                            Text("dirty")
                                .font(.caption2)
                                .foregroundStyle(.orange)
                        }
                    }

                    if gitState.changes.isEmpty {
                        Text("No changes")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    } else {
                        VStack(spacing: 2) {
                            ForEach(gitState.changes) { change in
                                Button {
                                    diffTarget = DiffTarget(path: change.path)
                                } label: {
                                    GitChangeRow(change: change)
                                }
                                .buttonStyle(.plain)
                            }
                        }
                    }

                    Divider()

                    createPRSection
                } else if isLoading {
                    ProgressView()
                        .frame(maxWidth: .infinity)
                } else if let errorMessage {
                    Text(errorMessage)
                        .font(.caption)
                        .foregroundStyle(.red)
                }
            }
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .task { await load() }
        .sheet(item: $diffTarget) { target in
            DiffSheetView(appState: appState, taskId: viewModel.taskId, path: target.path)
        }
    }

    @ViewBuilder
    private var createPRSection: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("PULL REQUEST")
                .font(.caption2)
                .fontWeight(.semibold)
                .foregroundStyle(.secondary)

            if let pr = createdPR ?? viewModel.task?.pr {
                HStack {
                    PRBadge(pr: pr)
                    if let url = URL(string: pr.url) {
                        Link(pr.title, destination: url)
                            .font(.caption)
                            .lineLimit(1)
                    }
                }
            } else {
                TextField("Title (optional)", text: $prTitle)
                    .textFieldStyle(.roundedBorder)
                TextField("Body (optional)", text: $prBody)
                    .textFieldStyle(.roundedBorder)
                Button {
                    Task { await createPR() }
                } label: {
                    if isCreatingPR {
                        ProgressView().controlSize(.small)
                    } else {
                        Text("Create PR")
                    }
                }
                .disabled(isCreatingPR)
            }
        }
    }

    private func load() async {
        guard let client = appState.client else { return }
        isLoading = true
        defer { isLoading = false }
        do {
            gitState = try await client.gitState(taskId: viewModel.taskId)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func createPR() async {
        guard let client = appState.client else { return }
        isCreatingPR = true
        defer { isCreatingPR = false }
        do {
            createdPR = try await client.createPR(
                taskId: viewModel.taskId,
                title: prTitle.isEmpty ? nil : prTitle,
                body: prBody.isEmpty ? nil : prBody
            )
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}

private struct GitChangeRow: View {
    let change: GitChange

    var body: some View {
        HStack(spacing: 6) {
            Text(change.status.rawValue)
                .font(.system(.caption, design: .monospaced))
                .fontWeight(.bold)
                .foregroundStyle(statusColor)
                .frame(width: 14)
            Text(change.path)
                .font(.system(.caption, design: .monospaced))
                .lineLimit(1)
                .truncationMode(.middle)
            Spacer()
            Text("+\(change.added) −\(change.removed)")
                .font(.system(.caption2, design: .monospaced))
                .foregroundStyle(.secondary)
            if change.touchedThisSession {
                Circle()
                    .fill(Color.accentColor)
                    .frame(width: 5, height: 5)
            }
        }
        .padding(.vertical, 2)
    }

    private var statusColor: Color {
        switch change.status {
        case .modified, .renamed: return .yellow
        case .added: return .green
        case .deleted: return .red
        case .untracked: return .secondary
        }
    }
}
