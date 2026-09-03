"""The single-line status bar above the input."""
from __future__ import annotations

from dataclasses import dataclass

from textual.widgets import Static

from ... import events


@dataclass(frozen=True)
class StatusState:
    mode: str
    role_name: str
    model: str
    session_id: str
    turns: int
    usage: events.Usage | None


def _fmt_tokens(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def _fmt_usage(usage: events.Usage | None) -> str:
    if usage is None:
        return "–"
    pct = (usage.used / usage.limit * 100) if usage.limit else 0.0
    return f"{_fmt_tokens(usage.used)}/{_fmt_tokens(usage.limit)} ({pct:.0f}%)"


def format_status(state: StatusState) -> str:
    tokens = _fmt_usage(state.usage)
    return (f" {state.mode} · {state.role_name} {state.model} · "
            f"tokens {tokens} · {state.session_id} · turns {state.turns}")


class StatusBar(Static):
    DEFAULT_CSS = """
    StatusBar {
        height: 1;
        background: $boost;
        color: $text-muted;
        padding: 0 1;
    }
    """

    def set_state(self, state: StatusState) -> None:
        self.update(format_status(state))
