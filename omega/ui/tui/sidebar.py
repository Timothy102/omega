"""The right-hand panel: tabbed Session / Git / Connections. Closed by
default (`OmegaApp` starts it hidden); toggled with ctrl+b or `/sidebar`."""
from __future__ import annotations

import asyncio
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, VerticalScroll
from textual.events import Click, Resize
from textual.widgets import Collapsible, ContentSwitcher, Static

from ... import events, gitlog, session
from .. import format
from .status import SPINNER_FRAMES

TAB_IDS = ["tab-session", "tab-git", "tab-connections"]
TAB_LABELS = {"tab-session": "session", "tab-git": "git", "tab-connections": "connections"}
_TAB_GAP = 3

_STATUS_COLOR = {"M": "yellow", "A": "green", "D": "red", "R": "yellow", "?": "dim"}

_STATE_GLYPH = {
    "connected": ("●", "green"),
    "configured": ("○", "dim"),
    "needs_auth": ("⚠", "yellow"),
    "error": ("✗", "red"),
    "disabled": ("⊘", "dim"),
}


def _extract_path(name: str, preview: str) -> str | None:
    if name == "read":
        rest = preview.removeprefix("read  ")
        return rest.split(":", 1)[0] if rest else None
    if name == "write":
        rest = preview.removeprefix("write  ")
        return rest.rsplit("  (", 1)[0] if "  (" in rest else (rest or None)
    if name == "edit":
        rest = preview.removeprefix("edit  ")
        return rest or None
    return None


def _extract_mcp_server(preview: str) -> str | None:
    rest = preview.removeprefix("call_tool  ")
    return rest.split(":", 1)[0] if ":" in rest else None


def _section(title: str, width: int) -> str:
    label = title.upper()
    bar = "─" * max(1, width - len(label) - 1)
    return f"[bold]{label}[/bold] [$rule]{bar}[/$rule]"


def _rule(width: int) -> str:
    return f"[$rule]{'─' * max(1, width)}[/$rule]"


def _kv(rows: list[tuple[str, str]]) -> list[str]:
    """Aligned `label   value` lines: dim labels in one column, values in
    the next, and a value's own newlines continue under the value column."""
    if not rows:
        return []
    col = max(len(label) for label, _ in rows) + 2
    lines = []
    for label, value in rows:
        first, *rest = value.split("\n")
        lines.append(f"[dim]{label.ljust(col)}[/dim]{first}")
        lines.extend(f"{' ' * col}{line}" for line in rest)
    return lines


def _chips(counter: Counter[str]) -> str:
    if not counter:
        return "[dim](none)[/dim]"
    chips = []
    for name, c in counter.most_common():
        style = format.style_for(name)
        chips.append(f"[{style}]{name} ×{c}[/{style}]")
    return "  ".join(chips)


def _context_bar(used: int, limit: int, cells: int = 8) -> str:
    """`▰▰▱▱▱▱▱▱ 10.4k / 1.0M · 1%` -- at least one cell fills as soon as
    anything has been used, so a near-empty context window doesn't read as a
    dead bar."""
    if limit <= 0:
        return f"{'▱' * cells} {format.fmt_num(used)} / –"
    pct = used / limit
    filled = max(1, round(pct * cells)) if used > 0 else 0
    filled = min(cells, filled)
    bar = "▰" * filled + "▱" * (cells - filled)
    return f"{bar} {format.fmt_num(used)} / {format.fmt_num(limit)} · {pct * 100:.0f}%"


def _files_block(files: dict[str, set[str]], limit: int = 15) -> str:
    if not files:
        return "[dim](none)[/dim]"
    rows = []
    for path, kinds in list(files.items())[:limit]:
        mark = "".join(sorted(kinds)).ljust(2)
        color = "yellow" if "W" in kinds else "cyan"
        rows.append(f"[{color}]{mark}[/{color}] {format.esc(path)}")
    more = len(files) - limit
    text = "\n".join(rows)
    if more > 0:
        text += f"\n[dim]+{more} more[/dim]"
    return text


