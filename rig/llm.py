import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal

from openai import AsyncOpenAI

from .config import Role
from .session import Message

StreamEvent = tuple[Literal["text"], str] | tuple[Literal["tool"], "ToolCall"] | tuple[Literal["done"], "Turn"]


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

    def as_message(self) -> Message:
        msg: Message = {"role": "assistant", "content": self.text or None}
        if self.tool_calls:
            msg["tool_calls"] = [
                {"id": c.id, "type": "function",
                 "function": {"name": c.name, "arguments": c.arguments or "{}"}}
                for c in self.tool_calls
            ]
        return msg


_clients: dict[str, AsyncOpenAI] = {}


def client_for(role: Role) -> AsyncOpenAI:
    p = role.provider
    if p.name not in _clients:
        _clients[p.name] = AsyncOpenAI(base_url=p.base_url, api_key=p.api_key)
    return _clients[p.name]


async def stream(role: Role, messages: list[Message],
                 tools: list[dict[str, Any]] | None = None) -> AsyncIterator[StreamEvent]:
    """Yield ('text', delta) and ('tool', ToolCall) events.

    A tool call is emitted the moment its arguments are known to be complete --
    when a higher-indexed call begins, or when the stream ends -- so the caller
    can start executing it while the model is still generating the next one.
    """
    kwargs: dict[str, Any] = {"model": role.model, "messages": messages, "stream": True,
                              "stream_options": {"include_usage": True}}
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    pending: dict[int, ToolCall] = {}
    turn = Turn()
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
