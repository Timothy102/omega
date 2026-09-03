import Foundation

/// One message from `/ws/tasks/{id}`. The daemon sends `{"type": "<EventClassName>", ...fields, "t": <unix seconds>, "turn": <n>}`.
/// The two synthetic request messages (`ask_user_request`, `confirm_request`) carry `request_id` but no `t`/`turn`.
public struct Event: Decodable, Sendable {
    public let type: String
    public let t: Double?
    public let turn: Int?
    public let payload: EventPayload

    private enum CodingKeys: String, CodingKey { case type, t, turn }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        type = try container.decode(String.self, forKey: .type)
        t = try container.decodeIfPresent(Double.self, forKey: .t)
        turn = try container.decodeIfPresent(Int.self, forKey: .turn)
        payload = try EventPayload(type: type, decoder: decoder)
    }

    public init(type: String, t: Double? = nil, turn: Int? = nil, payload: EventPayload) {
        self.type = type
        self.t = t
        self.turn = turn
        self.payload = payload
    }
}

public enum EventPayload: Sendable, Equatable {
    case textDelta(TextDelta)
    case toolStart(ToolStart)
    case toolEnd(ToolEnd)
    case compacted(Compacted)
    case memoryWrite(MemoryWrite)
    case memoryConsolidated(MemoryConsolidated)
    case subagentSpawned(SubagentSpawned)
    case subagentDone(SubagentDone)
    case error(ErrorEvent)
    case done(DoneEvent)
    case usage(UsageEvent)
    case fallback(Fallback)
    case modelUsed(ModelUsed)
    case phase(PhaseEvent)
    case checkpoint(CheckpointEvent)
    case verified(Verified)
    case jobStarted(JobStarted)
    case jobFinished(JobFinished)
    case retryBlocked(RetryBlocked)
    case askUserRequest(AskUserRequest)
    case confirmRequest(ConfirmRequest)
    case unknown(type: String)

    init(type: String, decoder: Decoder) throws {
        switch type {
        case "TextDelta": self = .textDelta(try TextDelta(from: decoder))
        case "ToolStart": self = .toolStart(try ToolStart(from: decoder))
        case "ToolEnd": self = .toolEnd(try ToolEnd(from: decoder))
        case "Compacted": self = .compacted(try Compacted(from: decoder))
        case "MemoryWrite": self = .memoryWrite(try MemoryWrite(from: decoder))
        case "MemoryConsolidated": self = .memoryConsolidated(try MemoryConsolidated(from: decoder))
        case "SubagentSpawned": self = .subagentSpawned(try SubagentSpawned(from: decoder))
        case "SubagentDone": self = .subagentDone(try SubagentDone(from: decoder))
        case "Error": self = .error(try ErrorEvent(from: decoder))
        case "Done": self = .done(try DoneEvent(from: decoder))
        case "Usage": self = .usage(try UsageEvent(from: decoder))
        case "Fallback": self = .fallback(try Fallback(from: decoder))
        case "ModelUsed": self = .modelUsed(try ModelUsed(from: decoder))
        case "Phase": self = .phase(try PhaseEvent(from: decoder))
        case "Checkpoint": self = .checkpoint(try CheckpointEvent(from: decoder))
        case "Verified": self = .verified(try Verified(from: decoder))
        case "JobStarted": self = .jobStarted(try JobStarted(from: decoder))
        case "JobFinished": self = .jobFinished(try JobFinished(from: decoder))
        case "RetryBlocked": self = .retryBlocked(try RetryBlocked(from: decoder))
        case "ask_user_request": self = .askUserRequest(try AskUserRequest(from: decoder))
        case "confirm_request": self = .confirmRequest(try ConfirmRequest(from: decoder))
        default: self = .unknown(type: type)
        }
    }
}

public struct TextDelta: Decodable, Sendable, Equatable {
    public let text: String
}

