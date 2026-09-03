import SwiftUI
import OmegaCore

struct TerminalDrawerView: View {
    let appState: AppState
    let viewModel: TaskViewModel

    @State private var terminals: [Terminal] = []
    @State private var selectedTerminalId: String?
    @State private var isCreating = false
    @State private var errorMessage: String?

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 4) {
                ForEach(terminals) { terminal in
                    Button {
                        selectedTerminalId = terminal.id
                    } label: {
                        Text(terminal.title ?? shortId(terminal.id))
                            .font(.caption)
                            .padding(.horizontal, 8)
                            .padding(.vertical, 4)
                            .background(
                                selectedTerminalId == terminal.id ? Color.accentColor.opacity(0.2) : Color.clear,
                                in: RoundedRectangle(cornerRadius: 4)
                            )
                    }
                    .buttonStyle(.plain)
                }

                Button {
                    Task { await createTerminal() }
                } label: {
                    if isCreating {
                        ProgressView().controlSize(.small)
                    } else {
                        Image(systemName: "plus")
                    }
                }
                .buttonStyle(.plain)
                .disabled(isCreating)

                if let errorMessage {
                    Text(errorMessage)
                        .font(.caption2)
                        .foregroundStyle(.red)
                        .lineLimit(1)
                }

                Spacer()
            }
            .padding(6)

            Divider()

            content
        }
        .onAppear {
            terminals = appState.terminals.filter { $0.taskId == viewModel.taskId }
            if selectedTerminalId == nil {
                selectedTerminalId = terminals.first?.id
            }
        }
    }

    @ViewBuilder
    private var content: some View {
        if let selectedTerminalId, let terminal = terminals.first(where: { $0.id == selectedTerminalId }) {
            if appState.settings.terminalsOpenExternally {
                ExternalTerminalPlaceholder(cwd: terminal.cwd)
            } else if let client = appState.client {
                TerminalPaneView(client: client, terminalId: terminal.id)
                    .id(terminal.id)
            } else {
                Text("Not connected to the daemon")
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        } else {
            VStack(spacing: 8) {
                Text("No terminal open")
                    .foregroundStyle(.secondary)
                Button("New Terminal") {
                    Task { await createTerminal() }
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
    }

    private func createTerminal() async {
        guard let client = appState.client else {
            errorMessage = "Not connected to the daemon."
            return
        }
        isCreating = true
        defer { isCreating = false }
        do {
            let terminal = try await client.createTerminal(
                taskId: viewModel.taskId,
                cwd: viewModel.task?.repoPath ?? NSHomeDirectory()
            )
            terminals.append(terminal)
            selectedTerminalId = terminal.id
            errorMessage = nil
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func shortId(_ id: String) -> String {
        String(id.prefix(6))
    }
}

private struct ExternalTerminalPlaceholder: View {
    let cwd: String

    var body: some View {
        VStack(spacing: 6) {
            Image(systemName: "terminal")
                .font(.title2)
                .foregroundStyle(.secondary)
            Text("Terminal running in")
                .foregroundStyle(.secondary)
            Text(cwd)
                .font(.system(.caption, design: .monospaced))
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}
