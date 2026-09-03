import json
from typing import cast

from . import llm
from .config import Config
from .llm import Turn
from .session import Message

FRACTION = 0.75
KEEP_LAST = 6

SYSTEM = """Summarise this conversation excerpt for an agent that will keep working.

Preserve: what was asked, what was done, file paths touched, decisions made,
errors hit and how they were resolved, and anything still outstanding.
Drop: raw file contents, long command output, and restated code.

Write dense prose. No preamble."""


def estimate_tokens(messages: list[Message]) -> int:
    return sum(len(json.dumps(m)) for m in messages) // 4


def safe_split(history: list[Message], keep_last: int) -> int:
    """Return an index to cut at that never separates an assistant message
    carrying tool_calls from the tool results that answer it.

    Only the position immediately before a user message is safe, because tool
    results always follow their assistant message inside a turn.
    """
    if len(history) <= keep_last:
        return 0
    for i in range(max(1, len(history) - keep_last), 0, -1):
        m = history[i]
        # Safe boundaries: a user message, or an assistant message with no
        # tool_calls (its group is complete). Cutting anywhere else orphans a
        # tool_call_id. Assistant boundaries matter because an agentic turn has
        # exactly ONE user message -- at index 0, which can never be a cut.
        if m.get("role") == "user":
            return i
        if m.get("role") == "assistant" and not m.get("tool_calls"):
            return i
    return 0


async def maybe_compact(cfg: Config, history: list[Message], used: int, limit: int,
                        fraction: float = FRACTION, keep_last: int = KEEP_LAST) -> str | None:
    """Shrink history in place. Returns a note if it compacted, else None."""
    if limit <= 0 or used < limit * fraction:
        return None

    cut = safe_split(history, keep_last)
    if cut == 0:
        return None

    older, recent = history[:cut], history[cut:]
    transcript = "\n\n".join(
        f"[{m.get('role')}] {str(m.get('content'))[:1500]}" for m in older
        if m.get("content"))

    role = cfg.role("compact") if "compact" in cfg.roles else cfg.role("main")
    summary = ""
    async for kind, payload in llm.stream(
            role, [{"role": "system", "content": SYSTEM},
                   {"role": "user", "content": transcript}]):
        if kind == "done":
            summary = cast(Turn, payload).text

    if not summary.strip():
        return None

    history[:] = [{"role": "user",
                   "content": f"[Summary of earlier conversation]\n{summary}"},
                  *recent]
    return f"compacted {len(older)} messages → summary"
