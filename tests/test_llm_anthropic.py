from dataclasses import dataclass, field
from typing import Any

import pytest

from rig import llm
from rig.config import Provider, Role


def make_role(model: str = "claude-opus-5", effort: str | None = "high") -> Role:
    provider = Provider(name="fake-anthropic", type="anthropic", api_key_literal="test-key")
    return Role(model=model, provider=provider, context=1_000_000, effort=effort, alias="opus")


# ---- history / schema conversion -------------------------------------------------

def test_tool_calls_convert_to_tool_use_blocks():
    history = [
        {"role": "user", "content": "read x"},
        {"role": "assistant", "content": "",
         "tool_calls": [{"id": "call_1", "type": "function",
                         "function": {"name": "read", "arguments": '{"path":"x"}'}}]},
    ]
    out = llm._anthropic_history(history, make_role())
    assert out[0] == {"role": "user", "content": [{"type": "text", "text": "read x"}]}
    assert out[1]["role"] == "assistant"
    assert out[1]["content"] == [{"type": "tool_use", "id": "call_1", "name": "read", "input": {"path": "x"}}]


def test_consecutive_tool_results_collapse_into_one_user_message():
    history = [
        {"role": "tool", "tool_call_id": "call_1", "content": "result one"},
        {"role": "tool", "tool_call_id": "call_2", "content": "result two"},
    ]
    out = llm._anthropic_history(history, make_role())
    assert len(out) == 1
    assert out[0]["role"] == "user"
    assert out[0]["content"] == [
        {"type": "tool_result", "tool_use_id": "call_1", "content": "result one"},
        {"type": "tool_result", "tool_use_id": "call_2", "content": "result two"},
    ]


def test_thinking_replayed_only_for_matching_model():
    thinking_blocks = [{"type": "thinking", "thinking": "reasoning...", "signature": "sig"}]
    history = [{"role": "assistant", "content": "done", "thinking": thinking_blocks,
               "model": "claude-opus-5"}]

    same_model = llm._anthropic_history(history, make_role(model="claude-opus-5"))
    assert same_model[0]["content"][0] == thinking_blocks[0]

    other_model = llm._anthropic_history(history, make_role(model="claude-sonnet-5"))
    assert all(b.get("type") != "thinking" for b in other_model[0]["content"])


def test_tool_schema_conversion():
    openai_tools = [{"type": "function", "function": {
        "name": "read", "description": "Read a file.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}},
                       "required": ["path"]}}}]
    out = llm._anthropic_tools(openai_tools)
    assert out == [{"name": "read", "description": "Read a file.",
                    "input_schema": {"type": "object", "properties": {"path": {"type": "string"}},
                                     "required": ["path"]}}]


# ---- streamed round-trip against a fake AsyncAnthropic client --------------------

@dataclass
class FakeBlock:
    type: str
    id: str = ""
    name: str = ""
    thinking_text: str = ""
    signature: str = ""

    def model_dump(self) -> dict[str, Any]:
        if self.type == "thinking":
            return {"type": "thinking", "thinking": self.thinking_text, "signature": self.signature}
        return {"type": self.type}


@dataclass
class FakeDelta:
    type: str
    text: str = ""
    partial_json: str = ""


@dataclass
class FakeEvent:
    type: str
    index: int = 0
    content_block: Any = None
    delta: Any = None


@dataclass
class FakeUsage:
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int


@dataclass
class FakeFinalMessage:
    content: list[Any]
    usage: FakeUsage
    stop_reason: str | None
    model: str


class FakeAnthropicStream:
    def __init__(self, events: list[FakeEvent], final: FakeFinalMessage) -> None:
        self._events = events
        self._final = final

    async def __aenter__(self) -> "FakeAnthropicStream":
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False

    async def __aiter__(self) -> Any:
        for e in self._events:
            yield e

    async def get_final_message(self) -> FakeFinalMessage:
        return self._final


@dataclass
class FakeBetaMessages:
    stream_obj: FakeAnthropicStream
    last_kwargs: dict[str, Any] = field(default_factory=dict)

    def stream(self, **kwargs: Any) -> FakeAnthropicStream:
        self.last_kwargs = kwargs
        return self.stream_obj


class FakeAnthropicClient:
    def __init__(self, stream_obj: FakeAnthropicStream) -> None:
        self.beta = type("Beta", (), {"messages": FakeBetaMessages(stream_obj)})()


def make_fake_client() -> FakeAnthropicClient:
    events = [
        FakeEvent(type="content_block_start", index=0, content_block=FakeBlock("text")),
        FakeEvent(type="content_block_delta", index=0, delta=FakeDelta(type="text_delta", text="Hello ")),
        FakeEvent(type="content_block_delta", index=0, delta=FakeDelta(type="text_delta", text="world")),
        FakeEvent(type="content_block_stop", index=0),
        FakeEvent(type="content_block_start", index=1,
                 content_block=FakeBlock("tool_use", id="toolu_1", name="read")),
        FakeEvent(type="content_block_delta", index=1,
                 delta=FakeDelta(type="input_json_delta", partial_json='{"pa')),
        FakeEvent(type="content_block_delta", index=1,
                 delta=FakeDelta(type="input_json_delta", partial_json='th":"x"}')),
        FakeEvent(type="content_block_stop", index=1),
    ]
    final = FakeFinalMessage(
        content=[
            FakeBlock("text"),
            FakeBlock("tool_use"),
            FakeBlock("thinking", thinking_text="reasoning...", signature="sig-abc"),
        ],
        usage=FakeUsage(input_tokens=50, output_tokens=20, cache_read_input_tokens=10),
        stop_reason="tool_use",
        model="claude-opus-5",
    )
    return FakeAnthropicClient(FakeAnthropicStream(events, final))