class SessionTab(VerticalScroll):
    def __init__(self, sess: session.Session, *, touched: set[str] | None = None,
                 id: str | None = None) -> None:
        super().__init__(id=id)
        self._sess = sess
        self._body = Static("")
        self._tool_turn: Counter[str] = Counter()
        self._tool_session: Counter[str] = Counter()
        self._files_turn: dict[str, set[str]] = {}
        self._files_session: dict[str, set[str]] = {}
        # call_id -> (path, R/W), committed into the files dicts above only on
        # a non-error ToolEnd -- a failed read/write must not show up as a
        # file "touched" this turn.
        self._pending_files: dict[str, tuple[str, str]] = {}
        self._subagents_turn: list[tuple[str, str]] = []
        self._sub_start: dict[str, float] = {}
        self._memory_recalls = 0
        self._memory_writes = 0
        self._artifacts = 0
        self._mcp_servers: set[str] = set()
        self._usage: events.Usage | None = None
        # alias -> {"prompt": n, "completion": n}, priced against
        # `omega.eval.prices` for the MODEL card's running "$ so far" -- kept
        # here rather than read off `OmegaApp` so this widget stays paintable
        # from its own recorded events alone.
        self._usage_totals: dict[str, dict[str, int]] = {}
        self._last_model: tuple[str | None, str] = (None, "?")
        self._context_limit = 0
        self._touched = touched if touched is not None else set()
        self._turn_started: float | None = None
        self._turn_elapsed = 0.0

    def compose(self) -> ComposeResult:
        yield self._body

    def on_mount(self) -> None:
        self.set_interval(1, self._repaint)
        self._repaint()

    def reset_turn(self) -> None:
        self._tool_turn.clear()
        self._files_turn.clear()
        self._pending_files.clear()
        self._subagents_turn.clear()
        self._turn_started = time.monotonic()
        self._turn_elapsed = 0.0
        self._repaint()

    def note_phase(self, state: str) -> None:
        """Freezes THIS TURN's elapsed time once the turn goes idle -- the
        1s repaint interval would otherwise keep advancing it forever."""
        if state == "idle" and self._turn_started is not None:
            self._turn_elapsed = time.monotonic() - self._turn_started
            self._turn_started = None
            self._repaint()

    def _turn_elapsed_seconds(self) -> float:
        if self._turn_started is not None:
            return time.monotonic() - self._turn_started
        return self._turn_elapsed

    def set_session(self, sess: session.Session) -> None:
        """`/sessions`: point this tab at a freshly resumed session -- turn
        and file trackers reset since they describe the OLD session's turn."""
        self._sess = sess
        self.reset_turn()

    def record_model(self, alias: str | None, model: str, *, limit: int | None = None) -> None:
        self._last_model = (alias, model)
        if limit is not None:
            self._context_limit = limit
        self._repaint()

    def record_usage(self, ev: events.Usage) -> None:
        self._usage = ev
        alias = self._last_model[0] or self._last_model[1]
        totals = self._usage_totals.setdefault(alias, {"prompt": 0, "completion": 0})
        totals["prompt"] += ev.prompt_tokens
        totals["completion"] += ev.completion_tokens
        self._repaint()

    def record_tool_start(self, ev: events.ToolStart) -> None:
        self._tool_turn[ev.name] += 1
        self._tool_session[ev.name] += 1
        path = _extract_path(ev.name, ev.args_preview)
        if path:
            self._pending_files[ev.call_id] = (path, "R" if ev.name == "read" else "W")
        if ev.name == "call_tool":
            server = _extract_mcp_server(ev.args_preview)
            if server:
                self._mcp_servers.add(server)
        if ev.name == "recall":
            self._memory_recalls += 1
        elif ev.name in ("remember", "supersede", "link"):
            self._memory_writes += 1
        self._repaint()

    def record_tool_end(self, ev: events.ToolEnd) -> None:
        if ev.offloaded:
            self._artifacts += 1
        pending = self._pending_files.pop(ev.call_id, None)
        if pending is not None and not ev.outcome.startswith("→ error"):
            path, kind = pending
            for bucket in (self._files_turn, self._files_session):
                bucket.setdefault(path, set()).add(kind)
            if kind == "W":
                expanded = Path(path).expanduser()
                absolute = expanded if expanded.is_absolute() else Path(self._sess.cwd) / expanded
                self._touched.add(str(absolute.resolve()))
        self._repaint()

    def record_subagent_spawned(self, ev: events.SubagentSpawned) -> None:
        self._subagents_turn.append((ev.subagent_id, ev.tier))
        self._sub_start[ev.subagent_id] = time.monotonic()
        self._repaint()

    def record_subagent_done(self, ev: events.SubagentDone) -> None:
        self._sub_start.pop(ev.subagent_id, None)
        self._repaint()

    def _cost_so_far(self) -> float:
        from ...eval import prices

        total = 0.0
        for alias, t in self._usage_totals.items():
            cost = prices.estimate_cost(alias, t["prompt"], t["completion"])
            if cost is not None:
                total += cost
        return total

    def _session_tokens(self) -> int:
        return sum(t["prompt"] + t["completion"] for t in self._usage_totals.values())

    def _repaint(self) -> None:
        width = self.size.width or 28
        alias, model = self._last_model
        used = self._usage.used if self._usage else 0
        limit = self._usage.limit if self._usage else self._context_limit

        lines = [_section("model", width)]
        lines += _kv([
            ("model", f"[bold]{format.esc(alias)}[/bold]  [dim]{format.esc(model)}[/dim]"
                      if alias else format.esc(model)),
            ("context", _context_bar(used, limit)),
            ("cost", f"${self._cost_so_far():.2f}"),
        ])
        lines.append("")

        lines.append(_section("this turn", width))
        turn_rows = [("elapsed", f"{self._turn_elapsed_seconds():.0f}s")]
        if self._tool_turn:
            turn_rows.append(("tools", _chips(self._tool_turn)))
        if self._files_turn:
            turn_rows.append(("files", _files_block(self._files_turn)))
        if self._subagents_turn:
            turn_rows.append(("agents", "\n".join(self._subagent_lines())))
        lines += _kv(turn_rows)
        lines.append("")

        lines.append(_section("totals", width))
        total_rows = [
            ("turns", str(self._sess.turns)),
            ("tokens", format.fmt_num(self._session_tokens())),
            ("tools", str(sum(self._tool_session.values()))),
            ("artifacts", str(self._artifacts)),
            ("memory", f"{self._memory_recalls} recalls · {self._memory_writes} writes"),
        ]
        if self._files_session:
            total_rows.append(("files", _files_block(self._files_session)))
        if self._mcp_servers:
            total_rows.append(("mcp", ", ".join(sorted(self._mcp_servers))))
        lines += _kv(total_rows)
        self._body.update("\n".join(lines))

    def _subagent_lines(self) -> list[str]:
        out = []
        for sid, tier in self._subagents_turn:
            started = self._sub_start.get(sid)
            if started is not None:
                elapsed = time.monotonic() - started
                frame = SPINNER_FRAMES[int(elapsed / 0.08) % len(SPINNER_FRAMES)]
                out.append(f"[$accent]{frame}[/$accent] {format.esc(tier)} · "
                           f"[dim]{format.esc(sid)} · {elapsed:.0f}s[/dim]")
            else:
                out.append(f"[green]✓[/green] {format.esc(tier)} · [dim]{format.esc(sid)}[/dim]")
        return out


