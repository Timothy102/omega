import SwiftUI
import OmegaCore

struct TaskDetailView: View {
    let appState: AppState
    let taskId: String

    @State private var viewModel: TaskViewModel
    @State private var showInspector = false
    @State private var showTerminal = false

    init(appState: AppState, taskId: String) {
        self.appState = appState
        self.taskId = taskId
        _viewModel = State(initialValue: appState.viewModel(for: taskId))
    }

    var body: some View {
        HStack(spacing: 0) {
            VStack(spacing: 0) {
                TranscriptView(viewModel: viewModel)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)

                if showTerminal {
                    Divider()
                    TerminalDrawerView(appState: appState, viewModel: viewModel)
                        .frame(height: 260)
                }

                if let errorMessage = viewModel.errorMessage {
                    Divider()
                    ErrorBanner(message: errorMessage) { viewModel.errorMessage = nil }
                }

                if let statusLine = viewModel.statusLine {
                    Divider()
                    StatusLineView(statusLine: statusLine)
                }

                Divider()
                ComposerView(appState: appState, viewModel: viewModel)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)

            if showInspector {
                Divider()
                InspectorView(appState: appState, viewModel: viewModel)
                    .frame(width: 320)
            }
        }
        .toolbar {
            ToolbarItemGroup(placement: .primaryAction) {
                Button {
                    showTerminal.toggle()
                } label: {
                    Image(systemName: "terminal")
                }
                .keyboardShortcut("t", modifiers: [.command, .option])
                .help("Toggle Terminal (⌥⌘T)")

                Button {
                    showInspector.toggle()
                } label: {
                    Image(systemName: "sidebar.right")
                }
                .keyboardShortcut("i", modifiers: [.command, .option])
                .help("Toggle Inspector (⌥⌘I)")
            }
        }
        .navigationTitle(viewModel.task?.title ?? "Task")
        .onDisappear {
            viewModel.stop()
        }
    }
}

private struct ErrorBanner: View {
    let message: String
    let dismiss: () -> Void

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: "exclamationmark.triangle.fill")
                .foregroundStyle(.red)
            Text(message)
                .font(.callout)
                .lineLimit(2)
            Spacer()
            Button {
                dismiss()
            } label: {
                Image(systemName: "xmark")
            }
            .buttonStyle(.plain)
        }
        .padding(8)
        .background(Color.red.opacity(0.1), in: RoundedRectangle(cornerRadius: 6))
        .padding(.horizontal, 12)
        .padding(.top, 8)
    }
}
