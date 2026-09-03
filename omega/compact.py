import json
from typing import cast

from . import llm, trajectory
from .config import Config
from .llm import Turn
from .session import Message

FRACTION = 0.75
KEEP_LAST = 6

# Cap on what the summariser LLM ever sees: the transcript already truncates
# each message to 1500 chars, but a long-running turn can still pile up far
# more messages than that keeps sane for one summarisation call.
_COMPACT_SUMMARY_INPUT_CHARS = 40_000

# Per-line cap for the deterministic ledger appended alongside the LLM
# summary -- generous compared to the live trajectory block's 64/120 char
# fields, since this is the durable record of a range that is about to be
# dropped from history for good.
_COMPACT_ENTRY_CHARS = 1000

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
        if m.get("content"))[:_COMPACT_SUMMARY_INPUT_CHARS]

    role = cfg.role("compact") if "compact" in cfg.roles else cfg.role("main")
    summary = ""
    async for kind, payload in llm.stream(
            role, [{"role": "system", "content": SYSTEM},
                   {"role": "user", "content": transcript}]):
        if kind == "done":
            summary = cast(Turn, payload).text

    if not summary.strip():
        return None

    # Anthropic's "preserved thinking" check binds a Claude Fable 5.1 thinking
    # block's signature to the exact conversation prefix that produced it;
    # summarising `older` into one message is itself a history edit, which
    # invalidates every thinking block still carried by `recent`. Dropping the
    # blocks (unbilled, and explicitly sanctioned as the no-beta recovery path)
    # is simpler and safer than keeping them alive across a rewritten prefix --
    # the model just answers the next turn without that carried-over reasoning.
    for m in recent:
        m.pop("thinking", None)

    ledger_lines = trajectory.compaction_lines(older, _COMPACT_ENTRY_CHARS)
    ledger_body = "\n".join(ledger_lines) if ledger_lines else "(no tool calls in this range)"

    # The ledger is its own message, never folded into the summary message
    # above: mutating an already-emitted message's content would invalidate
    # the Anthropic prompt cache for everything after it, while appending a
    # brand-new message only ever extends the cached prefix.
    history[:] = [
        {"role": "user", "content": f"[Summary of earlier conversation]\n{summary}"},
        {"role": "user", "content": f"[Action ledger for dropped range]\n{ledger_body}"},
        *recent,
    ]
    return f"compacted {len(older)} messages → summary"
