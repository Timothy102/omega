import secrets
from collections.abc import Callable

from . import events, tools
from .config import Config
from .session import Message

CFG: Config | None = None
# Set by run_turn at the start of every turn, mirroring tools.set_tainted(False).
EMIT: Callable[[events.Event], None] | None = None

TIERS = {"fast": "subagent_fast", "mid": "subagent_mid", "verify": "subagent_mid"}

SYSTEM = """You are a research subagent. You have read-only tools.

Investigate and answer the task concretely -- real paths, real line numbers, real
symbol names. Do not speculate; if you could not determine something, say so.

Your reply is the ONLY thing the orchestrator sees, so it must stand alone. Be
dense and short: no preamble, no restating the task, no offers to help further."""

REVIEW_SYSTEM = """You are a verification subagent reviewing a just-completed
BUILD-mode turn. You have read-only tools.

You are given the user's original request and a diff of the changes made to
address it. Check the diff for: correctness against the ask, cases the diff
misses, and leftover debug code (stray prints, commented-out code, TODO
markers left by mistake). Read surrounding files with your tools when the
diff alone doesn't tell you enough.

Reply with exactly "OK" if the change looks right. Otherwise reply with a
short numbered list of concrete issues -- no preamble, no restating the diff."""


@tools.tool(
    "subagent",
    "Delegate a bounded read-only research task to a smaller, faster model. "
    "Returns a dense summary instead of raw tool output, so use it for wide "
    "searches, multi-file reading, and 'where is X' questions. "
    "tier: 'fast' for lookups and greps, 'mid' for reasoning across files.",
    {"task": {"type": "string"},
     "tier": {"type": "string", "enum": ["fast", "mid"]}},
    ["task"],
)
async def _subagent(task: str, tier: str = "fast") -> str:
    from .loop import run_agent
    if CFG is None:
        return "error: subagent not wired"
    role = TIERS.get(tier, "subagent_fast")
    subagent_id = secrets.token_hex(3)
    emit = EMIT or (lambda _e: None)

    def forward(ev: events.Event) -> None:
        # The subagent's prose is its return value, not transcript text --
        # only its tool activity is visible upward. ModelUsed is also
        # swallowed: it names the subagent's own tier model and would
        # otherwise stomp the top-level turn's status-bar model on emit.
        # Phase is swallowed too -- the subagent's own waiting/thinking/
        # streaming churn is not the top-level turn's phase; the parent
        # stays in "tools" for the whole duration of the subagent call.
        if not isinstance(ev, events.TextDelta | events.Done | events.ModelUsed | events.Phase):
            emit(ev)

    emit(events.SubagentSpawned(subagent_id=subagent_id, tier=tier,
                                task_preview=" ".join(task.split())[:80]))
    history: list[Message] = [{"role": "user", "content": task}]
    result = await run_agent(CFG, role, SYSTEM, history,
                             tools.READ_ONLY - {"subagent"}, emit=forward,
                             max_rounds=12, subagent_id=subagent_id, tier=tier)
    emit(events.SubagentDone(subagent_id=subagent_id,
                             summary_preview=" ".join(result.split())[:120]))
    return result


async def review(cfg: Config, request: str, diff_text: str,
                 emit: Callable[[events.Event], None] | None = None) -> str:
    """Invoked automatically by loop.run_agent at the end of a BUILD turn
    that made a non-trivial change (see loop.py) -- not reachable from the
    `subagent` tool's tier enum, which only offers "fast"/"mid" to the model.
    Returns "OK" or a numbered list of issues; never raises."""
    from .loop import run_agent
    emit = emit or (lambda _e: None)
    subagent_id = secrets.token_hex(3)

    def forward(ev: events.Event) -> None:
        if not isinstance(ev, events.TextDelta | events.Done | events.ModelUsed | events.Phase):
            emit(ev)

    emit(events.SubagentSpawned(subagent_id=subagent_id, tier="verify",
                                task_preview=" ".join(request.split())[:80]))
    task = f"Original request:\n{request}\n\nDiff of changes made this turn:\n{diff_text}"
    history: list[Message] = [{"role": "user", "content": task}]
    result = await run_agent(cfg, TIERS["verify"], REVIEW_SYSTEM, history,
                             tools.READ_ONLY - {"subagent"}, emit=forward,
                             max_rounds=6, subagent_id=subagent_id, tier="verify")
    emit(events.SubagentDone(subagent_id=subagent_id,
                             summary_preview=" ".join(result.split())[:120]))
    return result
