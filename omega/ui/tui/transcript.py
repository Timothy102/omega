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

import textwrap
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from rich.markdown import Markdown
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.content import Content
from textual.events import Click, Resize
from textual.timer import Timer
from textual.widgets import Static

from ... import events
from .. import format
from . import theme
from .status import SPINNER_FRAMES

_GROUP_CAP = 3
# The result column: `⎿` hangs off the call row, and every following line
# aligns to the character after it.
_RESULT_MARK = "⎿"
_RESULT_INDENT = " " * 5
# Rail lines an error shows before it asks to be expanded -- enough for the
# exception type and the path or assertion that caused it, which is what
# decides the next action.
_ERROR_BODY_LINES = 3

# The streaming read-cursor: a block that sits on the newest character so the
# eye can find where the text currently is, instead of re-scanning the whole
# paragraph after every repaint.
#
# It pulses on a heartbeat envelope rather than an even blink -- two quick
# beats and a rest, at roughly 55bpm. An even blink reads as a warning
# indicator and competes for attention; a heartbeat reads as "alive" and the
# eye stops tracking it consciously within a second or two, which is exactly
# what a position marker should do.
# Brightness, not hue. A prose block renders through a rich `Table.grid`
# (the bullet gutter), and rich resolves markup on its own -- it has never
# heard of Textual's `$accent`, so a themed tag here raises `MarkupError` and
# takes the repaint down with it. `bold`/`dim` are rich's own, they inherit
# whatever foreground the terminal is already using, and a pulse in
# brightness is what reads as a heartbeat anyway.
_CURSOR = "▊"
_CURSOR_BEAT = ("bold", "bold", "dim", "bold", "bold",
                "dim", "dim", "dim", "dim", "dim")
_CURSOR_INTERVAL = 0.11
# How long the cursor lingers on the last character after the stream stops.
# The final delta and the turn ending are one instant apart; without a pause
# the marker vanishes exactly when the reader looks for where the text
# stopped.
_CURSOR_LINGER = 0.9


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


