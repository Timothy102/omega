import Foundation
import Observation

/// Turns a stream of daemon `Event`s into the block list a transcript view renders.
///
/// Rules (see app/Omega README for the full rationale, including where this deliberately
/// diverges from the Python TUI's reducer):
/// - A user prompt opens a new turn: any open assistant text block is implicitly done, the
///   current tool group is closed.
/// - `TextDelta` appends to the currently-open assistant text block, opening one if needed.
/// - `ToolStart` (and `SubagentSpawned`) close the open text block; consecutive tool calls with
///   no interleaving text share one collapsible group (collapses past 3 rows).
/// - A `ToolStart` identical (name + argsPreview) to the immediately preceding row in its group
///   is folded into that row as a `×N` repeat instead of a new row.
/// - Tool calls carrying a `subagentId` nest under that subagent's row instead of the top-level
///   group, and follow the same grouping/dedupe rules within that nested group.
/// - `SubagentDone` updates its `SubagentSpawned` row in place.
///
/// `@Observable` (from the `Observation` module, not SwiftUI) so a view reading `blocks` picks up
/// every mutation directly, rather than relying on some unrelated property ticking nearby.
@Observable
public final class TranscriptModel {
    private struct RowLocation {
        var blockIndex: Int
        var parentRowIndex: Int?
        var rowIndex: Int
    }

    public private(set) var blocks: [TranscriptBlock] = []
    public private(set) var currentModel: ModelUsed?
    public private(set) var lastUsage: UsageEvent?

    private var liveAssistantIndex: Int?
    private var currentGroupIndex: Int?
    private var lastTopLevelKey: String?
    private var lastNestedKey: [String: String] = [:]
    private var subagentLocation: [String: RowLocation] = [:]
    private var toolLocation: [String: RowLocation] = [:]

    private var activeToolCount = 0
    private var activeSubagentCount = 0
    private var phaseState: PhaseState = .idle
    private var turnStartedAt: Date?
    private var thinkingSince: Date?
    private var thinkingAccumulated: Double = 0
    private var liveTextLength = 0

    public init() {}

    // MARK: - Public entry points

    public func addUserMessage(_ text: String, mode: String, at date: Date = Date()) {
        closeLiveText()
        currentGroupIndex = nil
        blocks.append(.userPrompt(UserPromptBlock(id: UUID(), text: text, mode: mode, timestamp: date)))
    }

    public func apply(_ event: Event) {
        switch event.payload {
        case .textDelta(let d): handleTextDelta(d)
        case .toolStart(let s): handleToolStart(s)
        case .toolEnd(let e): handleToolEnd(e)
        case .subagentSpawned(let s): handleSubagentSpawned(s)
        case .subagentDone(let d): handleSubagentDone(d)
        case .done(let d): handleDone(d)
        case .error(let e): appendSystemLine("✗ error  \(firstLine(e.message))", isError: true)
        case .compacted(let c): appendSystemLine("⏺ \(c.note)", isError: false)
        case .memoryWrite(let m): appendSystemLine("◆ memory: \(m.type) '\(truncate(m.title, 60))' (\(m.scope))", isError: false)
        case .memoryConsolidated(let m): appendSystemLine("◆ memory: \(m.summary)", isError: false)
        case .checkpoint: appendSystemLine("⎘ checkpoint", isError: false)
        case .verified(let v): appendSystemLine(v.ok ? "✓ verified: \(v.resultsSummary)" : "✗ verification failed: \(v.resultsSummary)", isError: !v.ok)
        case .jobStarted(let j): appendSystemLine("⟳ job \(j.id) started", isError: false)
        case .jobFinished(let j): appendSystemLine(j.exitCode == 0 ? "✓ job \(j.id) finished (exit 0)" : "✗ job \(j.id) finished (exit \(j.exitCode))", isError: j.exitCode != 0)
        case .retryBlocked(let r): appendSystemLine("⚠ \(r.name) blocked after \(r.attempts) attempts", isError: true)
        case .fallback(let f): appendSystemLine("⇄ fell back \(f.fromModel) → \(f.toModel) (\(f.reason))", isError: false)
        case .modelUsed(let m): currentModel = m
        case .usage(let u): lastUsage = u
        case .phase(let p): handlePhase(p)
        case .askUserRequest(let r): handleAskUser(r)
        case .confirmRequest(let r): handleConfirm(r)
        case .unknown: break
        }
    }

    public func toggleGroup(_ blockId: UUID) {
        guard let idx = blocks.firstIndex(where: { $0.id == blockId }) else { return }
        if case .toolGroup(var b) = blocks[idx] {
            b.group.expanded.toggle()
            blocks[idx] = .toolGroup(b)
        }
    }

    public func toggleSubagentChildren(subagentId: String) {
        guard let loc = subagentLocation[subagentId] else { return }
        mutateRow(at: loc) { row in
            row.children?.expanded.toggle()
        }
    }

