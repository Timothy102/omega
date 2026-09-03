import Foundation
import Testing
@testable import OmegaCore

private func ev(_ payload: EventPayload) -> Event { Event(type: "test", t: nil, payload: payload) }

private func toolStart(_ callId: String, _ name: String, _ args: String, subagentId: String? = nil, tier: String? = nil) -> Event {
    ev(.toolStart(ToolStart(callId: callId, name: name, argsPreview: args, subagentId: subagentId, tier: tier)))
}

private func toolEnd(_ callId: String, _ name: String, outcome: String = "") -> Event {
    ev(.toolEnd(ToolEnd(callId: callId, name: name, resultPreview: "", durationS: 0.1, outcome: outcome)))
}

@Test func textDeltaOpensAndAccumulatesOneBlock() {
    let model = TranscriptModel()
    model.apply(ev(.textDelta(TextDelta(text: "Hel"))))
    model.apply(ev(.textDelta(TextDelta(text: "lo"))))
    #expect(model.blocks.count == 1)
    guard case .assistantText(let b) = model.blocks[0] else { Issue.record("expected assistantText"); return }
    #expect(b.text == "Hello")
    #expect(b.finalized == false)
}

@Test func toolStartClosesOpenTextBlockAndStartsGroup() {
    let model = TranscriptModel()
    model.apply(ev(.textDelta(TextDelta(text: "thinking out loud"))))
    model.apply(toolStart("c1", "read", "read  foo.py"))

    #expect(model.blocks.count == 2)
    guard case .assistantText(let text) = model.blocks[0] else { Issue.record("expected assistantText"); return }
    #expect(text.finalized == true)
    guard case .toolGroup(let group) = model.blocks[1] else { Issue.record("expected toolGroup"); return }
    #expect(group.group.rows.count == 1)
    #expect(group.group.rows[0].line == "read  foo.py")
}

@Test func newTextBlockOpensAfterToolGroup() {
    let model = TranscriptModel()
    model.apply(toolStart("c1", "read", "read  foo.py"))
    model.apply(toolEnd("c1", "read", outcome: "→ 12 lines"))
    model.apply(ev(.textDelta(TextDelta(text: "done reading"))))

    #expect(model.blocks.count == 2)
    guard case .assistantText(let b) = model.blocks[1] else { Issue.record("expected assistantText"); return }
    #expect(b.text == "done reading")
}

@Test func toolEndAttachesOutcomeToItsRow() {
    let model = TranscriptModel()
    model.apply(toolStart("c1", "bash", "bash  $ pytest"))
    model.apply(toolEnd("c1", "bash", outcome: "→ exit 1"))

    guard case .toolGroup(let group) = model.blocks[0] else { Issue.record("expected toolGroup"); return }
    #expect(group.group.rows[0].outcome == "→ exit 1")
    #expect(group.group.rows[0].isError == false)
}

@Test func errorOutcomeIsFlagged() {
    let model = TranscriptModel()
    model.apply(toolStart("c1", "bash", "bash  $ pytest"))
    model.apply(toolEnd("c1", "bash", outcome: "→ error: command not found"))

    guard case .toolGroup(let group) = model.blocks[0] else { Issue.record("expected toolGroup"); return }
    #expect(group.group.rows[0].isError == true)
}

@Test func consecutiveIdenticalToolCallsDedupeToOneRowWithCount() {
    let model = TranscriptModel()
    model.apply(toolStart("c1", "read", "read  foo.py"))
    model.apply(toolEnd("c1", "read", outcome: "→ 10 lines"))
    model.apply(toolStart("c2", "read", "read  foo.py"))
    model.apply(toolEnd("c2", "read", outcome: "→ 10 lines"))
    model.apply(toolStart("c3", "read", "read  foo.py"))

    guard case .toolGroup(let group) = model.blocks[0] else { Issue.record("expected toolGroup"); return }
    #expect(group.group.rows.count == 1)
    #expect(group.group.rows[0].repeatCount == 3)
}

@Test func differentToolCallsDoNotDedupe() {
    let model = TranscriptModel()
    model.apply(toolStart("c1", "read", "read  foo.py"))
    model.apply(toolStart("c2", "read", "read  bar.py"))

    guard case .toolGroup(let group) = model.blocks[0] else { Issue.record("expected toolGroup"); return }
    #expect(group.group.rows.count == 2)
    #expect(group.group.rows[0].repeatCount == 1)
    #expect(group.group.rows[1].repeatCount == 1)
}

@Test func groupCollapsesPastThreeRows() {
    let model = TranscriptModel()
    for i in 0..<5 {
        model.apply(toolStart("c\(i)", "read", "read  file\(i).py"))
        model.apply(toolEnd("c\(i)", "read", outcome: "→ 1 line"))
    }
    guard case .toolGroup(let group) = model.blocks[0] else { Issue.record("expected toolGroup"); return }
    #expect(group.group.rows.count == 5)
    #expect(group.group.expanded == false)
    #expect(group.group.visibleRows.count == 3)
    #expect(group.group.hiddenCount == 2)

    model.toggleGroup(group.id)
    guard case .toolGroup(let expanded) = model.blocks[0] else { Issue.record("expected toolGroup"); return }
    #expect(expanded.group.visibleRows.count == 5)
    #expect(expanded.group.hiddenCount == 0)
}