public struct ToolStart: Decodable, Sendable, Equatable {
    public let callId: String
    public let name: String
    public let argsPreview: String
    public let subagentId: String?
    public let tier: String?

    private enum CodingKeys: String, CodingKey {
        case callId = "call_id", name, argsPreview = "args_preview", subagentId = "subagent_id", tier
    }
}

public struct ToolEnd: Decodable, Sendable, Equatable {
    public let callId: String
    public let name: String
    public let resultPreview: String
    public let durationS: Double
    public let offloaded: Bool
    public let artifactId: String?
    public let resultChars: Int
    public let outcome: String

    private enum CodingKeys: String, CodingKey {
        case callId = "call_id", name, resultPreview = "result_preview", durationS = "duration_s",
             offloaded, artifactId = "artifact_id", resultChars = "result_chars", outcome
    }

    public init(
        callId: String, name: String, resultPreview: String, durationS: Double, offloaded: Bool = false,
        artifactId: String? = nil, resultChars: Int = 0, outcome: String = ""
    ) {
        self.callId = callId
        self.name = name
        self.resultPreview = resultPreview
        self.durationS = durationS
        self.offloaded = offloaded
        self.artifactId = artifactId
        self.resultChars = resultChars
        self.outcome = outcome
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        callId = try c.decode(String.self, forKey: .callId)
        name = try c.decode(String.self, forKey: .name)
        resultPreview = try c.decode(String.self, forKey: .resultPreview)
        durationS = try c.decode(Double.self, forKey: .durationS)
        offloaded = try c.decodeIfPresent(Bool.self, forKey: .offloaded) ?? false
        artifactId = try c.decodeIfPresent(String.self, forKey: .artifactId)
        resultChars = try c.decodeIfPresent(Int.self, forKey: .resultChars) ?? 0
        outcome = try c.decodeIfPresent(String.self, forKey: .outcome) ?? ""
    }
}

public struct Compacted: Decodable, Sendable, Equatable {
    public let note: String
}

public struct MemoryWrite: Decodable, Sendable, Equatable {
    public let nodeId: String
    public let type: String
    public let title: String
    public let scope: String

    private enum CodingKeys: String, CodingKey { case nodeId = "node_id", type, title, scope }
}

public struct MemoryConsolidated: Decodable, Sendable, Equatable {
    public let summary: String
}

public struct SubagentSpawned: Decodable, Sendable, Equatable {
    public let subagentId: String
    public let tier: String
    public let taskPreview: String

    private enum CodingKeys: String, CodingKey { case subagentId = "subagent_id", tier, taskPreview = "task_preview" }
}

public struct SubagentDone: Decodable, Sendable, Equatable {
    public let subagentId: String
    public let summaryPreview: String

    private enum CodingKeys: String, CodingKey { case subagentId = "subagent_id", summaryPreview = "summary_preview" }
}

public struct ErrorEvent: Decodable, Sendable, Equatable {
    public let message: String
}

public struct DoneEvent: Decodable, Sendable, Equatable {
    public let text: String
}

public struct UsageEvent: Decodable, Sendable, Equatable {
    public let promptTokens: Int
    public let completionTokens: Int
    public let used: Int
    public let limit: Int
    public let cacheRead: Int
    public let cacheWrite: Int

    private enum CodingKeys: String, CodingKey {
        case promptTokens = "prompt_tokens", completionTokens = "completion_tokens", used, limit,
             cacheRead = "cache_read", cacheWrite = "cache_write"
    }

    public init(
        promptTokens: Int, completionTokens: Int, used: Int, limit: Int, cacheRead: Int = 0, cacheWrite: Int = 0
    ) {
        self.promptTokens = promptTokens
        self.completionTokens = completionTokens
        self.used = used
        self.limit = limit
        self.cacheRead = cacheRead
        self.cacheWrite = cacheWrite
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        promptTokens = try c.decode(Int.self, forKey: .promptTokens)
        completionTokens = try c.decode(Int.self, forKey: .completionTokens)
        used = try c.decode(Int.self, forKey: .used)
        limit = try c.decode(Int.self, forKey: .limit)
        cacheRead = try c.decodeIfPresent(Int.self, forKey: .cacheRead) ?? 0
        cacheWrite = try c.decodeIfPresent(Int.self, forKey: .cacheWrite) ?? 0
    }
}

