import asyncio
import json

import pytest

from omega import artifacts, config, events, llm, loop, tools
from omega.llm import ToolCall, Turn


class FakeProvider:
    name = "fake-provider"


class FakeRole:
    context = 1_000_000
    model = "fake-model"
    alias = None
    fallback_alias = None
    provider = FakeProvider()


class FakeCfg:
    def role(self, name):
        return FakeRole()


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(artifacts, "DIR", tmp_path)
    monkeypatch.setattr(tools, "SESSION_ID", "sess-events")
    yield


def test_discuss_mode_has_read_only_tools():
    _system, tool_names = loop.MODES["discuss"]
    assert tool_names == tools.READ_ONLY


@pytest.mark.asyncio
async def test_system_prompt_splits_stable_and_volatile_at_marker(tmp_path, monkeypatch):
    seen: list[str] = []

    async def stream(role, messages, schemas=None, fallback=None):
        seen.append(messages[0]["content"])
        yield "done", Turn(text="ok", tool_calls=[])
    monkeypatch.setattr(llm, "stream", stream)

    await loop.run_agent(FakeCfg(), "main", "STABLE-PART", [{"role": "user", "content": "hi"}],
                         emit=lambda _e: None)

    assert seen[0].startswith("STABLE-PART" + loop.VOLATILE_MARKER)


@pytest.mark.asyncio
async def test_trajectory_block_appears_after_a_tool_call(tmp_path, monkeypatch):
    target = tmp_path / "small.txt"
    target.write_text("hi")
    call = ToolCall(id="call_1", name="read", arguments=json.dumps({"path": str(target)}))

    seen: list[str] = []

    async def stream(role, messages, schemas=None, fallback=None):
        seen.append(messages[0]["content"])
        if len(seen) == 1:
            yield "tool", call
            yield "done", Turn(text="", tool_calls=[call])
        else:
            yield "done", Turn(text="ok", tool_calls=[])
    monkeypatch.setattr(llm, "stream", stream)

    await loop.run_agent(FakeCfg(), "main", "STABLE-PART", [{"role": "user", "content": "read it"}],
                         emit=lambda _e: None)

    assert "## Recent actions" not in seen[0]
    assert "## Recent actions" in seen[1]
    assert "read" in seen[1]


def scripted_stream(rounds: list):
    async def stream(role, messages, schemas=None, fallback=None):
        for kind, payload in rounds.pop(0):
            yield kind, payload
    return stream


@pytest.mark.asyncio
async def test_emits_tool_start_end_text_done_in_order(tmp_path, monkeypatch):
    target = tmp_path / "small.txt"
    target.write_text("hello world")
    call = ToolCall(id="call_1", name="read",
                    arguments=json.dumps({"path": str(target)}))

    rounds = [
        [("tool", call), ("done", Turn(text="", tool_calls=[call]))],
        [("text", "done!"), ("done", Turn(text="done!", tool_calls=[]))],
    ]
    monkeypatch.setattr(llm, "stream", scripted_stream(rounds))

    received: list = []
    history = [{"role": "user", "content": "read the file"}]
    result = await loop.run_agent(FakeCfg(), "main", "sys", history,
                                  emit=received.append)

    assert result == "done!"
    assert [type(e) for e in received] == [
        events.ModelUsed, events.Phase, events.ToolStart, events.Phase, events.ToolEnd,
        events.Usage, events.Phase, events.Phase, events.TextDelta, events.Done, events.Phase]

    (_model_used, phase1, start, phase_tools, end, _usage,
     phase2, phase3, delta, done, phase4) = received
    assert phase1.state == "waiting"
    assert start.call_id == "call_1" and start.name == "read"
    assert start.args_preview == "read  small.txt"
    assert start.subagent_id is None and start.tier is None
    assert phase_tools.state == "tools"
    assert phase2.state == "waiting"
    assert phase3.state == "streaming"
    assert phase4.state == "idle"
    assert end.call_id == "call_1" and end.name == "read"
    assert end.offloaded is False and end.artifact_id is None
    assert end.duration_s >= 0
    assert delta.text == "done!"
    assert done.text == "done!"


@pytest.mark.asyncio
async def test_large_tool_result_marks_tool_end_offloaded(tmp_path, monkeypatch):
    target = tmp_path / "big.txt"
    target.write_text("y" * 5000)
    call = ToolCall(id="call_1", name="read",
                    arguments=json.dumps({"path": str(target), "limit": 10000}))

    rounds = [
        [("tool", call), ("done", Turn(text="", tool_calls=[call]))],
        [("done", Turn(text="ok", tool_calls=[]))],
    ]
    monkeypatch.setattr(llm, "stream", scripted_stream(rounds))

    received: list = []
    history = [{"role": "user", "content": "read the big file"}]
    await loop.run_agent(FakeCfg(), "main", "sys", history, emit=received.append)

    ends = [e for e in received if isinstance(e, events.ToolEnd)]
    assert len(ends) == 1
    assert ends[0].offloaded is True
    assert ends[0].artifact_id is not None
    assert len(ends[0].artifact_id) == 8