class _ChangeRow(Static, can_focus=True):
    """One `git status` row: focusable, opens the file's diff on enter/click."""

    DEFAULT_CSS = """
    _ChangeRow { height: 1; }
    _ChangeRow:focus { text-style: underline; }
    """
    BINDINGS = [Binding("enter", "activate", show=False)]

    def __init__(self, repo: gitlog.Repo, change: gitlog.Change, touched: bool) -> None:
        color = _STATUS_COLOR.get(change.status, "dim")
        counts = ""
        if change.status == "M" and (change.added or change.removed):
            counts = f"  [dim]+{change.added} −{change.removed}[/dim]"
        elif change.status == "A" and change.added:
            counts = f"  [dim]+{change.added}[/dim]"
        mark = " [$accent]●[/$accent]" if touched else ""
        super().__init__(f"[{color}]{change.status}[/{color}]  {format.esc(change.path)}{counts}{mark}")
        self.repo = repo
        self.path = change.path

    def on_click(self, event: Click) -> None:
        self.action_activate()

    def action_activate(self) -> None:
        from .modals import DiffScreen
        self.app.push_screen(DiffScreen(self.repo, self.path))


class GitTab(VerticalScroll):
    def __init__(self, cwd: str, *, touched: set[str] | None = None, id: str | None = None) -> None:
        super().__init__(id=id)
        self._cwd = cwd
        self._touched = touched if touched is not None else set()

    def on_mount(self) -> None:
        self.mount(Static("[dim]loading…[/dim]"))
        self.refresh_repos()

    def refresh_repos(self) -> None:
        self.run_worker(self._load(), exclusive=True)

    def _is_touched(self, repo: gitlog.Repo, path: str) -> bool:
        return str((repo.path / path).resolve()) in self._touched

    async def _load(self) -> None:
        repos = await gitlog.discover_repos_async(Path(self._cwd))
        await self.remove_children()
        if not repos:
            self.mount(Static(f"[dim]no git repositories under {format.esc(self._cwd)}[/dim]"))
            return
        for i, repo in enumerate(repos):
            if i:
                self.mount(Static(""))
            await self._mount_repo(repo)

    async def _mount_repo(self, repo: gitlog.Repo) -> None:
        changes = await gitlog.working_tree_async(repo)
        commits = await gitlog.recent_commits_async(repo, limit=10)
        width = self.size.width or 28
        dirty = "  [yellow]●[/yellow] [dim]dirty[/dim]" if repo.dirty else ""
        self.mount(Static(f"[bold]{format.esc(repo.name)}[/bold]  "
                          f"[dim]{format.esc(repo.branch)}[/dim]{dirty}"))
        self.mount(Static(_section("changes", width)))
        if not changes:
            self.mount(Static("[dim]  (clean)[/dim]"))
        else:
            for change in changes:
                self.mount(_ChangeRow(repo, change, touched=self._is_touched(repo, change.path)))
        history_body = "\n".join(
            f"[$accent]{format.esc(c.short_sha)}[/$accent]  "
            f"[dim]{format.esc(c.age)}  {format.esc(c.subject)}[/dim]" for c in commits
        ) or "[dim](no commits)[/dim]"
        self.mount(Collapsible(Static(history_body), title="HISTORY", collapsed=True))


