import SwiftUI
import OmegaCore

struct SidebarView: View {
    @Bindable var appState: AppState

    @State private var showingNewTask = false
    @State private var showingQuickSwitcher = false

    private var selectionBinding: Binding<SidebarSelection?> {
        Binding(
            get: { appState.selection },
            set: { newValue in
                if let newValue { appState.selection = newValue }
            }
        )
    }

    var body: some View {
        List(selection: selectionBinding) {
            Section {
                Label("Overview", systemImage: "square.grid.2x2")
                    .tag(SidebarSelection.overview)
            }

            ForEach(appState.tasksByRepo, id: \.repo) { group in
                Section {
                    ForEach(group.tasks) { task in
                        SidebarTaskRow(task: task)
                            .tag(SidebarSelection.task(task.id))
                    }
                } header: {
                    repoHeader(repo: group.repo, branch: group.branch)
                }
            }
        }
        .listStyle(.sidebar)
        .searchable(text: $appState.searchText, prompt: "Search tasks")
        .navigationSplitViewColumnWidth(min: 220, ideal: 260, max: 340)
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                Button {
                    showingNewTask = true
                } label: {
                    Label("New Task", systemImage: "plus")
                }
                .help("New Task (⌘N)")
            }
        }
        .onReceive(NotificationCenter.default.publisher(for: .omegaShowNewTaskSheet)) { _ in
            showingNewTask = true
        }
        .sheet(isPresented: $showingNewTask) {
            NewTaskSheet(appState: appState, isPresented: $showingNewTask)
        }
        .sheet(isPresented: $showingQuickSwitcher) {
            QuickSwitcherView(appState: appState, isPresented: $showingQuickSwitcher)
        }
        .background(keyboardShortcutButtons)
    }

    private func repoHeader(repo: String, branch: String) -> some View {
        (Text(repo) + Text("  ·  \(branch)").foregroundColor(.secondary))
    }

    @ViewBuilder
    private var keyboardShortcutButtons: some View {
        ForEach(1...9, id: \.self) { number in
            Button("") {
                selectTask(atIndex: number - 1)
            }
            .keyboardShortcut(KeyEquivalent(Character("\(number)")), modifiers: .command)
            .frame(width: 0, height: 0)
            .opacity(0)
        }
        Button("") {
            showingQuickSwitcher = true
        }
        .keyboardShortcut("k", modifiers: .command)
        .frame(width: 0, height: 0)
        .opacity(0)
    }

    private func selectTask(atIndex index: Int) {
        let tasks = appState.filteredTasks
        guard index < tasks.count else { return }
        appState.selection = .task(tasks[index].id)
    }
}

private struct SidebarTaskRow: View {
    var task: OmegaTask

    var body: some View {
        HStack(spacing: 8) {
            StatusDot(status: task.status)

            VStack(alignment: .leading, spacing: 2) {
                Text(task.title)
                    .lineLimit(1)
                    .truncationMode(.tail)
                HStack(spacing: 6) {
                    ModelChip(text: task.model)
                    if let pr = task.pr {
                        PRBadge(pr: pr)
                    }
                }
            }

            Spacer(minLength: 8)

            Text(ElapsedFormatter.string(from: task.elapsedSeconds))
                .font(.caption)
                .foregroundStyle(.secondary)
                .monospacedDigit()
        }
        .padding(.vertical, 2)
    }
}
