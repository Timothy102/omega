import Foundation
import OmegaCore

enum SidebarSelection: Hashable {
    case overview
    case task(String)
}

@MainActor
@Observable
final class AppState {
    var client: OmegaClientProtocol?
    var daemonStatus: DaemonStatus = .checking
    var tasks: [OmegaTask] = []
    var terminals: [Terminal] = []
    var connections: [Connection] = []
    var availableModels: [ModelInfo] = []
    var selection: SidebarSelection = .overview
    var searchText: String = ""

    let settings = AppSettings()
    let notificationService = NotificationService()

    private let launcher = DaemonLauncher()
    private var overviewStreamTask: Task<Void, Never>?
    private var taskViewModels: [String: TaskViewModel] = [:]

    func start() async {
        notificationService.isEnabled = settings.notificationsEnabled
        notificationService.onFocusTask = { [weak self] id in self?.selection = .task(id) }
        notificationService.requestAuthorization()

        let (status, client) = await launcher.launch(port: settings.daemonPort)
        daemonStatus = status
        self.client = client
        guard client != nil else { return }
        await refreshAll()
        observeOverview()
    }

    func retryConnection() async {
        daemonStatus = .checking
        await start()
    }

    func refreshAll() async {
        guard let client else { return }
        async let fetchedTasks = (try? client.listTasks()) ?? []
        async let fetchedTerminals = (try? client.listTerminals()) ?? []
        async let fetchedConnections = (try? client.connections()) ?? []
        async let fetchedModels = (try? client.models()) ?? []
        tasks = await fetchedTasks
        terminals = await fetchedTerminals
        connections = await fetchedConnections
        availableModels = await fetchedModels
        notificationService.observe(tasks: tasks)
    }

    private func observeOverview() {
        guard let client else { return }
        overviewStreamTask?.cancel()
        overviewStreamTask = Task { [weak self, client] in
            do {
                for try await message in client.overviewStream() {
                    guard let self else { return }
                    switch message {
                    case .tasks(let updated):
                        self.tasks = updated
                        self.notificationService.observe(tasks: updated)
                    case .terminals(let updated):
                        self.terminals = updated
                    case .unknown:
                        break
                    }
                }
            } catch {
                // Reconnect handled inside OmegaClient's stream backoff; nothing to do here.
            }
        }
    }

    func createTask(repoPath: String, prompt: String, model: String?, mode: TaskMode, useWorktree: Bool) async {
        guard let client else { return }
        settings.noteRecentRepo(repoPath)
        if let task = try? await client.createTask(
            repoPath: repoPath, prompt: prompt, model: model, mode: mode, useWorktree: useWorktree
        ) {
            tasks.append(task)
            selection = .task(task.id)
        }
    }

    func viewModel(for taskId: String) -> TaskViewModel {
        if let existing = taskViewModels[taskId] { return existing }
        guard let client else {
            let placeholder = TaskViewModel(taskId: taskId, client: MockOmegaClient())
            taskViewModels[taskId] = placeholder
            return placeholder
        }
        let vm = TaskViewModel(taskId: taskId, client: client)
        taskViewModels[taskId] = vm
        Task { await vm.start() }
        return vm
    }

    var filteredTasks: [OmegaTask] {
        guard !searchText.isEmpty else { return tasks }
        return tasks.filter {
            $0.title.localizedCaseInsensitiveContains(searchText)
                || $0.repoName.localizedCaseInsensitiveContains(searchText)
                || $0.branch.localizedCaseInsensitiveContains(searchText)
        }
    }

    var tasksByRepo: [(repo: String, branch: String, tasks: [OmegaTask])] {
        let grouped = Dictionary(grouping: filteredTasks) { $0.repoName }
        return grouped.keys.sorted().map { repo in
            let repoTasks = grouped[repo]!.sorted { $0.updatedAt > $1.updatedAt }
            let branch = repoTasks.first?.branch ?? ""
            return (repo, branch, repoTasks)
        }
    }
}
