"""Pure text formatting for the dim one-liners both UIs render for the same
events (`ToolStart`/`ToolEnd`/`SubagentSpawned`/`SubagentDone`/`Compacted`/
`MemoryWrite`/`MemoryConsolidated`/`Error`). Shared here so `ui/plain.py` and
`ui/tui/transcript.py` cannot drift on wording. Callers own presentation
concerns these functions don't: `ui/plain.py`'s leading blank-line spacing is
added at the call site, not here.
"""
from __future__ import annotations

from .. import events


def tool_start(ev: events.ToolStart) -> str:
    if ev.subagent_id:
        return f"  [dim]⏺ {ev.name}  {ev.args_preview}  ({ev.tier}·{ev.subagent_id})[/dim]"
    return f"[dim]⏺ {ev.name}[/dim] [dim italic]{ev.args_preview}[/dim italic]"


def tool_end(ev: events.ToolEnd) -> str | None:
    if not ev.offloaded:
        return None
    return f"  [dim]↳ offloaded → artifact {ev.artifact_id}[/dim]"


def subagent_spawned(ev: events.SubagentSpawned) -> str:
    return f"[dim]⏺ subagent({ev.tier}) {ev.task_preview}  [{ev.subagent_id}][/dim]"


def subagent_done(ev: events.SubagentDone) -> str:
    return f"  [dim]✓ {ev.subagent_id} done[/dim]"


def compacted(ev: events.Compacted) -> str:
    return f"[dim]⏺ {ev.note}[/dim]"


def memory_write(ev: events.MemoryWrite) -> str:
    return f"  [dim]◆ memory: {ev.type} '{ev.title}' ({ev.scope})[/dim]"


def memory_consolidated(ev: events.MemoryConsolidated) -> str:
    return f"  [dim]◆ memory: {ev.summary}[/dim]"


def error(ev: events.Error) -> str:
    return f"[red]error:[/red] {ev.message}"