    public func resolveAskUser(requestId: String, chosen: [String]) {
        guard let idx = blocks.firstIndex(where: {
            if case .askUser(let b) = $0 { return b.request.requestId == requestId }
            return false
        }) else { return }
        if case .askUser(var b) = blocks[idx] {
            b.resolved = true
            b.chosen = chosen
            blocks[idx] = .askUser(b)
        }
    }

    public func resolveConfirm(requestId: String, approved: Bool) {
        guard let idx = blocks.firstIndex(where: {
            if case .confirm(let b) = $0 { return b.request.requestId == requestId }
            return false
        }) else { return }
        if case .confirm(var b) = blocks[idx] {
            b.resolved = true
            b.approved = approved
            blocks[idx] = .confirm(b)
        }
    }

    public func statusLine(now: Date = Date()) -> StatusLine? {
        guard phaseState != .idle, let start = turnStartedAt else { return nil }
        let elapsed = now.timeIntervalSince(start)
        let verb: String
        if activeToolCount > 0 {
            verb = "Running \(activeToolCount) tool\(activeToolCount == 1 ? "" : "s")…"
        } else if activeSubagentCount > 0 {
            verb = "Waiting for subagent…"
        } else if phaseState == .streaming {
            verb = "Writing…"
        } else {
            verb = "Thinking…"
        }
        var thinking = thinkingAccumulated
        if phaseState == .thinking, let since = thinkingSince {
            thinking += now.timeIntervalSince(since)
        }
        let tokens = lastUsage?.completionTokens ?? (liveTextLength / 4)
        return StatusLine(verb: verb, elapsedSeconds: elapsed, tokens: tokens, thinkingSeconds: thinking)
    }

    // MARK: - Text

    private func handleTextDelta(_ d: TextDelta) {
        if liveAssistantIndex == nil {
            blocks.append(.assistantText(AssistantTextBlock(id: UUID(), text: "", finalized: false)))
            liveAssistantIndex = blocks.count - 1
            currentGroupIndex = nil
        }
        guard let idx = liveAssistantIndex, case .assistantText(var b) = blocks[idx] else { return }
        b.text += d.text
        blocks[idx] = .assistantText(b)
        liveTextLength += d.text.count
    }

    private func closeLiveText() {
        if let idx = liveAssistantIndex, case .assistantText(var b) = blocks[idx] {
            b.finalized = true
            blocks[idx] = .assistantText(b)
        }
        liveAssistantIndex = nil
    }

    private func handleDone(_ d: DoneEvent) {
        closeLiveText()
        currentGroupIndex = nil
        turnStartedAt = nil
        thinkingSince = nil
        thinkingAccumulated = 0
        liveTextLength = 0
    }

    // MARK: - Tools

    private func handleToolStart(_ s: ToolStart) {
        closeLiveText()
        activeToolCount += 1
        let key = "\(s.name)|\(s.argsPreview)"
        if let subagentId = s.subagentId {
            appendNestedRow(callId: s.callId, toolName: s.name, key: key, line: s.argsPreview, subagentId: subagentId)
        } else {
            appendTopLevelRow(callId: s.callId, toolName: s.name, key: key, line: s.argsPreview)
        }
    }

    private func handleToolEnd(_ e: ToolEnd) {
        activeToolCount = max(0, activeToolCount - 1)
        guard let loc = toolLocation[e.callId] else { return }
        let outcomeText = e.outcome.isEmpty ? nil : e.outcome
        let isError = outcomeText?.localizedCaseInsensitiveContains("error:") ?? false
        mutateRow(at: loc) { row in
            row.outcome = outcomeText
            row.isError = isError
        }
    }

    private func ensureTopLevelGroup() {
        if currentGroupIndex == nil {
            blocks.append(.toolGroup(ToolGroupBlock(id: UUID(), group: ToolGroup(rows: []))))
            currentGroupIndex = blocks.count - 1
            lastTopLevelKey = nil
        }
    }

    private func appendTopLevelRow(callId: String, toolName: String, key: String, line: String) {
        ensureTopLevelGroup()
        guard let groupIdx = currentGroupIndex, case .toolGroup(var block) = blocks[groupIdx] else { return }
        if key == lastTopLevelKey, let lastIdx = block.group.rows.indices.last, !block.group.rows[lastIdx].isSubagent {
            block.group.rows[lastIdx].repeatCount += 1
            block.group.rows[lastIdx].outcome = nil
            blocks[groupIdx] = .toolGroup(block)
            toolLocation[callId] = RowLocation(blockIndex: groupIdx, parentRowIndex: nil, rowIndex: lastIdx)
            return
        }
        block.group.rows.append(ToolRow(id: callId, toolName: toolName, line: line))
        blocks[groupIdx] = .toolGroup(block)
        lastTopLevelKey = key
        toolLocation[callId] = RowLocation(blockIndex: groupIdx, parentRowIndex: nil, rowIndex: block.group.rows.count - 1)
    }

