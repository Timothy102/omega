import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

from .config import Role
from .session import Message

PhaseState = Literal["thinking", "streaming"]
StreamEvent = (tuple[Literal["text"], str] | tuple[Literal["tool"], "ToolCall"]
              | tuple[Literal["done"], "Turn"] | tuple[Literal["phase"], PhaseState])

# The array-form fallback header would also work, but the scalar "default" mode
# picks Anthropic's recommended fallback per refusal category instead of
# pinning one model -- see shared/model-migration.md -> New API features.
_ANTHROPIC_BETAS = ["server-side-fallback-2026-07-01"]
_ANTHROPIC_MAX_TOKENS = 64000


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str = ""

    def args(self) -> dict[str, Any]:
        try:
            return dict(json.loads(self.arguments or "{}"))
        except json.JSONDecodeError as e:
            raise ValueError(f"bad arguments for {self.name}: {e}") from None


@dataclass
class Turn:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    thinking: list[dict[str, Any]] = field(default_factory=list)
    model: str = ""

    def as_message(self) -> Message:
        msg: Message = {"role": "assistant", "content": self.text or None}
        if self.tool_calls:
            msg["tool_calls"] = [
                {"id": c.id, "type": "function",
                 "function": {"name": c.name, "arguments": c.arguments or "{}"}}
                for c in self.tool_calls
            ]
        if self.thinking:
            msg["thinking"] = self.thinking
        if self.model:
            msg["model"] = self.model
        return msg


_clients: dict[str, AsyncOpenAI] = {}
_anthropic_clients: dict[str, AsyncAnthropic] = {}


def client_for(role: Role) -> AsyncOpenAI:
    p = role.provider
    if p.name not in _clients:
        _clients[p.name] = AsyncOpenAI(base_url=p.base_url, api_key=p.api_key)
    return _clients[p.name]


def _anthropic_client_for(role: Role) -> AsyncAnthropic:
    p = role.provider
    if p.name not in _anthropic_clients:
        kwargs: dict[str, Any] = {"api_key": p.api_key}
        if p.base_url:
            kwargs["base_url"] = p.base_url
        _anthropic_clients[p.name] = AsyncAnthropic(**kwargs)
    return _anthropic_clients[p.name]


async def stream(role: Role, messages: list[Message],
                 tools: list[dict[str, Any]] | None = None) -> AsyncIterator[StreamEvent]:
    """Yield ('text', delta) and ('tool', ToolCall) events.

    A tool call is emitted the moment its arguments are known to be complete --
    when a higher-indexed call begins, or when the stream ends -- so the caller
    can start executing it while the model is still generating the next one.

    Dispatches to the OpenAI-compatible or native Anthropic backend based on
    `role.provider.type` (defaults to "openai" for callers -- tests included --
    that build a bare provider stand-in without a `type` attribute).
    """
    if getattr(role.provider, "type", "openai") == "anthropic":
        async for ev in _stream_anthropic(role, messages, tools):
            yield ev
        return
    async for ev in _stream_openai(role, messages, tools):
        yield ev