@Test func subagentSpawnedCreatesRowAndNestsItsToolCalls() {
    let model = TranscriptModel()
    model.apply(ev(.subagentSpawned(SubagentSpawned(subagentId: "sub1", tier: "haiku", taskPreview: "grep for TODOs"))))
    model.apply(toolStart("c1", "grep", "grep  /TODO/ in src", subagentId: "sub1"))
    model.apply(toolEnd("c1", "grep", outcome: "→ 4 matches"))

    guard case .toolGroup(let group) = model.blocks[0] else { Issue.record("expected toolGroup"); return }
    #expect(group.group.rows.count == 1)
    let subagentRow = group.group.rows[0]
    #expect(subagentRow.isSubagent == true)
    #expect(subagentRow.children?.rows.count == 1)
    #expect(subagentRow.children?.rows[0].outcome == "→ 4 matches")
}

@Test func subagentDoneUpdatesRowInPlaceRatherThanAppending() {
    let model = TranscriptModel()
    model.apply(ev(.subagentSpawned(SubagentSpawned(subagentId: "sub1", tier: "haiku", taskPreview: "grep for TODOs"))))
    model.apply(ev(.subagentDone(SubagentDone(subagentId: "sub1", summaryPreview: "found 4 TODOs"))))

    #expect(model.blocks.count == 1)
    guard case .toolGroup(let group) = model.blocks[0] else { Issue.record("expected toolGroup"); return }
    #expect(group.group.rows.count == 1)
    #expect(group.group.rows[0].isSubagentDone == true)
    #expect(group.group.rows[0].line.contains("found 4 TODOs"))
}

@Test func userMessageStartsNewTurnAndClosesPriorGroup() {
    let model = TranscriptModel()
    model.apply(toolStart("c1", "read", "read  foo.py"))
    model.addUserMessage("do the next thing", mode: "build")
    model.apply(toolStart("c2", "write", "write  bar.py"))

    #expect(model.blocks.count == 3)
    guard case .userPrompt(let prompt) = model.blocks[1] else { Issue.record("expected userPrompt"); return }
    #expect(prompt.text == "do the next thing")
    guard case .toolGroup(let secondGroup) = model.blocks[2] else { Issue.record("expected toolGroup"); return }
    #expect(secondGroup.group.rows.count == 1)
}

@Test func askUserRequestRendersAsCardAndResolves() {
    let model = TranscriptModel()
    model.apply(ev(.askUserRequest(AskUserRequest(
        requestId: "req1", question: "Which framework?",
        options: [AskUserOption(label: "SwiftUI", description: nil)]
    ))))
    #expect(model.blocks.count == 1)
    guard case .askUser(let card) = model.blocks[0] else { Issue.record("expected askUser card"); return }
    #expect(card.resolved == false)

    model.resolveAskUser(requestId: "req1", chosen: ["SwiftUI"])
    guard case .askUser(let resolved) = model.blocks[0] else { Issue.record("expected askUser card"); return }
    #expect(resolved.resolved == true)
    #expect(resolved.chosen == ["SwiftUI"])
}

@Test func confirmRequestRendersAsCardAndResolves() {
    let model = TranscriptModel()
    model.apply(ev(.confirmRequest(ConfirmRequest(requestId: "req2", toolName: "bash", argsPreview: "rm -rf build"))))
    model.resolveConfirm(requestId: "req2", approved: true)
    guard case .confirm(let card) = model.blocks[0] else { Issue.record("expected confirm card"); return }
    #expect(card.resolved == true)
    #expect(card.approved == true)
}

@Test func statusLineReflectsActiveToolsThenIdlesOnDone() {
    let model = TranscriptModel()
    #expect(model.statusLine() == nil)

    model.apply(ev(.phase(PhaseEvent(state: .thinking))))
    var status = model.statusLine()
    #expect(status?.verb == "Thinking…")

    model.apply(toolStart("c1", "bash", "bash  $ pytest"))
    status = model.statusLine()
    #expect(status?.verb == "Running 1 tool…")

    model.apply(toolEnd("c1", "bash", outcome: "→ exit 0"))
    model.apply(ev(.done(DoneEvent(text: "all set"))))
    #expect(model.statusLine() == nil)
}

@Test func statusLinePrefersSubagentWaitOverThinking() {
    let model = TranscriptModel()
    model.apply(ev(.phase(PhaseEvent(state: .thinking))))
    model.apply(ev(.subagentSpawned(SubagentSpawned(subagentId: "sub1", tier: "haiku", taskPreview: "explore"))))
    #expect(model.statusLine()?.verb == "Waiting for subagent…")
}

@Test func usageEventFeedsStatusLineTokenCount() {
    let model = TranscriptModel()
    model.apply(ev(.phase(PhaseEvent(state: .streaming))))
    model.apply(ev(.usage(UsageEvent(promptTokens: 100, completionTokens: 1200, used: 1300, limit: 200_000))))
    #expect(model.statusLine()?.tokens == 1200)
}
