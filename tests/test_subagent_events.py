import pytest

from rig import events, loop, subagent


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    monkeypatch.setattr(subagent, "CFG", object())
    monkeypatch.setattr(subagent, "EMIT", None)
    yield


@pytest.mark.asyncio
async def test_subagent_emits_spawn_and_done_around_the_run(monkeypatch):
    async def fake_run_agent(cfg, role, system, history, tool_names, emit=None,
                             max_rounds=12, subagent_id=None, tier=None):
        assert subagent_id and tier == "fast"
        # Inner chatter -- must not leak upward.
        emit(events.TextDelta("thinking out loud"))
        emit(events.ToolStart(call_id="c1", name="grep", args_preview="foo"))
        emit(events.Done("thinking out loud"))
        return "summary of findings"

    monkeypatch.setattr(loop, "run_agent", fake_run_agent)

    received: list = []
    subagent.EMIT = received.append

    result = await subagent._subagent("look up the thing please", tier="fast")

    assert result == "summary of findings"
    assert [type(e) for e in received] == [
        events.SubagentSpawned, events.ToolStart, events.SubagentDone]

    spawned, tool_start, done = received
    assert spawned.subagent_id == done.subagent_id
    assert spawned.tier == "fast"
    assert spawned.task_preview == "look up the thing please"
    assert done.summary_preview == "summary of findings"
    assert tool_start.name == "grep"


@pytest.mark.asyncio
async def test_inner_text_deltas_and_done_are_filtered(monkeypatch):
    seen_by_inner: list = []

    async def fake_run_agent(cfg, role, system, history, tool_names, emit=None,
                             max_rounds=12, subagent_id=None, tier=None):
        for ev in (events.TextDelta("a"), events.TextDelta("b"),
                   events.Done("ab")):
            emit(ev)
            seen_by_inner.append(ev)
        return "ab"

    monkeypatch.setattr(loop, "run_agent", fake_run_agent)

    received: list = []
    subagent.EMIT = received.append

    await subagent._subagent("task", tier="mid")

    assert len(seen_by_inner) == 3
    kinds = [type(e) for e in received]
    assert events.TextDelta not in kinds
    assert events.Done not in kinds
    assert kinds == [events.SubagentSpawned, events.SubagentDone]


@pytest.mark.asyncio
async def test_returns_error_string_when_cfg_not_wired(monkeypatch):
    monkeypatch.setattr(subagent, "CFG", None)
    result = await subagent._subagent("task")
    assert result == "error: subagent not wired"