public struct Fallback: Decodable, Sendable, Equatable {
    public let fromModel: String
    public let toModel: String
    public let reason: String

    private enum CodingKeys: String, CodingKey { case fromModel = "from_model", toModel = "to_model", reason }
}

public struct ModelUsed: Decodable, Sendable, Equatable {
    public let alias: String?
    public let model: String
    public let provider: String
}

public struct PhaseEvent: Decodable, Sendable, Equatable {
    public let state: PhaseState
}

public enum PhaseState: String, Decodable, Sendable, Equatable {
    case waiting, thinking, streaming, tools, idle
}

public struct CheckpointEvent: Decodable, Sendable, Equatable {
    public let turn: Int
    public let id: String
}

public struct Verified: Decodable, Sendable, Equatable {
    public let resultsSummary: String
    public let ok: Bool

    private enum CodingKeys: String, CodingKey { case resultsSummary = "results_summary", ok }
}

public struct JobStarted: Decodable, Sendable, Equatable {
    public let id: String
    public let command: String
}

public struct JobFinished: Decodable, Sendable, Equatable {
    public let id: String
    public let exitCode: Int

    private enum CodingKeys: String, CodingKey { case id, exitCode = "exit_code" }
}

public struct RetryBlocked: Decodable, Sendable, Equatable {
    public let name: String
    public let attempts: Int
}

/// Synthetic, server-generated (not in `events.py`): a round-trip `ask_user` tool call awaiting a reply.
/// Field names beyond `request_id` are this client's assumption — see README "Assumed contract".
public struct AskUserOption: Codable, Sendable, Equatable {
    public let label: String
    public let description: String?
}

public struct AskUserRequest: Decodable, Sendable, Equatable {
    public let requestId: String
    public let question: String
    public let header: String?
    public let options: [AskUserOption]
    public let multiSelect: Bool

    private enum CodingKeys: String, CodingKey {
        case requestId = "request_id", question, header, options, multiSelect = "multi_select"
    }

    public init(requestId: String, question: String, header: String? = nil, options: [AskUserOption] = [], multiSelect: Bool = false) {
        self.requestId = requestId
        self.question = question
        self.header = header
        self.options = options
        self.multiSelect = multiSelect
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        requestId = try c.decode(String.self, forKey: .requestId)
        question = try c.decode(String.self, forKey: .question)
        header = try c.decodeIfPresent(String.self, forKey: .header)
        options = try c.decodeIfPresent([AskUserOption].self, forKey: .options) ?? []
        multiSelect = try c.decodeIfPresent(Bool.self, forKey: .multiSelect) ?? false
    }
}

/// Synthetic, server-generated: a tool-permission confirmation awaiting a reply.
/// Entirely this client's assumption — see README "Assumed contract".
public struct ConfirmRequest: Decodable, Sendable, Equatable {
    public let requestId: String
    public let toolName: String
    public let argsPreview: String
    public let risk: String?

    private enum CodingKeys: String, CodingKey {
        case requestId = "request_id", toolName = "tool_name", argsPreview = "args_preview", risk
    }

    public init(requestId: String, toolName: String, argsPreview: String = "", risk: String? = nil) {
        self.requestId = requestId
        self.toolName = toolName
        self.argsPreview = argsPreview
        self.risk = risk
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        requestId = try c.decode(String.self, forKey: .requestId)
        toolName = try c.decode(String.self, forKey: .toolName)
        argsPreview = try c.decodeIfPresent(String.self, forKey: .argsPreview) ?? ""
        risk = try c.decodeIfPresent(String.self, forKey: .risk)
    }
}
