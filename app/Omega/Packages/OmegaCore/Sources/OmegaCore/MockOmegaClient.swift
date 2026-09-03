import Foundation

/// An in-memory `OmegaClientProtocol` for previews and tests that never talks to a real daemon.
public final class MockOmegaClient: OmegaClientProtocol, @unchecked Sendable {
    public var tasks: [OmegaTask]
    public var terminals: [Terminal]
    public var connectionsList: [Connection]
    public var modelsList: [ModelInfo]
    public var gitStates: [String: GitRepoState] = [:]
    public var scriptedHistory: [String: [Event]] = [:]

    private var taskContinuations: [String: AsyncThrowingStream<Event, Error>.Continuation] = [:]
    private var overviewContinuation: AsyncThrowingStream<OverviewMessage, Error>.Continuation?

    public init(
        tasks: [OmegaTask] = [], terminals: [Terminal] = [],
        connections: [Connection] = [], models: [ModelInfo] = []
    ) {
        self.tasks = tasks
        self.terminals = terminals
        self.connectionsList = connections
        self.modelsList = models
    }

    public func health() async throws -> Bool { true }

    public func listTasks() async throws -> [OmegaTask] { tasks }

    public func createTask(repoPath: String, prompt: String, model: String?, mode: TaskMode?, useWorktree: Bool) async throws -> OmegaTask {
        let task = OmegaTask(
            id: UUID().uuidString, title: prompt, repoPath: repoPath,
            repoName: (repoPath as NSString).lastPathComponent, branch: "omega/new-task",
            status: .running, model: model ?? "opus", mode: mode ?? .build,
            createdAt: Date().timeIntervalSince1970, updatedAt: Date().timeIntervalSince1970
        )
        tasks.append(task)
        return task
    }

    public func task(id: String) async throws -> OmegaTask {
        guard let task = tasks.first(where: { $0.id == id }) else { throw OmegaAPIError.httpStatus(404, "not found") }
        return task
    }

    public func taskHistory(id: String) async throws -> [Event] { scriptedHistory[id] ?? [] }

    public func sendPrompt(taskId: String, text: String, mode: TaskMode?) async throws {}
    public func cancelTask(taskId: String) async throws {}
    public func answer(taskId: String, requestId: String, values: [String]) async throws {}
    public func confirm(taskId: String, requestId: String, approved: Bool, always: Bool) async throws {}
    public func setModel(taskId: String, model: String) async throws {}
    public func setMode(taskId: String, mode: TaskMode) async throws {}
    public func undo(taskId: String) async throws {}

    public func gitState(taskId: String) async throws -> GitRepoState {
        gitStates[taskId] ?? GitRepoState(path: "/tmp", name: "repo", branch: "main", dirty: false, changes: [])
    }

    public func diff(taskId: String, path: String) async throws -> DiffResponse {
        DiffResponse(path: path, diff: "")
    }

    public func createPR(taskId: String, title: String?, body: String?) async throws -> PullRequest {
        PullRequest(number: 1, title: title ?? "PR", url: "https://github.com/example/repo/pull/1", state: "open", checks: .pending)
    }

    public func artifacts(taskId: String) async throws -> [Artifact] { [] }
    public func artifact(taskId: String, artifactId: String) async throws -> Artifact {
        Artifact(id: artifactId, title: "artifact", kind: "text", createdAt: Date().timeIntervalSince1970, content: nil)
    }
    public func jobs(taskId: String) async throws -> [BackgroundJob] { [] }

    public func models() async throws -> [ModelInfo] { modelsList }
    public func connections() async throws -> [Connection] { connectionsList }
    public func connect(name: String) async throws {}

    public func listTerminals() async throws -> [Terminal] { terminals }
    public func createTerminal(taskId: String?, cwd: String) async throws -> Terminal {
        let terminal = Terminal(id: UUID().uuidString, taskId: taskId, cwd: cwd, createdAt: Date().timeIntervalSince1970)
        terminals.append(terminal)
        return terminal
    }
    public func closeTerminal(id: String) async throws {
        terminals.removeAll { $0.id == id }
    }

    public func taskEventStream(id: String) -> AsyncThrowingStream<Event, Error> {
        AsyncThrowingStream { continuation in
            taskContinuations[id] = continuation
        }
    }

    public func overviewStream() -> AsyncThrowingStream<OverviewMessage, Error> {
        AsyncThrowingStream { continuation in
            overviewContinuation = continuation
        }
    }

    public func terminalStream(id: String, incoming: @escaping @Sendable (TerminalFrame) -> Void) -> TerminalSocket {
        let task = URLSession.shared.webSocketTask(with: URL(string: "ws://127.0.0.1:1/mock")!)
        return TerminalSocket(task: task, incoming: incoming)
    }

    /// Test/preview helper: push an event into a task's live stream as if the daemon sent it.
    public func emit(_ event: Event, toTask id: String) {
        taskContinuations[id]?.yield(event)
    }

    public func emitOverview(_ message: OverviewMessage) {
        overviewContinuation?.yield(message)
    }
}
