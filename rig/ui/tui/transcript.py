"""The scrolling conversation pane. Mirrors `ui/plain.render`'s wording for the
dim one-liners so the two front ends read the same, without sharing plain.py's
console-printing code (plain.py's tests print through a live `rich.Console`
and are left untouched)."""
from __future__ import annotations

from rich.markdown import Markdown
from textual.containers import VerticalScroll
from textual.widgets import Static

from ... import events


def _tool_start_text(ev: events.ToolStart) -> str:
    if ev.subagent_id:
        return f"  [dim]⏺ {ev.name}  {ev.args_preview}  ({ev.tier}·{ev.subagent_id})[/dim]"
    return f"[dim]⏺ {ev.name}[/dim] [dim italic]{ev.args_preview}[/dim italic]"


def _tool_end_text(ev: events.ToolEnd) -> str | None:
    if not ev.offloaded:
        return None
    return f"  [dim]↳ offloaded → artifact {ev.artifact_id}[/dim]"


def _subagent_spawned_text(ev: events.SubagentSpawned) -> str:
    return f"[dim]⏺ subagent({ev.tier}) {ev.task_preview}  [{ev.subagent_id}][/dim]"


def _subagent_done_text(ev: events.SubagentDone) -> str:
    return f"  [dim]✓ {ev.subagent_id} done[/dim]"


def _compacted_text(ev: events.Compacted) -> str:
    return f"[dim]⏺ {ev.note}[/dim]"


def _memory_write_text(ev: events.MemoryWrite) -> str:
    return f"  [dim]◆ memory: {ev.type} '{ev.title}' ({ev.scope})[/dim]"


def _memory_consolidated_text(ev: events.MemoryConsolidated) -> str:
    return f"  [dim]◆ memory: {ev.summary}[/dim]"


def _error_text(ev: events.Error) -> str:
    return f"[red]error:[/red] {ev.message}"


class Transcript(VerticalScroll):
    DEFAULT_CSS = """
    Transcript {
        width: 3fr;
        border-right: solid $panel;
        padding: 0 1;
    }
    """

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._live_assistant: Static | None = None
        self._live_text: str = ""

    def _append(self, markup: str) -> Static:
        at_bottom = self.scroll_y >= self.max_scroll_y - 1
        line = Static(markup)
        self.mount(line)
        if at_bottom:
            self.scroll_end(animate=False)
        return line

    def add_user_message(self, text: str) -> None:
        self._append(f"[bold]› {text}[/bold]")

    def add_text_delta(self, text: str) -> None:
        if self._live_assistant is None:
            self._live_text = ""
            self._live_assistant = self._append("")
        self._live_text += text
        self._live_assistant.update(self._live_text)

    def finalize_turn(self, text: str) -> None:
        text = text or self._live_text
        if not text:
            return
        if self._live_assistant is None:
            self._live_assistant = self._append("")
        self._live_assistant.update(Markdown(text))
        self._live_assistant = None
        self._live_text = ""

    def add_tool_start(self, ev: events.ToolStart) -> None:
        self._append(_tool_start_text(ev))

    def add_tool_end(self, ev: events.ToolEnd) -> None:
        text = _tool_end_text(ev)
        if text is not None:
            self._append(text)

    def add_subagent_spawned(self, ev: events.SubagentSpawned) -> None:
        self._append(_subagent_spawned_text(ev))

    def add_subagent_done(self, ev: events.SubagentDone) -> None:
        self._append(_subagent_done_text(ev))

    def add_compacted(self, ev: events.Compacted) -> None:
        self._append(_compacted_text(ev))

    def add_memory_write(self, ev: events.MemoryWrite) -> None:
        self._append(_memory_write_text(ev))

    def add_memory_consolidated(self, ev: events.MemoryConsolidated) -> None:
        self._append(_memory_consolidated_text(ev))

    def add_error(self, ev: events.Error) -> None:
        self._append(_error_text(ev))

    def add_mode_switch(self, mode: str) -> None:
        self._append(f"[dim]mode: {mode}[/dim]")

    def add_resumed(self, session_id: str, turns: int, messages: int, cwd: str) -> None:
        self._append(f"[dim]resumed {session_id} — {turns} turns, {messages} messages · {cwd}[/dim]")

    def add_dim(self, text: str) -> None:
        self._append(f"[dim]{text}[/dim]")