async def _stream_openai(role: Role, messages: list[Message],
                         tools: list[dict[str, Any]] | None) -> AsyncIterator[StreamEvent]:
    # `thinking`/`model` are Anthropic-only bookkeeping stamped by as_message();
    # some OpenAI-compatible providers hard-reject unrecognized message keys.
    sent = [{k: v for k, v in m.items() if k not in ("thinking", "model")} for m in messages]
    kwargs: dict[str, Any] = {"model": role.model, "messages": sent, "stream": True,
                              "stream_options": {"include_usage": True}}
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    pending: dict[int, ToolCall] = {}
    turn = Turn(model=role.model)
    emitted: set[int] = set()

    async for chunk in await client_for(role).chat.completions.create(**kwargs):
        # The usage-bearing chunk arrives last with an EMPTY choices array, so
        # it must be read before the choices guard or it is silently discarded.
        if getattr(chunk, "usage", None):
            u = chunk.usage
            turn.prompt_tokens = getattr(u, "prompt_tokens", 0) or 0
            turn.completion_tokens = getattr(u, "completion_tokens", 0) or 0
            details = getattr(u, "prompt_tokens_details", None)
            turn.cached_tokens = (getattr(details, "cached_tokens", 0) or 0) if details else 0
        if not chunk.choices:
            continue
        choice = chunk.choices[0]
        delta = choice.delta

        if delta and delta.content:
            turn.text += delta.content
            yield "text", delta.content

        for tc in (delta.tool_calls or []) if delta else []:
            # A provider that reuses an index for a new call would otherwise
            # concatenate two names into one ("greplread").
            existing = pending.get(tc.index)
            if (existing is not None and tc.id and existing.id
                    and tc.id != existing.id and not existing.id.startswith("call_")):
                if tc.index not in emitted:
                    emitted.add(tc.index)
                    turn.tool_calls.append(existing)
                    yield "tool", existing
                del pending[tc.index]
            if tc.index not in pending:
                for done in sorted(i for i in pending if i < tc.index and i not in emitted):
                    emitted.add(done)
                    turn.tool_calls.append(pending[done])
                    yield "tool", pending[done]
                pending[tc.index] = ToolCall(id=tc.id or f"call_{tc.index}", name="")
            call = pending[tc.index]
            if tc.id:
                call.id = tc.id
            if tc.function and tc.function.name:
                call.name += tc.function.name
            if tc.function and tc.function.arguments:
                call.arguments += tc.function.arguments

        if choice.finish_reason:
            turn.finish_reason = choice.finish_reason

    incomplete: list[str] = []
    for i in sorted(i for i in pending if i not in emitted):
        call = pending[i]
        # finish_reason "length" means generation was cut off mid-call: its
        # arguments are truncated JSON and must never be dispatched or stored.
        if turn.finish_reason == "length" and not _parseable(call.arguments):
            incomplete.append(call.name or f"index {i}")
            continue
        turn.tool_calls.append(call)
        yield "tool", call

    if incomplete:
        turn.text += (f"\n[turn truncated by token limit; dropped incomplete "
                      f"tool call(s): {', '.join(incomplete)}]")

    yield "done", turn


def _parseable(args: str) -> bool:
    try:
        json.loads(args or "{}")
        return True
    except json.JSONDecodeError:
        return False


def _anthropic_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for t in tools:
        fn = t.get("function", t)
        out.append({"name": fn["name"], "description": fn.get("description", ""),
                    "input_schema": fn.get("parameters") or {"type": "object", "properties": {}}})
    return out


def _anthropic_history(history: list[Message], role: Role) -> list[dict[str, Any]]:
    """Convert rig's OpenAI-shaped history (after the leading system message)
    into Messages API turns. Consecutive `tool` messages collapse into one
    user message holding all their `tool_result` blocks -- the API rejects a
    `tool_result` that isn't inside a user message immediately following the
    `tool_use` turn. Thinking blocks are only replayed on the model that
    produced them (see the module docstring in compact.py for why)."""
    out: list[dict[str, Any]] = []
    i = 0
    while i < len(history):
        m = history[i]
        role_name = m.get("role")
        if role_name == "user":
            out.append({"role": "user", "content": [{"type": "text", "text": str(m.get("content") or "")}]})
            i += 1
        elif role_name == "assistant":
            content: list[dict[str, Any]] = []
            if m.get("model") == role.model:
                content.extend(m.get("thinking") or [])
            if m.get("content"):
                content.append({"type": "text", "text": str(m["content"])})
            for tc in m.get("tool_calls") or []:
                fn = tc["function"]
                try:
                    call_input = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    call_input = {}
                content.append({"type": "tool_use", "id": tc["id"], "name": fn["name"], "input": call_input})
            out.append({"role": "assistant", "content": content})
            i += 1
        elif role_name == "tool":
            blocks = []
            while i < len(history) and history[i].get("role") == "tool":
                t = history[i]
                blocks.append({"type": "tool_result", "tool_use_id": t["tool_call_id"],
                               "content": str(t.get("content") or "")})
                i += 1
            out.append({"role": "user", "content": blocks})
        else:
            i += 1
    return out


