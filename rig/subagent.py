import secrets

from . import events, tools

CFG = None
# Set by run_turn at the start of every turn, mirroring tools.set_tainted(False).
EMIT = None

TIERS = {"fast": "subagent_fast", "mid": "subagent_mid"}

SYSTEM = """You are a research subagent. You have read-only tools.

Investigate and answer the task concretely -- real paths, real line numbers, real
symbol names. Do not speculate; if you could not determine something, say so.

Your reply is the ONLY thing the orchestrator sees, so it must stand alone. Be
dense and short: no preamble, no restating the task, no offers to help further."""


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
async def _subagent(task, tier="fast"):
    from .loop import run_agent
    if CFG is None:
        return "error: subagent not wired"
    role = TIERS.get(tier, "subagent_fast")
    subagent_id = secrets.token_hex(3)
    emit = EMIT or (lambda _e: None)

    def forward(ev):
        # The subagent's prose is its return value, not transcript text --
        # only its tool activity is visible upward.
        if not isinstance(ev, events.TextDelta | events.Done):
            emit(ev)

    emit(events.SubagentSpawned(subagent_id=subagent_id, tier=tier,
                                task_preview=" ".join(task.split())[:80]))
    history = [{"role": "user", "content": task}]
    result = await run_agent(CFG, role, SYSTEM, history,
                             tools.READ_ONLY - {"subagent"}, emit=forward,
                             max_rounds=12, subagent_id=subagent_id, tier=tier)
    emit(events.SubagentDone(subagent_id=subagent_id,
                             summary_preview=" ".join(result.split())[:120]))
    return result
