import Foundation

public struct ToolRow: Identifiable, Equatable, Sendable {
    public let id: String
    public var toolName: String
    public var line: String
    public var outcome: String?
    public var isError: Bool
    public var repeatCount: Int
    public var children: ToolGroup?
    public var isSubagent: Bool
    public var isSubagentDone: Bool

    public init(
        id: String, toolName: String, line: String, outcome: String? = nil, isError: Bool = false,
        repeatCount: Int = 1, children: ToolGroup? = nil, isSubagent: Bool = false, isSubagentDone: Bool = false
    ) {
        self.id = id
        self.toolName = toolName
        self.line = line
        self.outcome = outcome
        self.isError = isError
        self.repeatCount = repeatCount
        self.children = children
        self.isSubagent = isSubagent
        self.isSubagentDone = isSubagentDone
    }
}

public struct ToolGroup: Equatable, Sendable {
    public static let collapseThreshold = 3

    public var rows: [ToolRow]
    public var expanded: Bool

    public init(rows: [ToolRow], expanded: Bool = false) {
        self.rows = rows
        self.expanded = expanded
    }

    public var visibleRows: [ToolRow] {
        expanded || rows.count <= Self.collapseThreshold ? rows : Array(rows.prefix(Self.collapseThreshold))
    }

    public var hiddenCount: Int {
        expanded ? 0 : max(0, rows.count - Self.collapseThreshold)
    }
}

public struct UserPromptBlock: Identifiable, Equatable, Sendable {
    public let id: UUID
    public var text: String
    public var mode: String
    public var timestamp: Date
}

public struct AssistantTextBlock: Identifiable, Equatable, Sendable {
    public let id: UUID
    public var text: String
    public var finalized: Bool
}

public struct ToolGroupBlock: Identifiable, Equatable, Sendable {
    public let id: UUID
    public var group: ToolGroup
}

public struct SystemLineBlock: Identifiable, Equatable, Sendable {
    public let id: UUID
    public var text: String
    public var isError: Bool
}

public struct AskUserCardBlock: Identifiable, Equatable, Sendable {
    public let id: UUID
    public var request: AskUserRequest
    public var resolved: Bool
    public var chosen: [String]
}

public struct ConfirmCardBlock: Identifiable, Equatable, Sendable {
    public let id: UUID
    public var request: ConfirmRequest
    public var resolved: Bool
    public var approved: Bool?
}

public enum TranscriptBlock: Identifiable, Equatable, Sendable {
    case userPrompt(UserPromptBlock)
    case assistantText(AssistantTextBlock)
    case toolGroup(ToolGroupBlock)
    case systemLine(SystemLineBlock)
    case askUser(AskUserCardBlock)
    case confirm(ConfirmCardBlock)

    public var id: UUID {
        switch self {
        case .userPrompt(let b): return b.id
        case .assistantText(let b): return b.id
        case .toolGroup(let b): return b.id
        case .systemLine(let b): return b.id
        case .askUser(let b): return b.id
        case .confirm(let b): return b.id
        }
    }
}

public struct StatusLine: Equatable, Sendable {
    public var verb: String
    public var elapsedSeconds: Double
    public var tokens: Int
    public var thinkingSeconds: Double
}
