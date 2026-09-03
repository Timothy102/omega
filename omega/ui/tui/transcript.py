"""The scrolling conversation pane. Renders the dim one-liners via
`ui/format.py`, which both front ends share so they cannot drift on wording
(plain.py's tests print through a live `rich.Console` and are left untouched).

Layout follows the reference terminal-agent look: each top-level block (user
prompt, assistant reply, a burst of tool calls, a subagent) is separated from
its neighbors by one blank line; a tool's outcome (or a subagent's nested
calls) renders as a dim `└ ...` sub-line beneath its block instead of a second
top-level line; and a single live status line docked to the bottom of the
pane tracks the running turn (phase, elapsed time, tokens, thinking time)
instead of per-row spinners."""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from rich.console import RenderableType
from rich.markdown import Markdown
from rich.table import Table
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.events import Click, Resize
from textual.timer import Timer
from textual.widgets import Static

from ... import events
from .. import format
from .status import SPINNER_FRAMES

_GROUP_CAP = 3
_ERROR_ROW_CHARS = 200


def _bulleted(body: RenderableType) -> Table:
    """`body` with a leading `●` gutter -- a plain string prefix like
    `"●  text"` wraps flush-left, losing the bullet's visual separation the
    moment a sentence overruns one line; a 3-col grid column keeps every
    wrapped continuation line indented under the text, not the bullet."""
    grid = Table.grid()
    grid.add_column(width=3)
    grid.add_column(ratio=1)
    grid.add_row("[bold]●[/bold]  ", body)
    return grid


def _flatten_truncate(text: str, limit: int) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


@dataclass
class _Group:
    is_sub: bool = False
    shown: int = 0
    total: int = 0
    hidden: list[events.ToolStart] = field(default_factory=list)
    more: Static | None = None
    expanded: bool = False


class _MoreLine(Static, can_focus=True):
    """A collapsed-group continuation line. Real focusable row: enter or a
    click expands it, same as any other interactive control."""

    DEFAULT_CSS = """
    _MoreLine { height: 1; }
    _MoreLine:focus { text-style: underline; }
    """
    BINDINGS = [Binding("enter", "activate", show=False)]

    def __init__(self, markup: str, on_click: Callable[[], None]) -> None:
        super().__init__(markup)
        self._click_cb = on_click

    def on_click(self, event: Click) -> None:
        self._click_cb()

    def action_activate(self) -> None:
        self._click_cb()


class _ErrorLine(Static, can_focus=True):
    """A tool's `└ error: …` sub-line, truncated to `_ERROR_ROW_CHARS` --
    focusable, expands to the full (still-wrapped) error text on enter/click,
    mirroring `_MoreLine`'s one-way collapsed-group expansion."""

    DEFAULT_CSS = """
    _ErrorLine { height: auto; }
    _ErrorLine:focus { text-style: underline; }
    """
    BINDINGS = [Binding("enter", "activate", show=False)]

    def __init__(self, short: str, full: str) -> None:
        super().__init__(short)
        self._full = full
        self._expanded = False

    def on_click(self, event: Click) -> None:
        self.action_activate()

    def action_activate(self) -> None:
        if self._expanded:
            return
        self._expanded = True
        self.update(self._full)


class _EmptyState(Static):
    DEFAULT_CSS = """
    _EmptyState {
        width: 100%;
        height: auto;
        content-align: center middle;
        color: $text-muted;
        padding: 2 0;
    }
    """

    def __init__(self) -> None:
        super().__init__(
            "[bold]⌘ omega[/bold]\n"
            "[dim]ask anything about this repo · /discuss to think together · "
            "/plan to plan[/dim]")


class _PromptBand(Static):
    """The turn-opening user-prompt row: a full-width subtle background band,
    no border -- `add_user_message` builds the left/right-aligned text."""

    DEFAULT_CSS = """
    _PromptBand { width: 100%; background: $surface-lighten-1; padding: 0 2; }
    """


