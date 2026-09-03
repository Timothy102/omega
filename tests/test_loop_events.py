import json

import pytest

from rig import artifacts, events, llm, loop, tools
from rig.llm import ToolCall, Turn


class FakeProvider:
    name = "fake-provider"


class FakeRole:
    context = 1_000_000
    model = "fake-model"
    alias = None
    provider = FakeProvider()


class FakeCfg:
    def role(self, name):
        return FakeRole()


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(artifacts, "DIR", tmp_path)
    monkeypatch.setattr(tools, "SESSION_ID", "sess-events")
    yield


def scripted_stream(rounds: list):
    async def stream(role, messages, schemas=None):
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
        events.ModelUsed, events.ToolStart, events.ToolEnd, events.Usage,
        events.TextDelta, events.Done]

    _model_used, start, end, _usage, delta, done = received
    assert start.call_id == "call_1" and start.name == "read"
    assert start.args_preview == str(target)[:60]
    assert start.subagent_id is None and start.tier is None
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
