import SwiftUI
import AppKit
import OmegaCore

struct NewTaskSheet: View {
    var appState: AppState
    @Binding var isPresented: Bool

    @State private var repoPath = ""
    @State private var prompt = ""
    @State private var model = ""
    @State private var mode: TaskMode = .build
    @State private var useWorktree = true
    @State private var isCreating = false

    private var modelOptions: [String] {
        guard !appState.availableModels.isEmpty else { return ["opus", "sonnet"] }
        return appState.availableModels.map(\.alias)
    }

    private var canCreate: Bool {
        !repoPath.isEmpty && !prompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && !isCreating
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("New Task")
                .font(.title2.bold())

            VStack(alignment: .leading, spacing: 6) {
                Text("Repository")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                HStack(spacing: 8) {
                    Picker("", selection: $repoPath) {
                        if repoPath.isEmpty {
                            Text("Choose a repo…").tag("")
                        }
                        ForEach(appState.settings.recentRepoPaths, id: \.self) { path in
                            Text((path as NSString).lastPathComponent).tag(path)
                        }
                    }
                    .labelsHidden()

                    Button("Choose Folder…", action: chooseFolder)
                }
            }

            VStack(alignment: .leading, spacing: 6) {
                Text("Prompt")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                TextEditor(text: $prompt)
                    .font(.body)
                    .frame(height: 120)
                    .overlay(
                        RoundedRectangle(cornerRadius: 6)
                            .stroke(Color.secondary.opacity(0.25))
                    )
            }

            HStack(alignment: .top, spacing: 24) {
                VStack(alignment: .leading, spacing: 6) {
                    Text("Model")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                    Picker("", selection: $model) {
                        ForEach(modelOptions, id: \.self) { alias in
                            Text(alias).tag(alias)
                        }
                    }
                    .labelsHidden()
                }

                VStack(alignment: .leading, spacing: 6) {
                    Text("Mode")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                    Picker("", selection: $mode) {
                        ForEach(TaskMode.allCases, id: \.self) { mode in
                            Text(mode.rawValue.capitalized).tag(mode)
                        }
                    }
                    .pickerStyle(.segmented)
                    .labelsHidden()
                }
            }

            Toggle("Use a worktree", isOn: $useWorktree)

            HStack {
                Spacer()
                Button("Cancel") {
                    isPresented = false
                }
                .keyboardShortcut(.cancelAction)

                Button("Create", action: createTask)
                    .keyboardShortcut(.defaultAction)
                    .disabled(!canCreate)
            }
        }
        .padding(20)
        .frame(width: 480)
        .onAppear {
            model = appState.settings.defaultModel
            if repoPath.isEmpty {
                repoPath = appState.settings.recentRepoPaths.first ?? ""
            }
        }
    }

    private func chooseFolder() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        guard panel.runModal() == .OK, let url = panel.url else { return }
        repoPath = url.path
    }

    private func createTask() {
        isCreating = true
        let path = repoPath
        let text = prompt
        let selectedModel = model
        let selectedMode = mode
        let worktree = useWorktree
        Task {
            await appState.createTask(
                repoPath: path, prompt: text, model: selectedModel, mode: selectedMode, useWorktree: worktree
            )
            isCreating = false
            isPresented = false
        }
    }
}
