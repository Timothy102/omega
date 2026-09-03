import Foundation

/// Mirrors `omega/ui/format.py`'s `category()` classifier — used by the app to pick a tool row's
/// accent color (cyan reads, yellow writes, magenta bash, green subagents, blue memory, indigo MCP, red errors).
public enum ToolCategory: String, Sendable, Equatable, CaseIterable {
    case read, write, bash, subagent, memory, artifact, askUser, mcp, error
}

public enum ToolFormatting {
    public static func category(forToolName name: String) -> ToolCategory {
        switch name {
        case "read", "glob", "grep": return .read
        case "write", "edit": return .write
        case "bash": return .bash
        case "subagent": return .subagent
        case "remember", "recall", "supersede", "link": return .memory
        case "fetch_result", "list_artifacts", "save_artifact", "update_artifact": return .artifact
        case "ask_user": return .askUser
        case "call_tool", "find_tools": return .mcp
        default:
            return name.hasPrefix("mcp__") ? .mcp : .read
        }
    }

    /// `13600 -> "13.6k"`, `1_000_000 -> "1.0M"`, `42 -> "42"` — mirrors `format.py`'s `fmt_num`.
    public static func formatCount(_ n: Int) -> String {
        if n >= 1_000_000 { return String(format: "%.1fM", Double(n) / 1_000_000) }
        if n >= 1_000 { return String(format: "%.1fk", Double(n) / 1_000) }
        return "\(n)"
    }
}