async def _stream_anthropic(role: Role, messages: list[Message],
                            tools: list[dict[str, Any]] | None) -> AsyncIterator[StreamEvent]:
    system_text = str(messages[0].get("content") or "") if messages and messages[0].get("role") == "system" else ""
    body = messages[1:] if messages and messages[0].get("role") == "system" else messages
    anth_messages = _anthropic_history(body, role)
    if anth_messages and anth_messages[-1]["content"]:
        # Breakpoint on the last block of the last turn: every earlier
        # breakpoint stays a valid read, so hits accrue as history grows.
        anth_messages[-1]["content"][-1] = {
            **anth_messages[-1]["content"][-1], "cache_control": {"type": "ephemeral"}}

    kwargs: dict[str, Any] = {
        "model": role.model,
        "max_tokens": _ANTHROPIC_MAX_TOKENS,
        "system": [{"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}],
        "messages": anth_messages,
        "betas": _ANTHROPIC_BETAS,
        "fallbacks": "default",
    }
    if tools:
        kwargs["tools"] = _anthropic_tools(tools)
    # Fable 5.1 thinking is always on and rejects any explicit `thinking` config
    # (including "adaptive"); every other claude-* model needs it spelled out.
    if not role.model.startswith("claude-fable") and role.model.startswith("claude"):
        kwargs["thinking"] = {"type": "adaptive"}
    if role.effort:
        kwargs["output_config"] = {"effort": role.effort}

    turn = Turn(model=role.model)
    pending_tools: dict[int, ToolCall] = {}
    in_thinking = False

    client = _anthropic_client_for(role)
    async with client.beta.messages.stream(**kwargs) as anthropic_stream:
        async for event in anthropic_stream:
            if event.type == "content_block_start":
                if event.content_block.type in ("thinking", "redacted_thinking"):
                    if not in_thinking:
                        in_thinking = True
                        yield "phase", "thinking"
                else:
                    if in_thinking:
                        in_thinking = False
                        yield "phase", "streaming"
                    if event.content_block.type == "tool_use":
                        pending_tools[event.index] = ToolCall(
                            id=event.content_block.id, name=event.content_block.name)
            elif event.type == "content_block_delta":
                if event.delta.type == "thinking_delta" and not in_thinking:
                    in_thinking = True
                    yield "phase", "thinking"
                elif event.delta.type == "text_delta":
                    if in_thinking:
                        in_thinking = False
                        yield "phase", "streaming"
                    turn.text += event.delta.text
                    yield "text", event.delta.text
                elif event.delta.type == "input_json_delta":
                    call = pending_tools.get(event.index)
                    if call is not None:
                        call.arguments += event.delta.partial_json
            elif event.type == "content_block_stop" and event.index in pending_tools:
                call = pending_tools.pop(event.index)
                turn.tool_calls.append(call)
                yield "tool", call
        final = await anthropic_stream.get_final_message()

    turn.prompt_tokens = final.usage.input_tokens
    turn.completion_tokens = final.usage.output_tokens
    turn.cached_tokens = final.usage.cache_read_input_tokens or 0
    turn.thinking = [b.model_dump() for b in final.content if b.type in ("thinking", "redacted_thinking")]

    if final.stop_reason == "max_tokens":
        turn.finish_reason = "length"
    elif final.stop_reason == "tool_use":
        turn.finish_reason = "tool_calls"
    elif final.stop_reason == "refusal":
        turn.finish_reason = "refusal"
        note = "\n[request declined by the model's safety classifiers"
        served_by = getattr(final, "model", "") or ""
        if served_by and served_by != role.model:
            note += f"; fallback model {served_by} was tried but also declined"
        turn.text += note + "]"
    else:
        turn.finish_reason = final.stop_reason or ""

    yield "done", turn