class _ToolBlock(Static, can_focus=True):
    """One tool call, whole: its call row, a few lines of the tool's REAL
    output on a `│` rail, and the `└ …` outcome footer.

    One widget instead of the three rows this used to mount, for two
    reasons. Correctness: a call's outcome now lands under its own row, where
    three independently mounted rows let three parallel tools pile all three
    outcomes at the bottom, attached to whichever row happened to finish
    last. And affordance: the block owns its own collapsed/expanded state, so
    enter or a click swaps the preview for a much longer excerpt in place --
    the same one-key expansion `_MoreLine` gives a collapsed group."""

    DEFAULT_CSS = """
    _ToolBlock { height: auto; }
    _ToolBlock:focus { background: $boost; }
    """
    BINDINGS = [Binding("enter", "expand", show=False)]

    def __init__(self, start: events.ToolStart, *, show_suffix: bool,
                 width_of: Callable[[], int | None],
                 body_width_of: Callable[[], int | None]) -> None:
        super().__init__()
        self._start = start
        self._show_suffix = show_suffix
        self._width_of = width_of
        self._body_width_of = body_width_of
        self._end: events.ToolEnd | None = None
        self._repeat = 1
        self._expanded = False
        self.update(self._markup())

    # ---- state changes -----------------------------------------------------

    def note_end(self, ev: events.ToolEnd) -> None:
        self._end = ev
        self.update(self._markup())

    def note_repeat(self, count: int) -> None:
        self._repeat = count
        self.update(self._markup())

    def rerender(self) -> None:
        """Rebuild against the pane's CURRENT width -- without this a row's
        truncation stays frozen at whatever width was current when it was
        first mounted, so a resize leaves rows wrapping or stuck short."""
        self.update(self._markup())

    def on_click(self, event: Click) -> None:
        self.action_expand()

    def action_expand(self) -> None:
        if not self._expandable():
            return
        self._expanded = not self._expanded
        self.update(self._markup())

    # ---- rendering ---------------------------------------------------------

    def _failed(self) -> bool:
        return self._end is not None and self._end.outcome.startswith("→ error")

    def _error_text(self) -> str:
        outcome = self._end.outcome if self._end is not None else ""
        body = outcome[2:] if outcome.startswith("→ ") else outcome
        return body[len("error:"):].strip() if body.startswith("error:") else body

    def _body(self) -> tuple[list[str], int]:
        """The block's evidence lines and how many more the full result holds.

        Success and failure share one shape on purpose: the result column
        always carries what the tool actually produced. An error used to be
        squeezed into a one-line footer instead, where it wrapped flush
        against the left margin and broke the block's own indentation the
        moment it ran past one line."""
        if self._end is None:
            return [], 0
        width = self._body_width_of() or 60
        if self._failed():
            # Wrapped rather than truncated: a stack trace or a provider's
            # error body is the whole point of the row, and the reason to
            # read it is usually past the first 80 columns.
            limit = format.PREVIEW_EXPANDED if self._expanded else _ERROR_BODY_LINES
            wrapped = textwrap.wrap(self._error_text(), width) or [""]
            return wrapped[:limit], max(0, len(wrapped) - limit)
        # `None` lets each tool's own budget in `PREVIEW_BUDGET` decide.
        budget = format.PREVIEW_EXPANDED if self._expanded else None
        shown = format.preview_lines(self._end.name, self._end.result_preview,
                                     limit=budget, width=width)
        hidden = format.preview_hidden(self._end.name, self._end.result_preview, len(shown))
        return shown, hidden

    def _expandable(self) -> bool:
        if self._end is None:
            return False
        return self._expanded or self._body()[1] > 0

    def _line_style(self, line: str) -> str:
        if self._failed():
            return "red"
        if self._end is not None and self._end.name in format.DIFF_RESULT_TOOLS:
            return format.diff_style(line)
        return "dim"

    def _result_rows(self) -> list[tuple[str, str]]:
        """`(style, text)` for everything below the call row: the verdict
        first, then the tool's own output, then the elision line."""
        if self._end is None:
            return []
        rows: list[tuple[str, str]] = []
        if self._end.outcome and not self._failed():
            # The verdict LEADS. A summary read after the evidence it
            # summarises arrives too late to be doing any work. A failure has
            # no separate verdict -- its message IS the body, below.
            rows.append(("dim", self._end.outcome.removeprefix("→ ")))
        body, hidden = self._body()
        rows += [(self._line_style(line), line) for line in body]
        if self._expanded:
            rows.append(("dim", "▾ enter to collapse"))
        elif hidden:
            # With nothing on show, the count above IS the total -- repeating
            # it as "+60 lines" reads like a second, different number.
            more = f"+{hidden} line" if hidden == 1 else f"+{hidden} lines"
            rows.append(("dim", f"… {more} (enter to expand)" if body
                         else "… enter to expand"))
        return rows

    def _markup(self) -> str:
        head = format.tool_start(self._start, show_subagent_suffix=self._show_suffix,
                                 width=self._width_of(), failed=self._failed())
        if self._repeat > 1:
            head += f"  [dim]×{self._repeat}[/dim]"
        lines = [head]
        for i, (style, text) in enumerate(self._result_rows()):
            # `⎿` hangs off the call row once; every line after it aligns to
            # the same column, so a multi-line result reads as one indented
            # column of output rather than as more top-level rows.
            prefix = f"  [dim]{_RESULT_MARK}[/dim]  " if i == 0 else _RESULT_INDENT
            lines.append(f"{prefix}[{style}]{format.esc(text)}[/{style}]")
        return "\n".join(lines)


class _Prose(Static):
    """An assistant prose block.

    Flat, with no gutter mark of its own: `⏺` now belongs to tool calls, and
    a second near-identical bullet on prose made the two read as one
    undifferentiated column. Prose is what the transcript is FOR, so it gets
    the left margin and the calls indent away from it.

    Capped at a readable measure rather than the pane's full width -- a line
    spanning a wide terminal costs the eye the return sweep on every wrap,
    which is the single biggest readability tax a full-width transcript
    imposes."""

    DEFAULT_CSS = """
    _Prose { max-width: 96; }
    """


class _EmptyState(Static):
    DEFAULT_CSS = """
    _EmptyState {
        width: 100%;
        height: 100%;
        content-align: center middle;
        text-align: center;
        color: $text-muted;
    }
    """

    def __init__(self) -> None:
        super().__init__(
            "[bold]⌘ omega[/bold]\n"
            "[dim]ask anything about this repo · /discuss to think together · "
            "/plan to plan[/dim]")


