import Foundation

public enum TaskStatus: String, Codable, Sendable, Equatable, CaseIterable {
    case running
    case waitingInput = "waiting_input"
    case idle
    case done
    case failed
}

public enum TaskMode: String, Codable, Sendable, Equatable, CaseIterable {
    case build, plan, discuss
}

public enum CIStatus: String, Codable, Sendable, Equatable {
    case passing, failing, pending, none
}

public struct PullRequest: Codable, Sendable, Equatable, Identifiable {
    public let number: Int
    public let title: String
    public let url: String
    public let state: String
    public let checks: CIStatus

    public var id: Int { number }

    public init(number: Int, title: String, url: String, state: String, checks: CIStatus) {
        self.number = number
        self.title = title
        self.url = url
        self.state = state
        self.checks = checks
    }
}

public struct OmegaTask: Codable, Sendable, Equatable, Identifiable {
    public let id: String
    public var title: String
    public var repoPath: String
    public var repoName: String
    public var branch: String
    public var status: TaskStatus
    public var model: String
    public var mode: TaskMode
    public var worktreePath: String?
    public var pr: PullRequest?
    public var tokensUsed: Int?
    public var costUsd: Double?
    public var createdAt: Double
    public var updatedAt: Double
    public var lastActivityAt: Double?

    private enum CodingKeys: String, CodingKey {
        case id, title, repo, cwd, worktree, branch, status, model, mode, pr
        case repoPath = "repo_path"
        case repoName = "repo_name"
        case worktreePath = "worktree_path"
        case tokensUsed = "tokens_used"
        case tokensIn = "tokens_in"
        case tokensOut = "tokens_out"
        case costUsd = "cost_usd"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
        case lastActivityAt = "last_activity_at"
        case created, updated
    }

    public init(
        id: String, title: String, repoPath: String, repoName: String, branch: String,
        status: TaskStatus, model: String, mode: TaskMode, worktreePath: String? = nil,
        pr: PullRequest? = nil, tokensUsed: Int? = nil, costUsd: Double? = nil,
        createdAt: Double, updatedAt: Double, lastActivityAt: Double? = nil
    ) {
        self.id = id
        self.title = title
        self.repoPath = repoPath
        self.repoName = repoName
        self.branch = branch
        self.status = status
        self.model = model
        self.mode = mode
        self.worktreePath = worktreePath
        self.pr = pr
        self.tokensUsed = tokensUsed
        self.costUsd = costUsd
        self.createdAt = createdAt
        self.updatedAt = updatedAt
        self.lastActivityAt = lastActivityAt
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        title = try c.decodeIfPresent(String.self, forKey: .title) ?? ""
        let repo = try c.decodeIfPresent(String.self, forKey: .repo)
        let repoPathKey = try c.decodeIfPresent(String.self, forKey: .repoPath)
        let cwd = try c.decodeIfPresent(String.self, forKey: .cwd)
        repoPath = repoPathKey ?? repo ?? cwd ?? ""
        repoName = try c.decodeIfPresent(String.self, forKey: .repoName)
            ?? URL(fileURLWithPath: repoPath).lastPathComponent
        branch = try c.decodeIfPresent(String.self, forKey: .branch) ?? ""
        status = try c.decodeIfPresent(TaskStatus.self, forKey: .status) ?? .idle
        model = try c.decodeIfPresent(String.self, forKey: .model) ?? "opus"
        mode = try c.decodeIfPresent(TaskMode.self, forKey: .mode) ?? .build
        let explicitWorktree = try c.decodeIfPresent(String.self, forKey: .worktreePath)
        let usesWorktree = try c.decodeIfPresent(Bool.self, forKey: .worktree) ?? false
        if let explicitWorktree, !explicitWorktree.isEmpty {
            worktreePath = explicitWorktree
        } else if usesWorktree, let cwd, !cwd.isEmpty {
            worktreePath = cwd
        } else if let cwd, !cwd.isEmpty, cwd != repoPath {
            worktreePath = cwd
        } else {
            worktreePath = nil
        }
        pr = try c.decodeIfPresent(PullRequest.self, forKey: .pr)
        if let used = try c.decodeIfPresent(Int.self, forKey: .tokensUsed) {
            tokensUsed = used
        } else {
            let tin = try c.decodeIfPresent(Int.self, forKey: .tokensIn) ?? 0
            let tout = try c.decodeIfPresent(Int.self, forKey: .tokensOut) ?? 0
            tokensUsed = (tin + tout) > 0 ? tin + tout : nil
        }
        costUsd = try c.decodeIfPresent(Double.self, forKey: .costUsd)
        createdAt = try c.decodeIfPresent(Double.self, forKey: .createdAt)
            ?? c.decodeIfPresent(Double.self, forKey: .created)
            ?? 0
        updatedAt = try c.decodeIfPresent(Double.self, forKey: .updatedAt)
            ?? c.decodeIfPresent(Double.self, forKey: .updated)
            ?? createdAt
        lastActivityAt = try c.decodeIfPresent(Double.self, forKey: .lastActivityAt)
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(id, forKey: .id)
        try c.encode(title, forKey: .title)
        try c.encode(repoPath, forKey: .repoPath)
        try c.encode(repoName, forKey: .repoName)
        try c.encode(branch, forKey: .branch)
        try c.encode(status, forKey: .status)
        try c.encode(model, forKey: .model)
        try c.encode(mode, forKey: .mode)
        try c.encodeIfPresent(worktreePath, forKey: .worktreePath)
        try c.encodeIfPresent(pr, forKey: .pr)
        try c.encodeIfPresent(tokensUsed, forKey: .tokensUsed)
        try c.encodeIfPresent(costUsd, forKey: .costUsd)
        try c.encode(createdAt, forKey: .createdAt)
        try c.encode(updatedAt, forKey: .updatedAt)
        try c.encodeIfPresent(lastActivityAt, forKey: .lastActivityAt)
    }

