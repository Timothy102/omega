"""The scrolling conversation pane. Renders the dim one-liners via
`ui/format.py`, which both front ends share so they cannot drift on wording
(plain.py's tests print through a live `rich.Console` and are left untouched)."""
from __future__ import annotations

from rich.markdown import Markdown
from textual.containers import VerticalScroll
from textual.widgets import Static

from ... import events
from .. import format


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
        self._append(format.tool_start(ev))

    def add_tool_end(self, ev: events.ToolEnd) -> None:
        text = format.tool_end(ev)
        if text is not None:
            self._append(text)

    def add_subagent_spawned(self, ev: events.SubagentSpawned) -> None:
        self._append(format.subagent_spawned(ev))

    def add_subagent_done(self, ev: events.SubagentDone) -> None:
        self._append(format.subagent_done(ev))

    def add_compacted(self, ev: events.Compacted) -> None:
        self._append(format.compacted(ev))

    def add_memory_write(self, ev: events.MemoryWrite) -> None:
        self._append(format.memory_write(ev))

    def add_memory_consolidated(self, ev: events.MemoryConsolidated) -> None:
        self._append(format.memory_consolidated(ev))

    def add_error(self, ev: events.Error) -> None:
        self._append(format.error(ev))

    def add_mode_switch(self, mode: str) -> None:
        self._append(f"[dim]mode: {mode}[/dim]")

    def add_resumed(self, session_id: str, turns: int, messages: int, cwd: str) -> None:
        self._append(f"[dim]resumed {session_id} — {turns} turns, {messages} messages · {cwd}[/dim]")

    def add_dim(self, text: str) -> None:
        self._append(f"[dim]{text}[/dim]")