class _PromptBand(Static):
    """The turn-opening user-prompt row: an accent-coloured `›` and the text,
    flat on the page -- `add_user_message` builds the left/right-aligned text."""

    DEFAULT_CSS = """
    _PromptBand { width: 100%; margin: 1 0; }
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
        color: $text-muted;
        display: none;
    }
    """

    def __init__(self, on_click: Callable[[], None]) -> None:
        super().__init__("[dim]↓ new output[/dim]")
        self._click_cb = on_click

    def on_click(self, event: Click) -> None:
        self._click_cb()


def _with_cursor(text: str, cursor: str) -> Content:
    """Model text is data, not markup. Escaping is not enough here: a
    delta that stops on a backslash (`contact\\` of `contact\\_sheet`) or an
    unclosed `[` (a link mid-stream) makes Textual's parser swallow the
    cursor's opening tag, and its `[/dim]` then raises `MarkupError` from
    the heartbeat timer. So the text is never parsed at all -- only the
    cursor is."""
    return Content(text) + Content.from_markup(cursor)


class Transcript(VerticalScroll):
    DEFAULT_CSS = """
    Transcript {
        width: 1fr;
        min-width: 40;
        padding: 0 2;
    }
    Transcript > Static { margin-bottom: 1; }
    Transcript > _LiveStatus, Transcript > _NewOutputPill { margin-bottom: 0; }
    """
    BINDINGS = []

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._live_assistant: Static | None = None
        self._live_text: str = ""
        self._session_id: str = ""

        self._cursor_timer: Timer | None = None
        self._cursor_frame = 0

        self._empty_state: _EmptyState | None = None
        self._last_kind: str | None = None

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

        # Each live call's own block. A `_ToolBlock` holds the `ToolStart`
        # behind its markup, so `on_resize` rebuilds every row against the
        # pane's new width instead of leaving stale truncation baked in from
        # whatever width was current when the row was first mounted.
        self._call_block: dict[str, _ToolBlock] = {}
        self._buffered_end: dict[str, events.ToolEnd] = {}
        self._last_sig: tuple[str, str, str | None] | None = None
        self._last_block: _ToolBlock | None = None
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

    def _append(self, markup: str | Static) -> Static:
        return self._mount_widget(markup if isinstance(markup, Static) else Static(markup))

    def _ensure_gap(self, kind: str) -> None:
        """Records which kind of block is being started.

        The blank line itself is CSS now (`Transcript > Static`'s
        `margin-bottom`) rather than an empty spacer widget, so every block
        breathes -- not only the ones that happen to follow a block of a
        different kind. Mounting spacers on top of that margin double-spaced
        the pane; the tracking stays because callers still read `_last_kind`
        to decide block boundaries."""
        self._last_kind = kind

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
        # The way out, stated where the waiting happens. It was documented
        # only in `?`, which is the one screen nobody opens while a turn they
        # want to stop is running.
        bits.append("ctrl+c to interrupt")
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
            self._stop_cursor()
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
        # `size` (the border box); `_PromptBand` adds none of its own.
        width = self.content_size.width or 74
        left = (f"[bold $accent]›[/bold $accent] [bold]{format.esc(text)}[/bold]"
                f"  [dim]{mode}[/dim]")
        ts = time.strftime("%H:%M")
        self._mount_widget(_PromptBand(format.right_align(left, f"[dim]{ts}[/dim]", width)))

    def _code_theme(self) -> str:
        return theme.current_code_theme(self.app)

    def _cursor_markup(self) -> str:
        style = _CURSOR_BEAT[self._cursor_frame % len(_CURSOR_BEAT)]
        return f"[{style}]{_CURSOR}[/{style}]"

    def _paint_live(self, cursor: str) -> None:
        if self._live_assistant is not None:
            self._live_assistant.update(_with_cursor(self._live_text, cursor))

    def _beat_cursor(self) -> None:
        self._cursor_frame += 1
        self._paint_live(self._cursor_markup())

    def _start_cursor(self) -> None:
        if self._cursor_timer is None:
            self._cursor_frame = 0
            self._cursor_timer = self.set_interval(_CURSOR_INTERVAL, self._beat_cursor)

    def _stop_cursor(self) -> None:
        if self._cursor_timer is not None:
            self._cursor_timer.stop()
            self._cursor_timer = None

    def add_text_delta(self, text: str) -> None:
        if self._live_assistant is None:
            self._ensure_gap("assistant")
            self._live_text = ""
            self._live_assistant = self._append(_Prose(""))
        self._live_text += text
        self._start_cursor()
        self._paint_live(self._cursor_markup())

    def _settle(self, widget: Static, text: str) -> None:
        """Retires a finished prose block: the cursor rests dim on the last
        character for `_CURSOR_LINGER`, then the block re-renders as Markdown
        without it. Deferred rather than immediate so the marker is still
        there when the reader's eye arrives at the end of the sentence that
        just finished."""
        self._stop_cursor()
        widget.update(_with_cursor(text, f"[dim]{_CURSOR}[/dim]"))

        def finish() -> None:
            widget.update(Markdown(text, code_theme=self._code_theme()))

        self.set_timer(_CURSOR_LINGER, finish)

    def _close_text_block(self) -> None:
        """Ends the CURRENT round's prose block by rendering it as Markdown in
        place, so a `ToolStart`/`SubagentSpawned` between two rounds of text
        never lets the second round's deltas keep appending onto the first
        round's already-mounted widget (which is what put round-2 prose above
        the tool calls that produced it, and ran its sentences together with
        round 1's)."""
        if self._live_assistant is None:
            return
        self._settle(self._live_assistant, self._live_text)
        self._live_assistant = None
        self._live_text = ""

    def finalize_turn(self, text: str) -> None:
        self._stop_status()
        text = text or self._live_text
        if not text:
            self._stop_cursor()
            return
        if self._live_assistant is None:
            self._ensure_gap("assistant")
            self._live_assistant = self._append(_Prose(""))
        self._settle(self._live_assistant, text)
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
        return f"{lead} +{remaining} more tool calls (g to expand)[/dim]"

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
        """Rebuilds every currently-visible tool block against the pane's new
        width -- without this, a row's `truncate_middle` cutoff stays fixed at
        whatever width was current when it was first mounted, so shrinking the
        pane (a live resize, or the sidebar opening) leaves rows wrapping
        across extra lines instead of re-truncating to fit, and growing it
        back leaves them stuck short of what would now fit."""
        for block in dict.fromkeys(self._call_block.values()):
            block.rerender()

    def _body_width(self) -> int | None:
        """Width for the `│` rail's lines. Wider than `_detail_width` -- a
        rail line pays only for the two-space indent and the rail itself, not
        for the head row's bullet and name column."""
        w = self.content_size.width
        return max(20, w - len(_RESULT_INDENT) - 2) if w else None

    def _new_block(self, ev: events.ToolStart, show_suffix: bool) -> _ToolBlock:
        block = _ToolBlock(ev, show_suffix=show_suffix, width_of=self._detail_width,
                           body_width_of=self._body_width)
        self._mount_widget(block)
        self._call_block[ev.call_id] = block
        end_ev = self._buffered_end.pop(ev.call_id, None)
        if end_ev is not None:
            block.note_end(end_ev)
        return block

    def add_tool_start(self, ev: events.ToolStart) -> None:
        if ev.name == "subagent":
            return
        self._close_text_block()
        self._active_tool_count += 1
        self._refresh_status_now()
        key = ev.subagent_id
        sig = (ev.name, ev.args_preview, key)
        if self._last_sig == sig and self._last_block is not None:
            self._last_repeat += 1
            self._last_block.note_repeat(self._last_repeat)
            self._call_block[ev.call_id] = self._last_block
            return

        group = self._group_for(key)
        group.total += 1
        if group.shown < _GROUP_CAP:
            group.shown += 1
            if key is None:
                self._ensure_gap("tools")
            show_suffix = key is None or len(self._active_subagents) > 1
            block = self._new_block(ev, show_suffix)
            self._last_sig, self._last_block, self._last_repeat = sig, block, 1
        else:
            group.hidden.append(ev)
            self._last_sig = None
            self._last_block = None
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
            self._new_block(start_ev, show_suffix)
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
        block = self._call_block.get(ev.call_id)
        if block is None:
            self._buffered_end[ev.call_id] = ev
            return
        # Always folded in, even with an empty outcome -- the result preview
        # is worth showing on its own for a call too fast to earn a duration.
        block.note_end(ev)

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