@pytest.mark.asyncio
async def test_subagent_id_and_tier_stamped_on_tool_events(tmp_path, monkeypatch):
    target = tmp_path / "small.txt"
    target.write_text("hi")
    call = ToolCall(id="call_1", name="read",
                    arguments=json.dumps({"path": str(target)}))

    rounds = [
        [("tool", call), ("done", Turn(text="", tool_calls=[call]))],
        [("done", Turn(text="ok", tool_calls=[]))],
    ]
    monkeypatch.setattr(llm, "stream", scripted_stream(rounds))

    received: list = []
    history = [{"role": "user", "content": "go"}]
    await loop.run_agent(FakeCfg(), "main", "sys", history, emit=received.append,
                         subagent_id="x", tier="fast")

    start = next(e for e in received if isinstance(e, events.ToolStart))
    end = next(e for e in received if isinstance(e, events.ToolEnd))
    assert start.subagent_id == "x" and start.tier == "fast"
    # ToolEnd carries no subagent_id/tier fields per the event schema -- only
    # ToolStart does; this test just confirms run_agent accepts the kwargs.
    assert end.call_id == "call_1"


@pytest.mark.asyncio
async def test_usage_emitted_with_role_context_as_limit(tmp_path, monkeypatch):
    target = tmp_path / "small.txt"
    target.write_text("hi")
    call = ToolCall(id="call_1", name="read",
                    arguments=json.dumps({"path": str(target)}))

    rounds = [
        [("tool", call), ("done", Turn(text="", tool_calls=[call],
                                       prompt_tokens=100, completion_tokens=20))],
        [("done", Turn(text="ok", tool_calls=[]))],
    ]
    monkeypatch.setattr(llm, "stream", scripted_stream(rounds))

    received: list = []
    history = [{"role": "user", "content": "hi"}]
    await loop.run_agent(FakeCfg(), "main", "sys", history, emit=received.append)

    usage = next(e for e in received if isinstance(e, events.Usage))
    assert usage.limit == FakeRole.context
    assert usage.prompt_tokens == 100 and usage.completion_tokens == 20
    assert usage.used == 120


@pytest.mark.asyncio
async def test_parallel_tool_calls_report_distinct_durations(tmp_path, monkeypatch):
    """Regression: timing each dispatched task from AFTER the whole batch's
    asyncio.gather made every tool in a parallel round report the slowest
    call's duration, not its own."""
    call_a = ToolCall(id="a", name="bash", arguments=json.dumps({"command": "fast"}))
    call_b = ToolCall(id="b", name="bash", arguments=json.dumps({"command": "slow"}))

    async def fake_run(call, allowed=None):
        await asyncio.sleep(0.02 if call.id == "a" else 0.2)
        return f"done {call.id}"
    monkeypatch.setattr(tools, "run", fake_run)

    rounds = [
        [("tool", call_a), ("tool", call_b),
         ("done", Turn(text="", tool_calls=[call_a, call_b]))],
        [("done", Turn(text="ok", tool_calls=[]))],
    ]
    monkeypatch.setattr(llm, "stream", scripted_stream(rounds))

    received: list = []
    history = [{"role": "user", "content": "go"}]
    await loop.run_agent(FakeCfg(), "main", "sys", history, emit=received.append)

    ends = {e.call_id: e for e in received if isinstance(e, events.ToolEnd)}
    assert ends["a"].duration_s < ends["b"].duration_s
    assert ends["b"].duration_s >= 0.15


@pytest.mark.asyncio
async def test_usage_limit_matches_selected_model_context(tmp_path, monkeypatch):
    """Regression guard: selecting a model via `/model` must carry that
    model's own context window into `Usage.limit`, not some other role's --
    `fable`'s 1048576 must never be reported as the dataclass default 128000."""
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    cfg = config.load()
    call = ToolCall(id="call_1", name="bash", arguments=json.dumps({"command": "pwd"}))

    rounds = [
        [("tool", call), ("done", Turn(text="", tool_calls=[call],
                                       prompt_tokens=100, completion_tokens=50))],
        [("done", Turn(text="ok", tool_calls=[]))],
    ]
    monkeypatch.setattr(llm, "stream", scripted_stream(rounds))

    received: list = []
    history = [{"role": "user", "content": "hi"}]
    await loop.run_turn(cfg, history, mode="build", emit=received.append, model="fable")

    usage = next(e for e in received if isinstance(e, events.Usage))
    assert usage.limit == 1048576
