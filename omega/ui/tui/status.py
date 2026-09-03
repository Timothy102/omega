"""The chrome line below the input: status cells folded in with the key
hints, per the reference terminal-agent layout (Part R7) -- there is no
separate status bar above the input any more."""
from __future__ import annotations

from dataclasses import dataclass

from textual.widgets import Static

from ... import events
from .. import format as ui_format

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


def _fmt_usage(usage: events.Usage | None) -> str:
    """`10.4k/1.0M` -- `ui_format.fmt_num` switches to the `M` scale above a
    million, unlike a bare `n/1000` division that would print `1048.6k`."""
    if usage is None:
        return "–"
    return f"{ui_format.fmt_num(usage.used)}/{ui_format.fmt_num(usage.limit)}"


def _fmt_cache(usage: events.Usage | None) -> str:
    """`cache 87%` = cache_read / prompt_tokens for the last turn -- omitted
    entirely (not just zeroed) when there was nothing to read from cache."""
    if usage is None or usage.cache_read <= 0 or usage.prompt_tokens <= 0:
        return ""
    pct = usage.cache_read / usage.prompt_tokens * 100
    return f" · [dim]cache {pct:.0f}%[/dim]"


HINT_TEXT = "ctrl+b panel · ctrl+o model · shift+tab mode · /help"


def format_status(state: StatusState, *, width: int | None = None) -> str:
    tokens = _fmt_usage(state.usage)
    cache = _fmt_cache(state.usage)
    model = f"{state.alias} · {state.model}" if state.alias else state.model
    full = f" {state.mode} · {model} · {tokens}{cache}"
    if width is None or len(full) <= width:
        return full
    # Narrow terminal: drop the raw model id first -- the alias alone still
    # says which model is in use.
    alias_only = f" {state.mode} · {state.alias or state.model} · {tokens}{cache}"
    return alias_only if len(alias_only) <= width else alias_only[:width]


class StatusBar(Static):
    """One dim chrome line: status cells on the left, key hints on the
    right -- the live phase/spinner now lives in the transcript's own
    running-turn line (Part R5), not duplicated here."""

    DEFAULT_CSS = """
    StatusBar {
        height: 1;
        color: $text-muted;
        padding: 0 1;
    }
    """

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._state: StatusState | None = None

    def set_state(self, state: StatusState) -> None:
        self._state = state
        self._repaint()

    def _repaint(self) -> None:
        if self._state is None:
            return
        width = self.size.width or 78
        full = format_status(self._state, width=None).strip()
        # The status cells always win the space fight -- the hint text is a
        # nice-to-have that only shows up once there's room left over for it.
        if ui_format.visible_len(full) + len(HINT_TEXT) + 3 <= width:
            self.update(ui_format.right_align(f"[dim]{full}[/dim]", f"[dim]{HINT_TEXT}[/dim]", width))
            return
        narrowed = format_status(self._state, width=width).strip()
        self.update(f"[dim]{narrowed}[/dim]")