class ConnectionsTab(VerticalScroll):
    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._header = Static("")
        self._body = Static("[dim]loading…[/dim]")

    def compose(self) -> ComposeResult:
        yield self._header
        yield self._body

    def on_mount(self) -> None:
        self.refresh_status()

    def refresh_status(self) -> None:
        self._header.update(_section("connections", self.size.width or 28))
        self.run_worker(self._load(), exclusive=True)

    async def _load(self) -> None:
        from ... import mcp
        try:
            statuses = await asyncio.to_thread(mcp.status)
        except Exception:
            self._body.update("[dim]no integrations connected[/dim]")
            return
        if not statuses:
            self._body.update("[dim]no integrations connected[/dim]")
            return
        lines = []
        for name, st in sorted(statuses.items()):
            glyph, color = _STATE_GLYPH.get(st.state, ("●", "dim"))
            bits = [f"[{color}]{glyph}[/{color}] {format.esc(name)}"]
            if st.state == "connected":
                bits.append(f"[dim]{st.tools} tools[/dim]")
                if st.last_used:
                    bits.append(f"[dim]· used {format.relative_age(time.time() - st.last_used)} ago[/dim]")
            elif st.state == "configured":
                bits.append("[dim]connects on first use[/dim]")
            elif st.state == "needs_auth":
                bits.append(f"[dim]omega connections connect {format.esc(name)}[/dim]")
            elif st.state == "error":
                bits.append(f"[dim]{format.esc((st.error or '')[:60])}[/dim]")
            lines.append("  ".join(bits))
        self._body.update("\n".join(lines))


