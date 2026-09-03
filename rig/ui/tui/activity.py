"""The right-hand panel of in-flight tool calls and subagents."""
from __future__ import annotations

import time
from dataclasses import dataclass

from textual.containers import VerticalScroll
from textual.widgets import Static

from ... import events

_IDLE_TEXT = "[dim]idle[/dim]"


@dataclass
class _Row:
    label: str
    start: float
    widget: Static


def _tool_label(ev: events.ToolStart) -> str:
    base = f"{ev.name}  {ev.args_preview}"
    return f"  {base}" if ev.subagent_id else base


def _subagent_label(ev: events.SubagentSpawned) -> str:
    return f"subagent({ev.tier}) {ev.task_preview}"


class ActivityPanel(VerticalScroll):
    DEFAULT_CSS = """
    ActivityPanel {
        width: 1fr;
        padding: 0 1;
        background: $panel;
    }
    """

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._rows: dict[str, _Row] = {}
        self._idle: Static = Static(_IDLE_TEXT)

    def on_mount(self) -> None:
        self.mount(self._idle)
        self.set_interval(1, self._tick)

    def _sync_idle(self) -> None:
        self._idle.display = not self._rows

    def _add(self, key: str, label: str) -> None:
        widget = Static(label)
        self._rows[key] = _Row(label=label, start=time.monotonic(), widget=widget)
        self.mount(widget)
        self._sync_idle()

    def _remove(self, key: str) -> None:
        row = self._rows.pop(key, None)
        if row is not None:
            row.widget.remove()
        self._sync_idle()

    def start_tool(self, ev: events.ToolStart) -> None:
        self._add(ev.call_id, _tool_label(ev))

    def end_tool(self, ev: events.ToolEnd) -> None:
        self._remove(ev.call_id)

    def start_subagent(self, ev: events.SubagentSpawned) -> None:
        self._add(ev.subagent_id, _subagent_label(ev))

    def end_subagent(self, ev: events.SubagentDone) -> None:
        self._remove(ev.subagent_id)

    def _tick(self) -> None:
        now = time.monotonic()
        for row in self._rows.values():
            elapsed = int(now - row.start)
            row.widget.update(f"{row.label}  [dim]{elapsed}s[/dim]")