    public var elapsedSeconds: Double {
        Date().timeIntervalSince1970 - createdAt
    }

    /// Directory the agent actually works in — worktree when present, else the repo.
    public var workspaceRoot: String { worktreePath ?? repoPath }
}

/// Confirmed daemon shape for `POST /api/terminals` is `{id, pid, cwd, created}`; `task_id`,
/// `title`, and `last_line` are this client's own additions for the sidebar/overview display and
/// may not be present on every response — all decode as optional.
public struct Terminal: Codable, Sendable, Equatable, Identifiable {
    public let id: String
    public var pid: Int?
    public var taskId: String?
    public var cwd: String
    public var title: String?
    public var createdAt: Double?
    public var lastLine: String?

    private enum CodingKeys: String, CodingKey {
        case id, pid, taskId = "task_id", cwd, title, createdAt = "created", lastLine = "last_line"
    }

    public init(
        id: String, pid: Int? = nil, taskId: String? = nil, cwd: String, title: String? = nil,
        createdAt: Double? = nil, lastLine: String? = nil
    ) {
        self.id = id
        self.pid = pid
        self.taskId = taskId
        self.cwd = cwd
        self.title = title
        self.createdAt = createdAt
        self.lastLine = lastLine
    }
}

/// `GET /api/tasks/{id}/jobs` — the agent's background bash jobs. Field names beyond `id` aren't
/// pinned down by the daemon contract; this mirrors `JobStarted`/`JobFinished` event fields.
public struct BackgroundJob: Codable, Sendable, Equatable, Identifiable {
    public let id: String
    public var command: String
    public var status: String
    public var exitCode: Int?

    private enum CodingKeys: String, CodingKey {
        case id, command, status, exitCode = "exit_code"
    }

    public init(id: String, command: String, status: String, exitCode: Int? = nil) {
        self.id = id
        self.command = command
        self.status = status
        self.exitCode = exitCode
    }
}

public enum ConnectionState: String, Codable, Sendable, Equatable {
    case connected, configured, needsAuth = "needs_auth", error, disabled
}

public struct Connection: Codable, Sendable, Equatable, Identifiable {
    public let name: String
    public var state: ConnectionState
    public var tools: Int
    public var error: String?
    public var lastUsed: Double?

    public var id: String { name }

    public init(name: String, state: ConnectionState, tools: Int, error: String? = nil, lastUsed: Double? = nil) {
        self.name = name
        self.state = state
        self.tools = tools
        self.error = error
        self.lastUsed = lastUsed
    }
}

public struct ModelInfo: Codable, Sendable, Equatable, Identifiable {
    public let alias: String
    public var model: String
    public var provider: String
    public var context: Int
    public var effort: String?
    public var fallback: String?

    public var id: String { alias }

    public init(alias: String, model: String, provider: String, context: Int, effort: String? = nil, fallback: String? = nil) {
        self.alias = alias
        self.model = model
        self.provider = provider
        self.context = context
        self.effort = effort
        self.fallback = fallback
    }
}

public enum GitChangeStatus: String, Codable, Sendable, Equatable {
    case modified = "M", added = "A", deleted = "D", renamed = "R", untracked = "?"
}

public struct GitChange: Codable, Sendable, Equatable, Identifiable {
    public let path: String
    public var status: GitChangeStatus
    public var added: Int
    public var removed: Int
    public var touchedThisSession: Bool

    public var id: String { path }

    private enum CodingKeys: String, CodingKey {
        case path, status, added, removed, touchedThisSession = "touched_this_session"
    }

    public init(path: String, status: GitChangeStatus, added: Int, removed: Int, touchedThisSession: Bool = false) {
        self.path = path
        self.status = status
        self.added = added
        self.removed = removed
        self.touchedThisSession = touchedThisSession
    }
}

public struct GitRepoState: Codable, Sendable, Equatable {
    public var path: String
    public var name: String
    public var branch: String
    public var dirty: Bool
    public var changes: [GitChange]

    public init(path: String, name: String, branch: String, dirty: Bool, changes: [GitChange]) {
        self.path = path
        self.name = name
        self.branch = branch
        self.dirty = dirty
        self.changes = changes
    }
}

public struct GitCommit: Codable, Sendable, Equatable, Identifiable {
    public let sha: String
    public var shortSha: String
    public var author: String
    public var age: String
    public var subject: String

    public var id: String { sha }

    private enum CodingKeys: String, CodingKey {
        case sha, shortSha = "short_sha", author, age, subject
    }
}

public struct DiffResponse: Codable, Sendable, Equatable {
    public let path: String
    public let diff: String
}

public struct Artifact: Codable, Sendable, Equatable, Identifiable {
    public let id: String
    public var title: String
    public var kind: String
    public var createdAt: Double
    public var content: String?

    private enum CodingKeys: String, CodingKey {
        case id, title, kind, createdAt = "created_at", content
    }
}
