from . import tools

CFG = None

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
    history = [{"role": "user", "content": task}]
    return await run_agent(CFG, role, SYSTEM, history,
                           tools.READ_ONLY - {"subagent"}, max_rounds=12)
