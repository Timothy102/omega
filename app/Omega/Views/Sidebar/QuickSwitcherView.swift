import SwiftUI
import OmegaCore

struct QuickSwitcherView: View {
    var appState: AppState
    @Binding var isPresented: Bool

    @State private var query = ""
    @FocusState private var isFocused: Bool

    private var results: [OmegaTask] {
        guard !query.isEmpty else { return appState.tasks }
        return appState.tasks.filter {
            $0.title.localizedCaseInsensitiveContains(query)
                || $0.repoName.localizedCaseInsensitiveContains(query)
        }
    }

    var body: some View {
        VStack(spacing: 0) {
            TextField("Jump to task…", text: $query)
                .textFieldStyle(.plain)
                .font(.system(size: 16))
                .padding(12)
                .focused($isFocused)
                .onSubmit(selectFirstResult)

            Divider()

            if results.isEmpty {
                Text("No matching tasks")
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                List(results) { task in
                    Button {
                        select(task)
                    } label: {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(task.title)
                            Text("\(task.repoName) · \(task.branch)")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                    .buttonStyle(.plain)
                }
                .listStyle(.plain)
            }
        }
        .frame(width: 480, height: 360)
        .onAppear { isFocused = true }
    }

    private func selectFirstResult() {
        guard let first = results.first else { return }
        select(first)
    }

    private func select(_ task: OmegaTask) {
        appState.selection = .task(task.id)
        isPresented = false
    }
}
