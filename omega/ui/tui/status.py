"""The single-line status bar above the input."""
from __future__ import annotations

from dataclasses import dataclass

from textual.widgets import Static

from ... import events

# Advanced every 80ms via `set_interval` while the turn is not idle.
SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


@dataclass(frozen=True)
class StatusState:
    mode: str
    role_name: str
    model: str
    session_id: str
    turns: int
    usage: events.Usage | None
    alias: str | None = None
    phase: str = "idle"


def _fmt_tokens(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def _fmt_usage(usage: events.Usage | None) -> str:
    if usage is None:
        return "–"
    pct = (usage.used / usage.limit * 100) if usage.limit else 0.0
    return f"{_fmt_tokens(usage.used)}/{_fmt_tokens(usage.limit)} ({pct:.0f}%)"


def phase_indicator(phase: str, frame: int) -> str:
    if phase == "idle" or not phase:
        return "[dim]●[/dim]"
    glyph = SPINNER_FRAMES[frame % len(SPINNER_FRAMES)]
    return f"[dim]{glyph} {phase}[/dim]"


def format_status(state: StatusState, *, width: int | None = None) -> str:
    tokens = _fmt_usage(state.usage)
    model = f"{state.alias} · {state.model}" if state.alias else state.model
    head = f" {state.mode} · {model} · tokens {tokens}"
    full = f"{head} · {state.session_id} · turns {state.turns}"
    if width is None or len(full) <= width:
        return full
    # Narrow terminal: drop the session id first, then turns, then the model
    # id -- the alias alone still says which model is in use.
    no_session = f"{head} · turns {state.turns}"
    if len(no_session) <= width:
        return no_session
    if len(head) <= width:
        return head
    alias_only = f" {state.mode} · {state.alias or state.model} · tokens {tokens}"
    return alias_only if len(alias_only) <= width else alias_only[:width]


class StatusBar(Static):
    DEFAULT_CSS = """
    StatusBar {
        height: 1;
        background: $boost;
        color: $text-muted;
        padding: 0 1;
    }
    """

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._state: StatusState | None = None
        self._frame = 0

    def on_mount(self) -> None:
        self.set_interval(0.08, self._tick)

    def _tick(self) -> None:
        if self._state is not None and self._state.phase != "idle":
            self._frame += 1
            self._repaint()

    def set_state(self, state: StatusState) -> None:
        self._state = state
        self._repaint()

    def _repaint(self) -> None:
        if self._state is None:
            return
        width = self.size.width or None
        body = format_status(self._state, width=width - 12 if width else None)
        self.update(f"{phase_indicator(self._state.phase, self._frame)} {body}")