class _LiveStatus(Static):
    """The single running-turn indicator, docked to the pane's bottom edge so
    it always stays below newly streamed content regardless of mount order."""

    DEFAULT_CSS = """
    _LiveStatus { dock: bottom; width: 100%; height: 1; padding: 0 1; }
    """


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
        width: 1fr;
        min-width: 40;
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

        self._empty_state: _EmptyState | None = None
        self._last_block: str | None = None

        # live status footer (Part R5)
        self._status_widget: Static | None = None
        self._status_timer: Timer | None = None
        self._status_frame = 0
        self._phase = "idle"
        self._turn_started: float | None = None
        self._thinking_seconds = 0.0
        self._thinking_entered: float | None = None
        self._active_tool_count = 0
        self._last_usage: events.Usage | None = None

        self._top_group: _Group | None = None
        self._sub_groups: dict[str, _Group] = {}
        self._sub_spawn_widget: dict[str, Static] = {}
        self._sub_task_text: dict[str, str] = {}
        self._sub_start_time: dict[str, float] = {}
        self._active_subagents: set[str] = set()

        self._call_widget: dict[str, Static] = {}
        self._call_base: dict[str, str] = {}
        # The `ToolStart` (and whether its subagent suffix was shown) behind
        # each currently-visible row's markup, kept so `on_resize` can rebuild
        # that markup against the pane's new width instead of leaving stale
        # truncation baked in from whatever width was current when the row
        # was first mounted.
        self._call_ev: dict[str, events.ToolStart] = {}
        self._call_suffix: dict[str, bool] = {}
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

    def _mount_widget(self, widget: Static) -> Static:
        at_bottom = self.scroll_y >= self.max_scroll_y - 1
        self.mount(widget)
        if at_bottom:
            self.scroll_end(animate=False)
            self._pill.display = False
        else:
            self._pill.display = True
        return widget

    def _append(self, markup: str) -> Static:
        return self._mount_widget(Static(markup))

    def _ensure_gap(self, kind: str) -> None:
        """One blank line between blocks of a different kind -- never between
        consecutive lines of the same block (a tool-call burst, a run of dim
        status lines)."""
        if self._last_block is not None and self._last_block != kind:
            self._append("")
        self._last_block = kind

    # ---- empty state (Part R10) ---------------------------------------------

    def show_empty_state(self) -> None:
        self._empty_state = _EmptyState()
        self.mount(self._empty_state)

    def _hide_empty_state(self) -> None:
        if self._empty_state is not None:
            self._empty_state.remove()
            self._empty_state = None

    # ---- live status footer (Part R5) ---------------------------------------

    def note_usage(self, ev: events.Usage) -> None:
        self._last_usage = ev
        self._refresh_status_now()

    def _verb(self) -> str:
        if self._active_tool_count > 0:
            n = self._active_tool_count
            return f"Running {n} tool{'' if n == 1 else 's'}…"
        if self._active_subagents:
            return "Waiting for subagent…"
        if self._phase == "streaming":
            return "Writing…"
        return "Thinking…"

    def _status_text(self) -> str:
        frame = SPINNER_FRAMES[self._status_frame % len(SPINNER_FRAMES)]
        elapsed = time.monotonic() - self._turn_started if self._turn_started else 0.0
        thinking = self._thinking_seconds
        if self._thinking_entered is not None:
            thinking += time.monotonic() - self._thinking_entered
        tokens = (self._last_usage.completion_tokens if self._last_usage is not None
                 else round(len(self._live_text) / 4))
        bits = [f"{elapsed:.0f}s"]
        if tokens:
            bits.append(f"↓ {format.fmt_num(tokens)} tokens")
        if thinking >= 1:
            bits.append(f"thought for {thinking:.0f}s")
        return (f"[$accent]{frame}[/$accent] [$accent]{self._verb()}[/$accent] "
               f"[dim]({' · '.join(bits)})[/dim]")

    def _refresh_status_now(self) -> None:
        if self._status_widget is not None:
            self._status_widget.update(self._status_text())

    def _tick_status(self) -> None:
        self._status_frame += 1
        self._refresh_status_now()

    def _ensure_status(self) -> None:
        if self._status_widget is None:
            self._status_widget = _LiveStatus(self._status_text())
            self.mount(self._status_widget)
            self._status_timer = self.set_interval(0.08, self._tick_status)

    def _stop_status(self) -> None:
        if self._status_timer is not None:
            self._status_timer.stop()
            self._status_timer = None
        if self._status_widget is not None:
            self._status_widget.remove()
            self._status_widget = None

    def note_phase(self, state: str) -> None:
        now = time.monotonic()
        if self._phase == "thinking" and state != "thinking" and self._thinking_entered is not None:
            self._thinking_seconds += now - self._thinking_entered
            self._thinking_entered = None
        if state == "thinking" and self._thinking_entered is None:
            self._thinking_entered = now
        if state == "waiting":
            self._top_group = None
        if state == "idle":
            self._phase = state
            self._stop_status()
            self._turn_started = None
            self._thinking_seconds = 0.0
            self._thinking_entered = None
            self._active_tool_count = 0
            self._last_usage = None
            return
        if self._turn_started is None:
            self._turn_started = now
        self._phase = state
        self._ensure_status()
        self._refresh_status_now()

    # ---- turn text ------------------------------------------------------------

    def add_user_message(self, text: str, mode: str) -> None:
        self._hide_empty_state()
        self._ensure_gap("user")
        # `content_size` excludes this widget's own padding/border, unlike
        # `size` (the border box) -- `_PromptBand`'s own `padding: 0 2` is the
        # remaining `-4` to subtract for the text-safe width inside it.
        width = (self.content_size.width or 74) - 4
        left = f"[bold]›[/bold] {format.esc(text)}  [dim]{mode}[/dim]"
        ts = time.strftime("%H:%M")
        self._mount_widget(_PromptBand(format.right_align(left, f"[dim]{ts}[/dim]", width)))

    def add_text_delta(self, text: str) -> None:
        if self._live_assistant is None:
            self._ensure_gap("assistant")
            self._live_text = ""
            self._live_assistant = self._append("")
        self._live_text += text
        # Model text is data, not markup -- a stray "[/x]" must never raise.
        self._live_assistant.update(_bulleted(format.esc(self._live_text)))

    def _close_text_block(self) -> None:
        """Ends the CURRENT round's prose block by rendering it as Markdown in
        place, so a `ToolStart`/`SubagentSpawned` between two rounds of text
        never lets the second round's deltas keep appending onto the first
        round's already-mounted widget (which is what put round-2 prose above
        the tool calls that produced it, and ran its sentences together with
        round 1's)."""
        if self._live_assistant is None:
            return
        self._live_assistant.update(_bulleted(Markdown(self._live_text, code_theme="monokai")))
        self._live_assistant = None
        self._live_text = ""

    def finalize_turn(self, text: str) -> None:
        self._stop_status()
        text = text or self._live_text
        if not text:
            return
        if self._live_assistant is None:
            self._ensure_gap("assistant")
            self._live_assistant = self._append("")
        self._live_assistant.update(_bulleted(Markdown(text, code_theme="monokai")))
        self._live_assistant = None
        self._live_text = ""

    # ---- tool calls (C4/C7/C9, Part R2/R3) -------------------------------------

    def _group_for(self, key: str | None) -> _Group:
        if key is None:
            if self._top_group is None:
                self._top_group = _Group(is_sub=False)
            return self._top_group
        return self._sub_groups.setdefault(key, _Group(is_sub=True))

    def _more_text(self, group: _Group) -> str:
        remaining = group.total - group.shown
        lead = "  [dim]└ …" if group.is_sub else "  [dim]…"
        return f"{lead} +{remaining} more (▸ to expand)[/dim]"

    def _detail_width(self) -> int | None:
        # `content_size` already excludes this widget's own padding/border --
        # unlike the old `self.size.width` (the border box), it doesn't need
        # a guessed-at fudge factor to avoid overflow from double-counting
        # them. The remaining `-24` budget covers the row's own chrome: the
        # bullet/indent, the (never-truncated) name column for the handful of
        # tool names longer than `pad_name`'s 12-column default, and a small
        # margin of safety.
        w = self.content_size.width
        return max(10, w - 24) if w else None

    def on_resize(self, event: Resize) -> None:
        self._retruncate_tool_rows()

    def _retruncate_tool_rows(self) -> None:
        """Rebuilds every currently-visible tool row's markup against the
        pane's new width -- without this, a row's `truncate_middle` cutoff
        stays fixed at whatever width was current when it was first mounted,
        so shrinking the pane (a live resize, or the sidebar opening) leaves
        rows wrapping across extra lines instead of re-truncating to fit, and
        growing it back leaves them stuck short of what would now fit."""
        width = self._detail_width()
        for call_id, widget in self._call_widget.items():
            ev = self._call_ev.get(call_id)
            if ev is None:
                continue
            show_suffix = self._call_suffix.get(call_id, True)
            markup = format.tool_start(ev, show_subagent_suffix=show_suffix, width=width)
            self._call_base[call_id] = markup
            if widget is self._last_widget and self._last_repeat > 1:
                self._last_base = markup
                widget.update(f"{markup}  [dim]×{self._last_repeat}[/dim]")
            else:
                widget.update(markup)

    def _outcome_line(self, outcome: str, *, full: bool = False) -> str:
        is_error = outcome.startswith("→ error")
        sub = outcome[2:] if outcome.startswith("→ ") else outcome
        if is_error and not full:
            sub = _flatten_truncate(sub, _ERROR_ROW_CHARS)
        color = "red" if is_error else "dim"
        return f"  [{color}]└ {format.esc(sub)}[/{color}]"

    def _mount_outcome(self, outcome: str) -> None:
        if not outcome.startswith("→ error"):
            self._append(self._outcome_line(outcome))
            return
        short = self._outcome_line(outcome)
        full = self._outcome_line(outcome, full=True)
        self._mount_widget(_ErrorLine(short, full))

    def add_tool_start(self, ev: events.ToolStart) -> None:
        if ev.name == "subagent":
            return
        self._close_text_block()
        self._active_tool_count += 1
        self._refresh_status_now()
        key = ev.subagent_id
        sig = (ev.name, ev.args_preview, key)
        if self._last_sig == sig and self._last_widget is not None:
            self._last_repeat += 1
            self._last_widget.update(f"{self._last_base}  [dim]×{self._last_repeat}[/dim]")
            self._call_widget[ev.call_id] = self._last_widget
            self._call_base[ev.call_id] = self._last_base
            self._call_ev[ev.call_id] = ev
            self._call_suffix[ev.call_id] = key is None or len(self._active_subagents) > 1
            return

        group = self._group_for(key)
        group.total += 1
        if group.shown < _GROUP_CAP:
            group.shown += 1
            if key is None:
                self._ensure_gap("tools")
            show_suffix = key is None or len(self._active_subagents) > 1
            markup = format.tool_start(ev, show_subagent_suffix=show_suffix,
                                       width=self._detail_width())
            widget = self._append(markup)
            self._call_widget[ev.call_id] = widget
            self._call_base[ev.call_id] = markup
            self._call_ev[ev.call_id] = ev
            self._call_suffix[ev.call_id] = show_suffix
            self._last_sig, self._last_widget, self._last_base, self._last_repeat = sig, widget, markup, 1
        else:
            group.hidden.append(ev)
            self._last_sig = None
            self._last_widget = None
            if group.more is None:
                if key is None:
                    self._ensure_gap("tools")
                group.more = self._append_more(group)
            else:
                group.more.update(self._more_text(group))
            self._latest_group = group

    def _append_more(self, group: _Group) -> Static:
        line = _MoreLine(self._more_text(group), lambda: self._expand_group(group))
        return self._mount_widget(line)

    def _expand_group(self, group: _Group) -> None:
        if group.expanded or not group.hidden:
            return
        group.expanded = True
        for start_ev in group.hidden:
            show_suffix = start_ev.subagent_id is None or len(self._active_subagents) > 1
            markup = format.tool_start(start_ev, show_subagent_suffix=show_suffix,
                                       width=self._detail_width())
            widget = self._append(markup)
            self._call_widget[start_ev.call_id] = widget
            self._call_base[start_ev.call_id] = markup
            self._call_ev[start_ev.call_id] = start_ev
            self._call_suffix[start_ev.call_id] = show_suffix
            end_ev = self._buffered_end.pop(start_ev.call_id, None)
            if end_ev is not None and end_ev.outcome:
                self._mount_outcome(end_ev.outcome)
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
        self._active_tool_count = max(0, self._active_tool_count - 1)
        self._refresh_status_now()
        widget = self._call_widget.get(ev.call_id)
        if widget is None:
            self._buffered_end[ev.call_id] = ev
            return
        if not ev.outcome:
            return
        self._mount_outcome(ev.outcome)

    def add_subagent_spawned(self, ev: events.SubagentSpawned) -> None:
        self._close_text_block()
        self._active_subagents.add(ev.subagent_id)
        self._sub_task_text[ev.subagent_id] = ev.task_preview
        self._top_group = None
        self._ensure_gap("tools")
        markup = format.subagent_spawned(ev, show_id=len(self._active_subagents) > 1)
        widget = self._append(markup)
        self._sub_spawn_widget[ev.subagent_id] = widget
        self._sub_start_time[ev.subagent_id] = time.monotonic()
        self._refresh_status_now()

    def add_subagent_done(self, ev: events.SubagentDone) -> None:
        self._active_subagents.discard(ev.subagent_id)
        widget = self._sub_spawn_widget.pop(ev.subagent_id, None)
        task = self._sub_task_text.pop(ev.subagent_id, "")
        started = self._sub_start_time.pop(ev.subagent_id, None)
        self._refresh_status_now()
        if widget is None:
            return
        elapsed = time.monotonic() - started if started is not None else 0.0
        widget.update(format.subagent_done(ev, task_preview=task, elapsed_s=elapsed))

    def add_compacted(self, ev: events.Compacted) -> None:
        self._ensure_gap("other")
        self._append(format.compacted(ev))

    def add_memory_write(self, ev: events.MemoryWrite) -> None:
        self._ensure_gap("other")
        self._append(format.memory_write(ev))

    def add_memory_consolidated(self, ev: events.MemoryConsolidated) -> None:
        self._ensure_gap("other")
        self._append(format.memory_consolidated(ev))

    def add_error(self, ev: events.Error) -> None:
        self._ensure_gap("other")
        detail = f"  [dim](details in ~/.omega/sessions/{self._session_id}.json)[/dim]" if self._session_id else ""
        self._append(f"{format.error(ev)}{detail}")

    def add_mode_switch(self, mode: str) -> None:
        self._ensure_gap("other")
        self._append(f"[dim]mode: {mode}[/dim]")

    def add_resumed(self, session_id: str, turns: int, messages: int, cwd: str, ago: str = "") -> None:
        self._ensure_gap("other")
        suffix = f", last active {ago} ago" if ago else ""
        self._append(f"[dim]resumed {format.esc(session_id)} — {turns} turns, {messages} messages"
                     f"{suffix} · {format.esc(cwd)}[/dim]")

    def add_dim(self, text: str) -> None:
        self._ensure_gap("other")
        self._append(f"[dim]{format.esc(text)}[/dim]")

    # ---- B1's edit-safety events (guarded in app.py -- see format.py) ------

    def add_checkpoint(self, ev: Any) -> None:
        self._ensure_gap("other")
        self._append(format.checkpoint(ev))

    def add_verified(self, ev: Any) -> None:
        self._ensure_gap("other")
        self._append(format.verified(ev))

    def add_job_started(self, ev: Any) -> None:
        self._ensure_gap("other")
        self._append(format.job_started(ev))

    def add_job_finished(self, ev: Any) -> None:
        self._ensure_gap("other")
        self._append(format.job_finished(ev))
