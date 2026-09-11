import Foundation

/// The `@query` token under the caret, if the user is in a file-mention.
public struct MentionQuery: Equatable, Sendable {
    public let query: String
    /// UTF-16 location of the leading `@`.
    public let location: Int
    /// UTF-16 length of `@` + query.
    public let length: Int

    public init(query: String, location: Int, length: Int) {
        self.query = query
        self.location = location
        self.length = length
    }

    public var utf16Range: NSRange { NSRange(location: location, length: length) }
}

/// Pure helpers for the macOS composer: mention parsing, file ranking, prompt assembly.
public enum ComposerMentions {
    /// Characters allowed in a mention token after `@`.
    private static let tokenCharacters = CharacterSet.alphanumerics
        .union(CharacterSet(charactersIn: "._-++/\\"))

    /// If `cursorUTF16` sits inside an `@token` that started at a word boundary, return it.
    public static func query(in text: String, cursorUTF16: Int) -> MentionQuery? {
        let ns = text as NSString
        let len = ns.length
        guard len > 0 else { return nil }
        let cursor = max(0, min(cursorUTF16, len))

        var start = cursor
        while start > 0 {
            let prev = start - 1
            let ch = ns.character(at: prev)
            guard let scalar = UnicodeScalar(ch), tokenCharacters.contains(scalar) else { break }
            start = prev
        }
        guard start > 0, ns.character(at: start - 1) == 0x40 else { return nil } // '@'
        let at = start - 1
        if at > 0 {
            let before = ns.character(at: at - 1)
            if let scalar = UnicodeScalar(before), !CharacterSet.whitespacesAndNewlines.contains(scalar) {
                return nil
            }
        }
        let query = ns.substring(with: NSRange(location: start, length: cursor - start))
        return MentionQuery(query: query, location: at, length: cursor - at)
    }

    /// Rank workspace-relative paths for a mention query. Empty query → shortest paths first.
    public static func rank(files: [String], query: String, limit: Int = 12) -> [String] {
        let q = query.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        if q.isEmpty {
            return Array(
                files.sorted { lhs, rhs in
                    if pathDepth(lhs) != pathDepth(rhs) { return pathDepth(lhs) < pathDepth(rhs) }
                    return lhs.localizedCaseInsensitiveCompare(rhs) == .orderedAscending
                }
                .prefix(limit)
            )
        }

        struct Scored { let path: String; let score: Int; let filename: String }
        var hits: [Scored] = []
        hits.reserveCapacity(min(files.count, 64))
        for path in files {
            let filename = (path as NSString).lastPathComponent
            let pathLower = path.lowercased()
            let fileLower = filename.lowercased()
            let score: Int
            if fileLower == q { score = 0 }
            else if fileLower.hasPrefix(q) { score = 1 }
            else if pathLower.hasPrefix(q) { score = 2 }
            else if fileLower.contains(q) { score = 3 }
            else if pathLower.contains(q) { score = 4 }
            else { continue }
            hits.append(Scored(path: path, score: score, filename: filename))
        }
        hits.sort { lhs, rhs in
            if lhs.score != rhs.score { return lhs.score < rhs.score }
            if lhs.filename.count != rhs.filename.count { return lhs.filename.count < rhs.filename.count }
            return lhs.path.localizedCaseInsensitiveCompare(rhs.path) == .orderedAscending
        }
        if hits.count > limit { hits.removeSubrange(limit...) }
        return hits.map(\.path)
    }

    private static func pathDepth(_ path: String) -> Int {
        path.split(separator: "/").count
    }
}

public enum ComposerPromptBuilder {
    /// Flatten the composer's text + dropped/pasted files into the text-only prompt the daemon accepts.
    public static func build(text: String, attachmentPaths: [String]) -> String {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        let paths = attachmentPaths.map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }.filter { !$0.isEmpty }
        if paths.isEmpty { return trimmed }
        var lines: [String] = []
        if !trimmed.isEmpty {
            lines.append(trimmed)
            lines.append("")
        }
        lines.append("Attached:")
        for path in paths {
            lines.append("- \(path)")
        }
        return lines.joined(separator: "\n")
    }

    /// Prefer a workspace-relative path so the agent can `read` it from cwd.
    public static func displayPath(_ path: String, workspaceRoot: String?) -> String {
        guard let root = workspaceRoot, !root.isEmpty else { return path }
        let rootURL = URL(fileURLWithPath: root).standardizedFileURL
        let fileURL = URL(fileURLWithPath: path).standardizedFileURL
        let rootPath = rootURL.path
        let filePath = fileURL.path
        if filePath == rootPath { return "." }
        let prefix = rootPath.hasSuffix("/") ? rootPath : rootPath + "/"
        if filePath.hasPrefix(prefix) {
            return String(filePath.dropFirst(prefix.count))
        }
        return path
    }
}
