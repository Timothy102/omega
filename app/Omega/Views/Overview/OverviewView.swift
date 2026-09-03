import SwiftUI
import OmegaCore

struct OverviewView: View {
    var appState: AppState

    private var activeTasks: [OmegaTask] {
        appState.tasks
            .filter { $0.status == .running || $0.status == .waitingInput }
            .sorted { $0.updatedAt > $1.updatedAt }
    }

    private var recentPRs: [(task: OmegaTask, pr: PullRequest)] {
        appState.tasks
            .compactMap { task in task.pr.map { (task: task, pr: $0) } }
            .sorted { $0.task.updatedAt > $1.task.updatedAt }
    }

    private var todaysCost: Double {
        let cutoff = Date().timeIntervalSince1970 - 86400
        return appState.tasks
            .filter { ($0.lastActivityAt ?? $0.updatedAt) >= cutoff }
            .compactMap(\.costUsd)
            .reduce(0, +)
    }

    private let columns = [GridItem(.adaptive(minimum: 260), spacing: 16)]

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 28) {
                header

                if activeTasks.isEmpty {
                    emptyState
                } else {
                    LazyVGrid(columns: columns, spacing: 16) {
                        ForEach(activeTasks) { task in
                            OverviewTaskCard(task: task) {
                                appState.selection = .task(task.id)
                            }
                        }
                    }
                }

                if !appState.terminals.isEmpty {
                    terminalsSection
                }

                if !recentPRs.isEmpty {
                    prsSection
                }

                if !appState.connections.isEmpty {
                    connectionsSection
                }
            }
            .padding(24)
        }
        .navigationTitle("Overview")
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Overview")
                .font(.largeTitle.bold())
            Text("Today's cost: \(formattedCost(todaysCost))")
                .font(.subheadline)
                .foregroundStyle(.secondary)
        }
    }

    private var emptyState: some View {
        VStack(spacing: 12) {
            Image(systemName: "checkmark.circle")
                .font(.system(size: 36))
                .foregroundStyle(.secondary)
            Text("Nothing running right now")
                .font(.title3)
                .foregroundStyle(.secondary)
            Button {
                NotificationCenter.default.post(name: .omegaShowNewTaskSheet, object: nil)
            } label: {
                Label("New Task", systemImage: "plus")
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 60)
    }

    private var terminalsSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Terminals").font(.headline)
            VStack(spacing: 0) {
                ForEach(Array(appState.terminals.enumerated()), id: \.offset) { index, terminal in
                    TerminalRow(terminal: terminal, taskName: taskName(for: terminal))
                    if index < appState.terminals.count - 1 {
                        Divider()
                    }
                }
            }
            .padding(12)
            .background(Color.secondary.opacity(0.06), in: RoundedRectangle(cornerRadius: 10))
        }
    }

    private var prsSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Recent PRs").font(.headline)
            VStack(spacing: 0) {
                ForEach(Array(recentPRs.enumerated()), id: \.offset) { index, entry in
                    HStack(spacing: 10) {
                        CIStatusGlyph(status: entry.pr.checks)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(entry.pr.title).lineLimit(1)
                            Text("#\(entry.pr.number) · \(entry.pr.state) · \(entry.task.repoName)")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                        Spacer()
                    }
                    .padding(.vertical, 6)
                    if index < recentPRs.count - 1 {
                        Divider()
                    }
                }
            }
            .padding(12)
            .background(Color.secondary.opacity(0.06), in: RoundedRectangle(cornerRadius: 10))
        }
    }

    private var connectionsSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Connections").font(.headline)
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 8) {
                    ForEach(appState.connections) { connection in
                        ConnectionPill(connection: connection)
                    }
                }
            }
        }
    }

    private func taskName(for terminal: Terminal) -> String {
        guard let taskId = terminal.taskId,
              let task = appState.tasks.first(where: { $0.id == taskId }) else {
            return "Standalone"
        }
        return task.title
    }

    private func formattedCost(_ value: Double) -> String {
        String(format: "$%.2f", value)
    }
}
