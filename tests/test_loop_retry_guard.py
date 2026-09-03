import json

import pytest

from omega import events, llm, loop, permissions, tools
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


def scripted_stream(rounds: list):
    async def stream(role, messages, schemas=None, fallback=None):
        for kind, payload in rounds.pop(0):
            yield kind, payload
    return stream


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    loop._reset_repeat_fails()
    tools.set_tainted(False)
    monkeypatch.setattr(tools, "SESSION_ID", None)
    monkeypatch.setattr(permissions, "STORE", tmp_path / "permissions.json")

    async def approve_all(name, args, why):
        return True
    tools.CONFIRM = approve_all
    yield
    tools.CONFIRM = None
    loop._reset_repeat_fails()


@pytest.fixture
def fake_failing_tool(monkeypatch):
    calls = {"count": 0}

    async def fn(x: str = "") -> str:
        calls["count"] += 1
        return "error: boom"

    entry = tools.ToolEntry(
        fn=fn, locks_path=None, mutates=False, deferred=False,
        schema={"type": "function", "function": {
            "name": "fake_fail", "description": "test tool that always errors",
            "parameters": {"type": "object", "properties": {"x": {"type": "string"}},
                           "required": []}}})
    monkeypatch.setitem(tools.REGISTRY, "fake_fail", entry)
    return calls


def _call(call_id: str, x: str = "same") -> ToolCall:
    return ToolCall(id=call_id, name="fake_fail", arguments=json.dumps({"x": x}))


@pytest.mark.asyncio
async def test_identical_repeat_failures_are_warned_then_blocked(monkeypatch, fake_failing_tool):
    call1, call2, call3, call4 = (_call(f"c{i}") for i in range(1, 5))

    rounds = [
        [("tool", call1), ("done", Turn(text="", tool_calls=[call1]))],
        [("tool", call2), ("done", Turn(text="", tool_calls=[call2]))],
        [("tool", call3), ("done", Turn(text="", tool_calls=[call3]))],
        [("tool", call4), ("done", Turn(text="", tool_calls=[call4]))],
        [("done", Turn(text="giving up", tool_calls=[]))],
    ]
    monkeypatch.setattr(llm, "stream", scripted_stream(rounds))

    received: list = []
    history = [{"role": "user", "content": "save the project"}]
    result = await loop.run_agent(FakeCfg(), "main", "sys", history, emit=received.append)

    assert result == "giving up"

    tool_messages = [m for m in history if m.get("role") == "tool"]
    assert len(tool_messages) == 4

    assert "[harness]" not in tool_messages[0]["content"]
    assert "[harness] this exact call has failed 2 times" in tool_messages[1]["content"]

    assert tool_messages[2]["content"].startswith(
        "error: blocked — identical call already failed 2 times")
    assert "Change the arguments or ask the user." in tool_messages[2]["content"]
    assert tool_messages[3]["content"].startswith("error: blocked")

    # Rounds 3 and 4 never reached the tool -- only rounds 1 and 2 ran it.
    assert fake_failing_tool["count"] == 2

    blocked_events = [e for e in received if isinstance(e, events.RetryBlocked)]
    assert len(blocked_events) == 2
    assert all(e.name == "fake_fail" and e.attempts == 2 for e in blocked_events)

    # A different-args call is a different key -- still executed normally,
    # even though the guard state (module-level, per turn) carries over.
    different_call = _call("c5", x="different")
    rounds2 = [
        [("tool", different_call), ("done", Turn(text="", tool_calls=[different_call]))],
        [("done", Turn(text="ok", tool_calls=[]))],
    ]
    monkeypatch.setattr(llm, "stream", scripted_stream(rounds2))
    await loop.run_agent(FakeCfg(), "main", "sys", history, emit=lambda _e: None)

    tool_messages = [m for m in history if m.get("role") == "tool"]
    assert tool_messages[4]["content"] == "error: boom"
    assert fake_failing_tool["count"] == 3


@pytest.mark.asyncio
async def test_success_resets_the_repeat_fail_streak(monkeypatch, fake_failing_tool):
    async def flaky(x: str = "") -> str:
        fake_failing_tool["count"] += 1
        return "error: boom" if fake_failing_tool["count"] == 1 else "ok now"
    monkeypatch.setitem(tools.REGISTRY, "fake_fail", tools.ToolEntry(
        fn=flaky, locks_path=None, mutates=False, deferred=False,
        schema=tools.REGISTRY["fake_fail"].schema))

    call1, call2 = _call("r1"), _call("r2")
    rounds = [
        [("tool", call1), ("done", Turn(text="", tool_calls=[call1]))],
        [("tool", call2), ("done", Turn(text="", tool_calls=[call2]))],
        [("done", Turn(text="done", tool_calls=[]))],
    ]
    monkeypatch.setattr(llm, "stream", scripted_stream(rounds))

    history = [{"role": "user", "content": "retry"}]
    await loop.run_agent(FakeCfg(), "main", "sys", history, emit=lambda _e: None)

    tool_messages = [m for m in history if m.get("role") == "tool"]
    assert tool_messages[0]["content"] == "error: boom"
    assert tool_messages[1]["content"] == "ok now"

    key = ("fake_fail", json.dumps({"x": "same"}, sort_keys=True))
    assert key not in loop._repeat_fails


@pytest.mark.asyncio
async def test_bash_gets_a_higher_block_threshold(monkeypatch):
    async def fake_run(call, allowed=None):
        return "error: boom"
    monkeypatch.setattr(tools, "run", fake_run)

    calls = [ToolCall(id=f"b{i}", name="bash", arguments=json.dumps({"command": "flaky"}))
             for i in range(1, 6)]
    rounds = [[("tool", c), ("done", Turn(text="", tool_calls=[c]))] for c in calls]
    rounds.append([("done", Turn(text="done", tool_calls=[]))])
    monkeypatch.setattr(llm, "stream", scripted_stream(rounds))

    received: list = []
    history = [{"role": "user", "content": "run it"}]
    await loop.run_agent(FakeCfg(), "main", "sys", history, emit=received.append)

    tool_messages = [m for m in history if m.get("role") == "tool"]
    # bash: BASH_REPEAT_FAIL_BLOCK=4 -> first 3 attempts execute, 4th+ blocked.
    assert tool_messages[0]["content"] == "error: boom"
    assert tool_messages[1]["content"] == (
        "error: boom\n[harness] this exact call has failed 2 times with the same "
        "error. Do not retry it unchanged: read the error, change the arguments, "
        "try a different tool, or ask the user.")
    assert tool_messages[2]["content"].startswith("error: boom")
    assert "[harness]" in tool_messages[2]["content"]
    assert tool_messages[3]["content"].startswith("error: blocked")
    assert tool_messages[4]["content"].startswith("error: blocked")

    blocked_events = [e for e in received if isinstance(e, events.RetryBlocked)]
    assert len(blocked_events) == 2
    assert all(e.attempts == 3 for e in blocked_events)