class _TabStrip(Static):
    """The panel's own flat tab row -- `SESSION  GIT  CONNECTIONS` with the
    active one in bold, over a hairline that continues the header's rule
    across the divider. Click a label to switch, same as ctrl+1/2/3."""

    DEFAULT_CSS = """
    _TabStrip { height: 2; }
    """

    def __init__(self, on_pick: Callable[[str], None]) -> None:
        super().__init__()
        self._active = TAB_IDS[0]
        self._spans: list[tuple[int, int, str]] = []
        self._on_pick = on_pick

    def set_active(self, tab_id: str) -> None:
        self._active = tab_id
        self._repaint()

    def on_mount(self) -> None:
        self._repaint()

    def on_resize(self, event: Resize) -> None:
        self._repaint()

    def on_click(self, event: Click) -> None:
        for start, end, tab_id in self._spans:
            if start <= event.x < end:
                self._on_pick(tab_id)
                return

    def _repaint(self) -> None:
        width = self.size.width or 28
        parts, self._spans, x = [], [], 0
        for tab_id in TAB_IDS:
            label = TAB_LABELS[tab_id].upper()
            style = "bold" if tab_id == self._active else "dim"
            parts.append(f"[{style}]{label}[/{style}]")
            self._spans.append((x, x + len(label), tab_id))
            x += len(label) + _TAB_GAP
        self.update((" " * _TAB_GAP).join(parts) + "\n" + _rule(width))


class Sidebar(Container):
    DEFAULT_CSS = """
    Sidebar {
        width: 34;
        max-width: 40%;
        border-left: solid $rule;
        padding: 0 0 0 1;
    }
    Sidebar ContentSwitcher { height: 1fr; margin-top: 1; }
    """

    def __init__(self, sess: session.Session, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._touched: set[str] = set()
        self.session_tab = SessionTab(sess, touched=self._touched, id=TAB_IDS[0])
        self.git_tab = GitTab(sess.cwd, touched=self._touched, id=TAB_IDS[1])
        self.connections_tab = ConnectionsTab(id=TAB_IDS[2])
        self._strip = _TabStrip(self._set_active)
        self._cwd = sess.cwd

    def compose(self) -> ComposeResult:
        yield self._strip
        with ContentSwitcher(initial=TAB_IDS[0]):
            yield self.session_tab
            yield self.git_tab
            yield self.connections_tab

    @property
    def active(self) -> str:
        current = self.query_one(ContentSwitcher).current
        return current if current in TAB_IDS else TAB_IDS[0]

    def _set_active(self, tab_id: str) -> None:
        self.query_one(ContentSwitcher).current = tab_id
        self._strip.set_active(tab_id)

    def cycle_tab(self, forward: bool = True) -> None:
        idx = TAB_IDS.index(self.active)
        self._set_active(TAB_IDS[(idx + (1 if forward else -1)) % len(TAB_IDS)])

    def show_tab(self, index: int) -> None:
        if 0 <= index < len(TAB_IDS):
            self._set_active(TAB_IDS[index])