    private func appendNestedRow(callId: String, toolName: String, key: String, line: String, subagentId: String) {
        guard let loc = subagentLocation[subagentId] else {
            appendTopLevelRow(callId: callId, toolName: toolName, key: key, line: line)
            return
        }
        guard case .toolGroup(var block) = blocks[loc.blockIndex] else { return }
        var parent = block.group.rows[loc.rowIndex]
        var children = parent.children ?? ToolGroup(rows: [])
        if key == lastNestedKey[subagentId], let lastIdx = children.rows.indices.last {
            children.rows[lastIdx].repeatCount += 1
            children.rows[lastIdx].outcome = nil
            parent.children = children
            block.group.rows[loc.rowIndex] = parent
            blocks[loc.blockIndex] = .toolGroup(block)
            toolLocation[callId] = RowLocation(blockIndex: loc.blockIndex, parentRowIndex: loc.rowIndex, rowIndex: lastIdx)
            return
        }
        children.rows.append(ToolRow(id: callId, toolName: toolName, line: line))
        lastNestedKey[subagentId] = key
        parent.children = children
        block.group.rows[loc.rowIndex] = parent
        blocks[loc.blockIndex] = .toolGroup(block)
        toolLocation[callId] = RowLocation(blockIndex: loc.blockIndex, parentRowIndex: loc.rowIndex, rowIndex: children.rows.count - 1)
    }

    private func mutateRow(at loc: RowLocation, _ transform: (inout ToolRow) -> Void) {
        guard case .toolGroup(var block) = blocks[loc.blockIndex] else { return }
        if let parentIdx = loc.parentRowIndex {
            guard parentIdx < block.group.rows.count else { return }
            var parent = block.group.rows[parentIdx]
            guard var children = parent.children, loc.rowIndex < children.rows.count else { return }
            transform(&children.rows[loc.rowIndex])
            parent.children = children
            block.group.rows[parentIdx] = parent
        } else {
            guard loc.rowIndex < block.group.rows.count else { return }
            transform(&block.group.rows[loc.rowIndex])
        }
        blocks[loc.blockIndex] = .toolGroup(block)
    }

    // MARK: - Subagents

    private func handleSubagentSpawned(_ s: SubagentSpawned) {
        closeLiveText()
        ensureTopLevelGroup()
        activeSubagentCount += 1
        guard let groupIdx = currentGroupIndex, case .toolGroup(var block) = blocks[groupIdx] else { return }
        block.group.rows.append(ToolRow(
            id: s.subagentId, toolName: "subagent", line: "subagent(\(s.tier))  \(s.taskPreview)", isSubagent: true
        ))
        blocks[groupIdx] = .toolGroup(block)
        lastTopLevelKey = nil
        subagentLocation[s.subagentId] = RowLocation(blockIndex: groupIdx, parentRowIndex: nil, rowIndex: block.group.rows.count - 1)
    }

    private func handleSubagentDone(_ d: SubagentDone) {
        activeSubagentCount = max(0, activeSubagentCount - 1)
        guard let loc = subagentLocation[d.subagentId] else { return }
        mutateRow(at: loc) { row in
            row.line = "Agent \"\(d.summaryPreview)\" finished"
            row.isSubagentDone = true
        }
    }

    // MARK: - Phase / status

    private func handlePhase(_ p: PhaseEvent) {
        let now = Date()
        if phaseState == .thinking, p.state != .thinking, let since = thinkingSince {
            thinkingAccumulated += now.timeIntervalSince(since)
            thinkingSince = nil
        }
        if p.state == .thinking, thinkingSince == nil {
            thinkingSince = now
        }
        if phaseState == .idle, p.state != .idle {
            turnStartedAt = now
        }
        if p.state == .idle {
            turnStartedAt = nil
            thinkingSince = nil
        }
        phaseState = p.state
    }

    // MARK: - Ask user / confirm

    private func handleAskUser(_ r: AskUserRequest) {
        closeLiveText()
        currentGroupIndex = nil
        blocks.append(.askUser(AskUserCardBlock(id: UUID(), request: r, resolved: false, chosen: [])))
    }

    private func handleConfirm(_ r: ConfirmRequest) {
        closeLiveText()
        currentGroupIndex = nil
        blocks.append(.confirm(ConfirmCardBlock(id: UUID(), request: r, resolved: false, approved: nil)))
    }

    // MARK: - Small helpers

    private func appendSystemLine(_ text: String, isError: Bool) {
        blocks.append(.systemLine(SystemLineBlock(id: UUID(), text: text, isError: isError)))
    }

    private func firstLine(_ s: String) -> String {
        s.split(separator: "\n", maxSplits: 1, omittingEmptySubsequences: false).first.map(String.init) ?? s
    }

    private func truncate(_ s: String, _ n: Int) -> String {
        s.count <= n ? s : String(s.prefix(n))
    }
}
