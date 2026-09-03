import SwiftUI
import OmegaCore

struct ComposerView: View {
    let appState: AppState
    @Bindable var viewModel: TaskViewModel

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                Picker("Mode", selection: $viewModel.mode) {
                    ForEach(TaskMode.allCases, id: \.self) { mode in
                        Text(mode.rawValue.capitalized).tag(mode)
                    }
                }
                .labelsHidden()
                .pickerStyle(.segmented)
                .frame(width: 220)
                .onChange(of: viewModel.mode) { _, newMode in
                    Task { await viewModel.setMode(newMode) }
                }

                Menu {
                    ForEach(appState.availableModels) { model in
                        Button {
                            Task { await viewModel.setModel(model.alias) }
                        } label: {
                            if model.alias == viewModel.selectedModel {
                                Label(model.alias, systemImage: "checkmark")
                            } else {
                                Text(model.alias)
                            }
                        }
                    }
                } label: {
                    Label(viewModel.selectedModel, systemImage: "cpu")
                        .font(.callout)
                }
                .menuStyle(.borderlessButton)
                .fixedSize()

                Spacer()

                Button("Cancel") {
                    Task { await viewModel.cancelRun() }
                }
                .disabled(viewModel.task?.status != .running)
            }

            ZStack(alignment: .topLeading) {
                TextEditor(text: $viewModel.composerText)
                    .font(.body)
                    .scrollContentBackground(.hidden)
                    .frame(minHeight: 56, maxHeight: 140)
                    .padding(6)
                    .background(Color.primary.opacity(0.04), in: RoundedRectangle(cornerRadius: 8))

                if viewModel.composerText.isEmpty {
                    Text("Message the agent…")
                        .foregroundStyle(.secondary)
                        .padding(.horizontal, 11)
                        .padding(.vertical, 14)
                        .allowsHitTesting(false)
                }
            }

            HStack {
                Spacer()
                Button {
                    Task { await viewModel.send() }
                } label: {
                    if viewModel.isSending {
                        ProgressView().controlSize(.small)
                    } else {
                        Label("Send", systemImage: "paperplane.fill")
                    }
                }
                .keyboardShortcut(.return, modifiers: .command)
                .buttonStyle(.borderedProminent)
                .disabled(isSendDisabled)
            }
        }
        .padding(12)
    }

    private var isSendDisabled: Bool {
        viewModel.composerText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || viewModel.isSending
    }
}
