"""The scrolling conversation pane. Renders the dim one-liners via
`ui/format.py`, which both front ends share so they cannot drift on wording
(plain.py's tests print through a live `rich.Console` and are left untouched).

Tool-call lines fold their `ToolEnd` outcome in place instead of adding a
second line, and a model round or subagent that dispatches more than a
handful of tool calls collapses the rest behind a `… +N more` line."""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from rich.markdown import Markdown
from textual.containers import VerticalScroll
from textual.events import Click
from textual.widgets import Static

from ... import events
from .. import format
from .status import SPINNER_FRAMES

_GROUP_CAP = 3


@dataclass
class _Group:
    shown: int = 0
    total: int = 0
    hidden: list[events.ToolStart] = field(default_factory=list)
    more: Static | None = None
    expanded: bool = False


class _MoreLine(Static):
    def __init__(self, markup: str, on_click: Callable[[], None]) -> None:
        super().__init__(markup)
        self._click_cb = on_click

    def on_click(self, event: Click) -> None:
        self._click_cb()


class _NewOutputPill(Static):
    DEFAULT_CSS = """
    _NewOutputPill {
        dock: bottom;
        width: 100%;
        height: 1;
        content-align: right middle;
        background: $panel;
        color: $text-muted;
        display: none;
    }
    """

    def __init__(self, on_click: Callable[[], None]) -> None:
        super().__init__("[dim]↓ new output[/dim]")
        self._click_cb = on_click

    def on_click(self, event: Click) -> None:
        self._click_cb()