@pytest.mark.asyncio
async def test_streamed_round_trip(monkeypatch):
    role = make_role()
    fake_client = make_fake_client()
    monkeypatch.setitem(llm._anthropic_clients, role.provider.name, fake_client)

    messages = [{"role": "system", "content": "be terse"},
               {"role": "user", "content": "read x please"}]
    tools = [{"type": "function", "function": {
        "name": "read", "description": "Read a file.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}}]

    received: list[tuple[str, Any]] = []
    async for kind, payload in llm.stream(role, messages, tools):
        received.append((kind, payload))

    assert [k for k, _ in received] == ["text", "text", "tool", "done"]

    text_parts = [p for k, p in received if k == "text"]
    assert text_parts == ["Hello ", "world"]

    call = next(p for k, p in received if k == "tool")
    assert call.id == "toolu_1" and call.name == "read"
    assert call.args() == {"path": "x"}

    turn = next(p for k, p in received if k == "done")
    assert turn.text == "Hello world"
    assert turn.prompt_tokens == 50 and turn.completion_tokens == 20 and turn.cached_tokens == 10
    assert turn.finish_reason == "tool_calls"
    assert turn.model == "claude-opus-5"
    assert turn.thinking == [{"type": "thinking", "thinking": "reasoning...", "signature": "sig-abc"}]

    kwargs = fake_client.beta.messages.last_kwargs
    assert kwargs["model"] == "claude-opus-5"
    assert kwargs["betas"] == ["server-side-fallback-2026-07-01"]
    assert kwargs["fallbacks"] == "default"
    assert kwargs["output_config"] == {"effort": "high"}
    assert kwargs["thinking"] == {"type": "adaptive"}
    assert kwargs["system"] == [{"type": "text", "text": "be terse",
                                 "cache_control": {"type": "ephemeral"}}]
    last_block = kwargs["messages"][-1]["content"][-1]
    assert last_block["cache_control"] == {"type": "ephemeral"}


@pytest.mark.asyncio
async def test_refusal_maps_finish_reason_and_appends_note(monkeypatch):
    role = make_role()
    final = FakeFinalMessage(content=[], usage=FakeUsage(0, 0, 0),
                             stop_reason="refusal", model="claude-opus-5")
    fake_client = FakeAnthropicClient(FakeAnthropicStream([], final))
    monkeypatch.setitem(llm._anthropic_clients, role.provider.name, fake_client)

    messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
    received = [ev async for ev in llm.stream(role, messages)]
    turn = next(p for k, p in received if k == "done")
    assert turn.finish_reason == "refusal"
    assert "declined" in turn.text


@pytest.mark.asyncio
async def test_thinking_block_yields_phase_events_around_it(monkeypatch):
    role = make_role()
    events_seq = [
        FakeEvent(type="content_block_start", index=0, content_block=FakeBlock("thinking")),
        FakeEvent(type="content_block_delta", index=0,
                 delta=FakeDelta(type="thinking_delta", text="mulling it over...")),
        FakeEvent(type="content_block_stop", index=0),
        FakeEvent(type="content_block_start", index=1, content_block=FakeBlock("text")),
        FakeEvent(type="content_block_delta", index=1,
                 delta=FakeDelta(type="text_delta", text="Hello")),
        FakeEvent(type="content_block_stop", index=1),
    ]
    final = FakeFinalMessage(
        content=[FakeBlock("thinking", thinking_text="mulling it over...", signature="sig"),
                FakeBlock("text")],
        usage=FakeUsage(input_tokens=10, output_tokens=5, cache_read_input_tokens=0),
        stop_reason="end_turn", model="claude-opus-5")
    fake_client = FakeAnthropicClient(FakeAnthropicStream(events_seq, final))
    monkeypatch.setitem(llm._anthropic_clients, role.provider.name, fake_client)

    messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
    received = [ev async for ev in llm.stream(role, messages)]

    kinds = [k for k, _ in received]
    assert kinds == ["phase", "phase", "text", "done"]
    assert received[0] == ("phase", "thinking")
    assert received[1] == ("phase", "streaming")
    assert received[2] == ("text", "Hello")


@pytest.mark.asyncio
async def test_fable_model_omits_thinking_param(monkeypatch):
    role = make_role(model="claude-fable-5-1", effort="xhigh")
    fake_client = make_fake_client()
    monkeypatch.setitem(llm._anthropic_clients, role.provider.name, fake_client)

    messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
    async for _ in llm.stream(role, messages):
        pass

    kwargs = fake_client.beta.messages.last_kwargs
    assert "thinking" not in kwargs
    assert kwargs["output_config"] == {"effort": "xhigh"}
