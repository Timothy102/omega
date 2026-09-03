import Foundation

/// REST + WebSocket client for the `omega serve` daemon. All state is immutable — safe to share.
public final class OmegaClient: OmegaClientProtocol, Sendable {
    private let baseURL: URL
    private let token: String
    private let session: URLSession
    private let decoder: JSONDecoder
    private let encoder: JSONEncoder

    public init(config: ServeConfig, session: URLSession = .shared) {
        self.baseURL = config.baseURL
        self.token = config.token
        self.session = session
        let decoder = JSONDecoder()
        self.decoder = decoder
        let encoder = JSONEncoder()
        self.encoder = encoder
    }

    // MARK: - REST plumbing

    private func makeRequest(path: String, method: String, query: [String: String] = [:], jsonBody: Data? = nil) throws -> URLRequest {
        var components = URLComponents(url: baseURL.appendingPathComponent(path), resolvingAgainstBaseURL: false)!
        if !query.isEmpty {
            components.queryItems = query.map { URLQueryItem(name: $0.key, value: $0.value) }
        }
        guard let url = components.url else { throw OmegaAPIError.invalidResponse }
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        if let jsonBody {
            request.httpBody = jsonBody
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        return request
    }

    private func send(path: String, method: String, query: [String: String] = [:], jsonBody: Data? = nil) async throws -> Data {
        let request = try makeRequest(path: path, method: method, query: query, jsonBody: jsonBody)
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else { throw OmegaAPIError.invalidResponse }
        guard (200..<300).contains(http.statusCode) else {
            throw OmegaAPIError.httpStatus(http.statusCode, String(data: data, encoding: .utf8) ?? "")
        }
        return data
    }

    private func get<T: Decodable>(_ path: String, query: [String: String] = [:]) async throws -> T {
        let data = try await send(path: path, method: "GET", query: query)
        return try decode(T.self, from: data)
    }

    private func post<Body: Encodable, T: Decodable>(_ path: String, body: Body) async throws -> T {
        let payload = try encoder.encode(body)
        let data = try await send(path: path, method: "POST", jsonBody: payload)
        return try decode(T.self, from: data)
    }

    private func postVoid(_ path: String) async throws {
        _ = try await send(path: path, method: "POST")
    }

    private func postVoid<Body: Encodable>(_ path: String, body: Body) async throws {
        let payload = try encoder.encode(body)
        _ = try await send(path: path, method: "POST", jsonBody: payload)
    }

    private func decode<T: Decodable>(_ type: T.Type, from data: Data) throws -> T {
        do {
            return try decoder.decode(T.self, from: data)
        } catch {
            throw OmegaAPIError.decoding(String(describing: error))
        }
    }

    // MARK: - Health / Tasks

    public func health() async throws -> Bool {
        struct Health: Decodable { let status: String }
        let health: Health = try await get("/api/health")
        return health.status == "ok"
    }

    public func listTasks() async throws -> [OmegaTask] {
        struct Response: Decodable { let tasks: [OmegaTask] }
        let response: Response = try await get("/api/tasks")
        return response.tasks
    }

    public func createTask(repoPath: String, prompt: String, model: String?, mode: TaskMode?, useWorktree: Bool) async throws -> OmegaTask {
        struct Body: Encodable {
            let repo: String
            let prompt: String
            let model: String?
            let mode: TaskMode?
            let worktree: Bool
        }
        return try await post("/api/tasks", body: Body(repo: repoPath, prompt: prompt, model: model, mode: mode, worktree: useWorktree))
    }

    public func task(id: String) async throws -> OmegaTask {
        try await get("/api/tasks/\(id)")
    }

    /// Replays a task's history from `/trace`, which returns NDJSON (one `Event` per line) inside
    /// a JSON string, not a JSON array — the daemon has no separate `/events` endpoint.
    public func taskHistory(id: String) async throws -> [Event] {
        struct Response: Decodable { let trace: String }
        let response: Response = try await get("/api/tasks/\(id)/trace")
        return response.trace
            .split(separator: "\n", omittingEmptySubsequences: true)
            .compactMap { line -> Event? in
                guard let data = line.data(using: .utf8) else { return nil }
                return try? decoder.decode(Event.self, from: data)
            }
    }

    // MARK: - Task control

    public func sendPrompt(taskId: String, text: String, mode: TaskMode?) async throws {
        struct Body: Encodable { let text: String; let mode: TaskMode? }
        try await postVoid("/api/tasks/\(taskId)/prompt", body: Body(text: text, mode: mode))
    }

    public func cancelTask(taskId: String) async throws {
        try await postVoid("/api/tasks/\(taskId)/cancel")
    }

    /// The daemon takes one `answer` string (selected label(s) joined by `", "`, or free text) —
    /// not a list — so multi-select choices are joined before sending.
    public func answer(taskId: String, requestId: String, values: [String]) async throws {
        struct Body: Encodable { let requestId: String; let answer: String
            enum CodingKeys: String, CodingKey { case requestId = "request_id", answer }
        }
        try await postVoid("/api/tasks/\(taskId)/answer", body: Body(requestId: requestId, answer: values.joined(separator: ", ")))
    }

    public func confirm(taskId: String, requestId: String, approved: Bool, always: Bool) async throws {
        struct Body: Encodable { let requestId: String; let allow: Bool; let always: Bool
            enum CodingKeys: String, CodingKey { case requestId = "request_id", allow, always }
        }
        try await postVoid("/api/tasks/\(taskId)/confirm", body: Body(requestId: requestId, allow: approved, always: always))
    }

    public func setModel(taskId: String, model: String) async throws {
        struct Body: Encodable { let model: String }
        try await postVoid("/api/tasks/\(taskId)/model", body: Body(model: model))
    }

    public func setMode(taskId: String, mode: TaskMode) async throws {
        struct Body: Encodable { let mode: TaskMode }
        try await postVoid("/api/tasks/\(taskId)/mode", body: Body(mode: mode))
    }

    public func undo(taskId: String) async throws {
        try await postVoid("/api/tasks/\(taskId)/undo")
    }

    // MARK: - Git / PR

    public func gitState(taskId: String) async throws -> GitRepoState {
        try await get("/api/tasks/\(taskId)/git")
    }

    public func diff(taskId: String, path: String) async throws -> DiffResponse {
        try await get("/api/tasks/\(taskId)/diff", query: ["path": path])
    }

    public func createPR(taskId: String, title: String?, body: String?) async throws -> PullRequest {
        struct Body: Encodable { let title: String?; let body: String? }
        return try await post("/api/tasks/\(taskId)/pr", body: Body(title: title, body: body))
    }

    // MARK: - Artifacts

    public func artifacts(taskId: String) async throws -> [Artifact] {
        struct Response: Decodable { let artifacts: [Artifact] }
        let response: Response = try await get("/api/tasks/\(taskId)/artifacts")
        return response.artifacts
    }

    public func artifact(taskId: String, artifactId: String) async throws -> Artifact {
        try await get("/api/tasks/\(taskId)/artifacts/\(artifactId)")
    }

    public func jobs(taskId: String) async throws -> [BackgroundJob] {
        struct Response: Decodable { let jobs: [BackgroundJob] }
        let response: Response = try await get("/api/tasks/\(taskId)/jobs")
        return response.jobs
    }

    // MARK: - Models / Connections

    public func models() async throws -> [ModelInfo] {
        struct Response: Decodable { let models: [ModelInfo] }
        let response: Response = try await get("/api/models")
        return response.models
    }

    public func connections() async throws -> [Connection] {
        struct Response: Decodable { let connections: [Connection] }
        let response: Response = try await get("/api/connections")
        return response.connections
    }

    public func connect(name: String) async throws {
        try await postVoid("/api/connections/\(name)/connect")
    }

    // MARK: - Terminals

    public func listTerminals() async throws -> [Terminal] {
        struct Response: Decodable { let terminals: [Terminal] }
        let response: Response = try await get("/api/terminals")
        return response.terminals
    }

    public func createTerminal(taskId: String?, cwd: String) async throws -> Terminal {
        struct Body: Encodable { let taskId: String?; let cwd: String
            enum CodingKeys: String, CodingKey { case taskId = "task_id", cwd }
        }
        return try await post("/api/terminals", body: Body(taskId: taskId, cwd: cwd))
    }

    public func closeTerminal(id: String) async throws {
        _ = try await send(path: "/api/terminals/\(id)", method: "DELETE")
    }

    // MARK: - WebSocket streams

    private func webSocketURL(path: String) -> URL {
        var components = URLComponents(url: baseURL.appendingPathComponent(path), resolvingAgainstBaseURL: false)!
        components.scheme = baseURL.scheme == "https" ? "wss" : "ws"
        components.queryItems = [URLQueryItem(name: "token", value: token)]
        return components.url!
    }

    /// Reconnects with exponential backoff (0.5s, 1s, 2s, 4s, capped at 10s) until the stream is torn down.
    public func taskEventStream(id: String) -> AsyncThrowingStream<Event, Error> {
        reconnectingStream(path: "/ws/tasks/\(id)") { [decoder] data in
            try decoder.decode(Event.self, from: data)
        }
    }

    public func overviewStream() -> AsyncThrowingStream<OverviewMessage, Error> {
        reconnectingStream(path: "/ws/overview") { [decoder] data in
            try decoder.decode(OverviewMessage.self, from: data)
        }
    }

    private func reconnectingStream<T: Sendable>(
        path: String,
        decode: @escaping @Sendable (Data) throws -> T
    ) -> AsyncThrowingStream<T, Error> {
        let url = webSocketURL(path: path)
        let session = self.session
        return AsyncThrowingStream { continuation in
            let task = Task {
                var backoff: UInt64 = 500_000_000
                while !Task.isCancelled {
                    let socket = session.webSocketTask(with: url)
                    socket.resume()
                    do {
                        while !Task.isCancelled {
                            let message = try await socket.receive()
                            switch message {
                            case .data(let data):
                                continuation.yield(try decode(data))
                            case .string(let text):
                                if let data = text.data(using: .utf8) {
                                    continuation.yield(try decode(data))
                                }
                            @unknown default:
                                break
                            }
                        }
                        backoff = 500_000_000
                    } catch {
                        if Task.isCancelled { break }
                    }
                    socket.cancel(with: .goingAway, reason: nil)
                    if Task.isCancelled { break }
                    try? await Task.sleep(nanoseconds: backoff)
                    backoff = min(backoff * 2, 10_000_000_000)
                }
                continuation.finish()
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    public func terminalStream(id: String, incoming: @escaping @Sendable (TerminalFrame) -> Void) -> TerminalSocket {
        let url = webSocketURL(path: "/ws/terminals/\(id)")
        let task = session.webSocketTask(with: url)
        return TerminalSocket(task: task, incoming: incoming)
    }
}