class Transcript(VerticalScroll):
    DEFAULT_CSS = """
    Transcript {
        width: 3fr;
        border-right: solid $panel;
        padding: 0 1;
    }
    """
    BINDINGS = []

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._live_assistant: Static | None = None
        self._live_text: str = ""
        self._session_id: str = ""

        self._thinking_widget: Static | None = None
        self._thinking_timer: object | None = None
        self._thinking_frame = 0

        self._top_group: _Group | None = None
        self._sub_groups: dict[str, _Group] = {}
        self._sub_spawn_widget: dict[str, Static] = {}
        self._sub_spawn_base: dict[str, str] = {}
        self._sub_start_time: dict[str, float] = {}
        self._active_subagents: set[str] = set()

        self._call_widget: dict[str, Static] = {}
        self._call_base: dict[str, str] = {}
        self._buffered_end: dict[str, events.ToolEnd] = {}
        self._last_sig: tuple[str, str, str | None] | None = None
        self._last_widget: Static | None = None
        self._last_base: str = ""
        self._last_repeat = 1
        self._latest_group: _Group | None = None

        self._pill = _NewOutputPill(self.jump_to_end)

    def on_mount(self) -> None:
        self.mount(self._pill)

    def set_session(self, session_id: str) -> None:
        self._session_id = session_id

    def jump_to_end(self) -> None:
        self.scroll_end(animate=False)
        self._pill.display = False

    def _append(self, markup: str) -> Static:
        at_bottom = self.scroll_y >= self.max_scroll_y - 1
        line = Static(markup)
        self.mount(line)
        if at_bottom:
            self.scroll_end(animate=False)
            self._pill.display = False
        else:
            self._pill.display = True
        return line

    # ---- thinking spinner (Part A5) ----------------------------------------

    def set_thinking(self, active: bool) -> None:
        if active:
            if self._thinking_widget is None:
                self._thinking_frame = 0
                self._thinking_widget = self._append(self._thinking_text())
                self._thinking_timer = self.set_interval(0.08, self._tick_thinking)
            return
        if self._thinking_widget is not None:
            self._thinking_widget.remove()
            self._thinking_widget = None
        if self._thinking_timer is not None:
            self._thinking_timer.stop()  # type: ignore[attr-defined]
            self._thinking_timer = None

    def _thinking_text(self) -> str:
        glyph = SPINNER_FRAMES[self._thinking_frame % len(SPINNER_FRAMES)]
        return f"[dim]{glyph} thinking…[/dim]"

    def _tick_thinking(self) -> None:
        self._thinking_frame += 1
        if self._thinking_widget is not None:
            self._thinking_widget.update(self._thinking_text())

    def note_phase(self, state: str) -> None:
        if state == "waiting":
            self._top_group = None
        self.set_thinking(state == "thinking")

    # ---- turn text ----------------------------------------------------------

    def add_user_message(self, text: str, mode: str) -> None:
        self._append("")
        self._append(f"[bold]›[/bold] {text}   [dim]{mode}[/dim]")

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
        self._live_assistant.update(Markdown(text, code_theme="monokai"))
        self._live_assistant = None
        self._live_text = ""

    # ---- tool calls (C4/C7/C9) -----------------------------------------------

    def _group_for(self, key: str | None) -> _Group:
        if key is None:
            if self._top_group is None:
                self._top_group = _Group()
            return self._top_group
        return self._sub_groups.setdefault(key, _Group())

    def _more_text(self, group: _Group) -> str:
        return f"  [dim]… +{group.total - group.shown} more (▸ to expand)[/dim]"

    def add_tool_start(self, ev: events.ToolStart) -> None:
        if ev.name == "subagent":
            return
        key = ev.subagent_id
        sig = (ev.name, ev.args_preview, key)
        if self._last_sig == sig and self._last_widget is not None:
            self._last_repeat += 1
            self._last_widget.update(f"{self._last_base}  [dim]×{self._last_repeat}[/dim]")
            self._call_widget[ev.call_id] = self._last_widget
            self._call_base[ev.call_id] = self._last_base
            return

        group = self._group_for(key)
        group.total += 1
        if group.shown < _GROUP_CAP:
            group.shown += 1
            show_suffix = key is None or len(self._active_subagents) > 1
            markup = format.tool_start(ev, show_subagent_suffix=show_suffix)
            widget = self._append(markup)
            self._call_widget[ev.call_id] = widget
            self._call_base[ev.call_id] = markup
            self._last_sig, self._last_widget, self._last_base, self._last_repeat = sig, widget, markup, 1
        else:
            group.hidden.append(ev)
            self._last_sig = None
            self._last_widget = None
            if group.more is None:
                group.more = self._append_more(group)
            else:
                group.more.update(self._more_text(group))
            self._latest_group = group

    def _append_more(self, group: _Group) -> Static:
        at_bottom = self.scroll_y >= self.max_scroll_y - 1
        line = _MoreLine(self._more_text(group), lambda: self._expand_group(group))
        self.mount(line)
        if at_bottom:
            self.scroll_end(animate=False)
        return line

    def _expand_group(self, group: _Group) -> None:
        if group.expanded or not group.hidden:
            return
        group.expanded = True
        for start_ev in group.hidden:
            show_suffix = start_ev.subagent_id is None or len(self._active_subagents) > 1
            markup = format.tool_start(start_ev, show_subagent_suffix=show_suffix)
            widget = self._append(markup)
            self._call_widget[start_ev.call_id] = widget
            self._call_base[start_ev.call_id] = markup
            end_ev = self._buffered_end.pop(start_ev.call_id, None)
            if end_ev is not None and end_ev.outcome:
                widget.update(f"{markup}  [dim]{end_ev.outcome}[/dim]")
        if group.more is not None:
            group.more.remove()
            group.more = None
        group.hidden.clear()

    def expand_latest(self) -> None:
        if self._latest_group is not None:
            self._expand_group(self._latest_group)

    def add_tool_end(self, ev: events.ToolEnd) -> None:
        if ev.name == "subagent":
            return
        widget = self._call_widget.get(ev.call_id)
        if widget is None:
            self._buffered_end[ev.call_id] = ev
            return
        if not ev.outcome:
            return
        base = self._call_base.get(ev.call_id, "")
        widget.update(f"{base}  [dim]{ev.outcome}[/dim]")

    def add_subagent_spawned(self, ev: events.SubagentSpawned) -> None:
        self._active_subagents.add(ev.subagent_id)
        self._top_group = None
        markup = format.subagent_spawned(ev)
        widget = self._append(markup)
        self._sub_spawn_widget[ev.subagent_id] = widget
        self._sub_spawn_base[ev.subagent_id] = markup
        self._sub_start_time[ev.subagent_id] = time.monotonic()

    def add_subagent_done(self, ev: events.SubagentDone) -> None:
        self._active_subagents.discard(ev.subagent_id)
        widget = self._sub_spawn_widget.pop(ev.subagent_id, None)
        base = self._sub_spawn_base.pop(ev.subagent_id, None)
        started = self._sub_start_time.pop(ev.subagent_id, None)
        if widget is None or base is None:
            return
        elapsed = time.monotonic() - started if started is not None else 0.0
        widget.update(f"{base}  [dim]✓ {elapsed:.0f}s[/dim]")

    def add_compacted(self, ev: events.Compacted) -> None:
        self._append(format.compacted(ev))

    def add_memory_write(self, ev: events.MemoryWrite) -> None:
        self._append(format.memory_write(ev))

    def add_memory_consolidated(self, ev: events.MemoryConsolidated) -> None:
        self._append(format.memory_consolidated(ev))

    def add_error(self, ev: events.Error) -> None:
        first_line = ev.message.splitlines()[0] if ev.message else ""
        detail = f" [dim](details in ~/.rig/sessions/{self._session_id}.json)[/dim]" if self._session_id else ""
        self._append(f"[red]error:[/red] {first_line}{detail}")

    def add_mode_switch(self, mode: str) -> None:
        self._append(f"[dim]mode: {mode}[/dim]")

    def add_resumed(self, session_id: str, turns: int, messages: int, cwd: str, ago: str = "") -> None:
        suffix = f", last active {ago} ago" if ago else ""
        self._append(f"[dim]resumed {session_id} — {turns} turns, {messages} messages{suffix} · {cwd}[/dim]")

    def add_dim(self, text: str) -> None:
        self._append(f"[dim]{text}[/dim]")
