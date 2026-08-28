import json

import pytest

from conftest import Chunk, tc
from rig import llm


async def drain(chunks, role=None):
    """Run llm.stream against a canned chunk sequence."""
    class FakeCompletions:
        async def create(self, **kw):
            async def gen():
                for c in chunks:
                    yield c
            return gen()

    class FakeClient:
        chat = type("X", (), {"completions": FakeCompletions()})()

    llm._clients["fake"] = FakeClient()
    role = role or type("R", (), {"model": "m", "provider": type("P", (), {"name": "fake"})()})()
    events = []
    async for kind, payload in llm.stream(role, [{"role": "user", "content": "x"}]):
        events.append((kind, payload))
    return events


@pytest.mark.asyncio
async def test_tool_calls_assembled_across_chunks():
    events = await drain([
        Chunk(tool_calls=[tc(0, "a", "re", '{"pa')]),
        Chunk(tool_calls=[tc(0, None, "ad", 'th":"x"}')]),
        Chunk(tool_calls=[tc(1, "b", "glob", '{"pattern":"*.py"}')]),
        Chunk(finish_reason="tool_calls"),
    ])
    calls = [p for k, p in events if k == "tool"]
    assert [c.name for c in calls] == ["read", "glob"]
    assert calls[0].args() == {"path": "x"}
    turn = [p for k, p in events if k == "done"][0]
    assert len(turn.tool_calls) == 2 and turn.finish_reason == "tool_calls"


@pytest.mark.asyncio
async def test_call_emitted_before_stream_ends():
    """The whole point of streaming dispatch: call 0 fires before the end."""
    events = await drain([
        Chunk(tool_calls=[tc(0, "a", "read", '{"path":"x"}')]),
        Chunk(tool_calls=[tc(1, "b", "read", '{"path":"y"}')]),
        Chunk(finish_reason="tool_calls"),
    ])
    kinds = [k for k, _ in events]
    assert kinds.index("tool") < kinds.index("done")


@pytest.mark.asyncio
async def test_usage_chunk_is_captured_despite_empty_choices():
    """Regression: the usage chunk has no choices and was silently dropped."""
    usage = type("U", (), {"prompt_tokens": 111, "completion_tokens": 7,
                           "prompt_tokens_details": type("D", (), {"cached_tokens": 99})()})()
    events = await drain([Chunk(content="hi"), Chunk(finish_reason="stop"),
                          Chunk(usage=usage)])
    turn = [p for k, p in events if k == "done"][0]
    assert turn.prompt_tokens == 111 and turn.cached_tokens == 99


@pytest.mark.asyncio
async def test_incomplete_call_dropped_on_length_finish():
    """Truncated arguments must never be dispatched or written to history."""
    events = await drain([
        Chunk(tool_calls=[tc(0, "a", "bash", '{"command":"ec')]),
        Chunk(finish_reason="length"),
    ])
    assert [p for k, p in events if k == "tool"] == []
    turn = [p for k, p in events if k == "done"][0]
    assert turn.tool_calls == [] and "truncated" in turn.text
