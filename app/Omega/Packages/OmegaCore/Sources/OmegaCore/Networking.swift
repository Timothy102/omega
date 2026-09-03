import Foundation

public enum OmegaAPIError: Error, Sendable, Equatable, LocalizedError {
    case invalidResponse
    case httpStatus(Int, String)
    case decoding(String)
    case notConnected
    case cancelled

    public var errorDescription: String? {
        switch self {
        case .invalidResponse: return "The daemon returned an invalid response."
        case .httpStatus(let code, let body): return "Daemon returned HTTP \(code): \(body)"
        case .decoding(let detail): return "Failed to decode daemon response: \(detail)"
        case .notConnected: return "Not connected to the omega daemon."
        case .cancelled: return "Cancelled."
        }
    }
}

/// Message shape on `/ws/overview` — unspecified by the daemon contract; this client's choice.
public enum OverviewMessage: Sendable, Equatable {
    case tasks([OmegaTask])
    case terminals([Terminal])
    case unknown(type: String)
}

extension OverviewMessage: Decodable {
    private enum CodingKeys: String, CodingKey { case type, tasks, terminals }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        let type = try c.decode(String.self, forKey: .type)
        switch type {
        case "tasks": self = .tasks(try c.decode([OmegaTask].self, forKey: .tasks))
        case "terminals": self = .terminals(try c.decode([Terminal].self, forKey: .terminals))
        default: self = .unknown(type: type)
        }
    }
}

/// A control/data frame on `/ws/terminals/{id}`. Binary frames carry raw PTY bytes both directions;
/// a resize is sent as a text JSON frame `{"resize":[cols,rows]}` — unspecified by the daemon contract.
public enum TerminalFrame: Sendable, Equatable {
    case data(Data)
    case resize(cols: Int, rows: Int)
}

public protocol OmegaClientProtocol: Sendable {
    func health() async throws -> Bool

    func listTasks() async throws -> [OmegaTask]
    func createTask(repoPath: String, prompt: String, model: String?, mode: TaskMode?, useWorktree: Bool) async throws -> OmegaTask
    func task(id: String) async throws -> OmegaTask
    func taskHistory(id: String) async throws -> [Event]

    func sendPrompt(taskId: String, text: String, mode: TaskMode?) async throws
    func cancelTask(taskId: String) async throws
    func answer(taskId: String, requestId: String, values: [String]) async throws
    func confirm(taskId: String, requestId: String, approved: Bool, always: Bool) async throws
    func setModel(taskId: String, model: String) async throws
    func setMode(taskId: String, mode: TaskMode) async throws
    func undo(taskId: String) async throws

    func gitState(taskId: String) async throws -> GitRepoState
    func diff(taskId: String, path: String) async throws -> DiffResponse
    func createPR(taskId: String, title: String?, body: String?) async throws -> PullRequest

    func artifacts(taskId: String) async throws -> [Artifact]
    func artifact(taskId: String, artifactId: String) async throws -> Artifact
    func jobs(taskId: String) async throws -> [BackgroundJob]

    func models() async throws -> [ModelInfo]
    func connections() async throws -> [Connection]
    func connect(name: String) async throws

    func listTerminals() async throws -> [Terminal]
    func createTerminal(taskId: String?, cwd: String) async throws -> Terminal
    func closeTerminal(id: String) async throws

    func taskEventStream(id: String) -> AsyncThrowingStream<Event, Error>
    func overviewStream() -> AsyncThrowingStream<OverviewMessage, Error>
    func terminalStream(id: String, incoming: @escaping @Sendable (TerminalFrame) -> Void) -> TerminalSocket
}

extension OmegaClientProtocol {
    /// Convenience for callers that don't need to set "always allow" on a permission confirmation.
    public func confirm(taskId: String, requestId: String, approved: Bool) async throws {
        try await confirm(taskId: taskId, requestId: requestId, approved: approved, always: false)
    }
}
