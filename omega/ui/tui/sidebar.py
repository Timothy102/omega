"""The right-hand panel: tabbed Session / Git / Connections. Closed by
default (`OmegaApp` starts it hidden); toggled with ctrl+b or `/sidebar`."""
from __future__ import annotations

import asyncio
import time
from collections import Counter
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, VerticalScroll
from textual.events import Click
from textual.widgets import Collapsible, Static, TabbedContent, TabPane

from ... import events, gitlog, session
from .. import format
from .status import SPINNER_FRAMES

TAB_IDS = ["tab-session", "tab-git", "tab-connections"]

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
    return f"[bold]{label}[/bold] [dim]{bar}[/dim]"


def _chips(counter: Counter[str]) -> str:
    if not counter:
        return "[dim](none)[/dim]"
    chips = []
    for name, c in counter.most_common():
        style = format.style_for(name)
        chips.append(f"[{style}]{name} ×{c}[/{style}]")
    return "  ".join(chips)


def _files_block(files: dict[str, set[str]], limit: int = 15) -> str:
    if not files:
        return "[dim](none)[/dim]"
    rows = []
    for path, kinds in list(files.items())[:limit]:
        mark = "".join(sorted(kinds))
        color = "yellow" if "W" in kinds else "cyan"
        rows.append(f"[{color}]{mark}[/{color}] [dim]{path}[/dim]")
    more = len(files) - limit
    text = "\n".join(rows)
    if more > 0:
        text += f"\n[dim]+{more} more[/dim]"
    return text


class SessionTab(VerticalScroll):
    def __init__(self, sess: session.Session, *, touched: set[str] | None = None) -> None:
        super().__init__()
        self._sess = sess
        self._body = Static("")
        self._tool_turn: Counter[str] = Counter()
        self._tool_session: Counter[str] = Counter()
        self._files_turn: dict[str, set[str]] = {}
        self._files_session: dict[str, set[str]] = {}
        self._subagents_turn: list[tuple[str, str]] = []
        self._sub_start: dict[str, float] = {}
        self._memory_recalls = 0
        self._memory_writes = 0
        self._artifacts = 0
        self._mcp_servers: set[str] = set()
        self._usage: events.Usage | None = None
        self._last_model: tuple[str | None, str] = (None, "?")
        self._touched = touched if touched is not None else set()

    def compose(self) -> ComposeResult:
        yield self._body

    def on_mount(self) -> None:
        self.set_interval(1, self._repaint)
        self._repaint()

    def reset_turn(self) -> None:
        self._tool_turn.clear()
        self._files_turn.clear()
        self._subagents_turn.clear()
        self._repaint()

    def record_model(self, alias: str | None, model: str) -> None:
        self._last_model = (alias, model)
        self._repaint()

    def record_usage(self, ev: events.Usage) -> None:
        self._usage = ev
        self._repaint()

    def record_tool_start(self, ev: events.ToolStart) -> None:
        self._tool_turn[ev.name] += 1
        self._tool_session[ev.name] += 1
        path = _extract_path(ev.name, ev.args_preview)
        if path:
            kind = "R" if ev.name == "read" else "W"
            for bucket in (self._files_turn, self._files_session):
                bucket.setdefault(path, set()).add(kind)
            if kind == "W":
                expanded = Path(path).expanduser()
                absolute = expanded if expanded.is_absolute() else Path(self._sess.cwd) / expanded
                self._touched.add(str(absolute.resolve()))
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
        self._repaint()

    def record_subagent_spawned(self, ev: events.SubagentSpawned) -> None:
        self._subagents_turn.append((ev.subagent_id, ev.tier))
        self._sub_start[ev.subagent_id] = time.monotonic()
        self._repaint()

    def record_subagent_done(self, ev: events.SubagentDone) -> None:
        self._sub_start.pop(ev.subagent_id, None)
        self._repaint()

    def _repaint(self) -> None:
        width = self.size.width or 28
        alias, model = self._last_model
        header = f"[bold]{alias}[/bold] · {model}" if alias else model
        used = format.fmt_num(self._usage.used) if self._usage else "–"
        limit = format.fmt_num(self._usage.limit) if self._usage else "–"

        lines = [header, f"[dim]tokens {used}/{limit} · {self._sess.turns} turns[/dim]", ""]
        lines.append(_section("this turn", width))
        lines.append(_chips(self._tool_turn))
        lines.append("[dim]FILES[/dim]")
        lines.append(_files_block(self._files_turn))
        for sid, tier in self._subagents_turn:
            started = self._sub_start.get(sid)
            if started is not None:
                elapsed = time.monotonic() - started
                frame = SPINNER_FRAMES[int(elapsed / 0.08) % len(SPINNER_FRAMES)]
                lines.append(f"[dim]{frame} {tier} · {sid} · {elapsed:.0f}s[/dim]")
            else:
                lines.append(f"[green]✓[/green] [dim]{tier} · {sid}[/dim]")

        if self._tool_turn != self._tool_session or self._files_turn != self._files_session:
            lines.append("")
            lines.append(_section("session totals", width))
            lines.append(_chips(self._tool_session))
            lines.append(f"[dim]{self._memory_recalls} recalls · {self._memory_writes} writes[/dim]")
            lines.append(f"[dim]{self._artifacts} artifacts[/dim]")
            lines.append("[dim]FILES[/dim]")
            lines.append(_files_block(self._files_session))
            if self._mcp_servers:
                lines.append(f"[dim]mcp: {', '.join(sorted(self._mcp_servers))}[/dim]")
        self._body.update("\n".join(lines))


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
        super().__init__(f"[{color}]{change.status}[/{color}]  {change.path}{counts}{mark}")
        self.repo = repo
        self.path = change.path

    def on_click(self, event: Click) -> None:
        self.action_activate()

    def action_activate(self) -> None:
        from .modals import DiffScreen
        self.app.push_screen(DiffScreen(self.repo, self.path))


class GitTab(VerticalScroll):
    def __init__(self, cwd: str, *, touched: set[str] | None = None) -> None:
        super().__init__()
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
            self.mount(Static(f"[dim]no git repositories under {self._cwd}[/dim]"))
            return
        for repo in repos:
            await self._mount_repo(repo)

    async def _mount_repo(self, repo: gitlog.Repo) -> None:
        changes = await gitlog.working_tree_async(repo)
        commits = await gitlog.recent_commits_async(repo, limit=10)
        dirty = "  [yellow]●[/yellow] [dim]dirty[/dim]" if repo.dirty else ""
        self.mount(Static(f"[bold]{repo.name}[/bold]  [dim]{repo.branch}[/dim]{dirty}"))
        self.mount(Static("[dim]CHANGES[/dim]"))
        if not changes:
            self.mount(Static("[dim]  (clean)[/dim]"))
        else:
            for change in changes:
                self.mount(_ChangeRow(repo, change, touched=self._is_touched(repo, change.path)))
        history_body = "\n".join(
            f"[$accent]{c.short_sha}[/$accent]  [dim]{c.age}  {c.subject}[/dim]" for c in commits
        ) or "[dim](no commits)[/dim]"
        self.mount(Collapsible(Static(history_body), title="HISTORY", collapsed=True))


class ConnectionsTab(VerticalScroll):
    def __init__(self) -> None:
        super().__init__()
        self._body = Static("[dim]loading…[/dim]")

    def compose(self) -> ComposeResult:
        yield self._body
        yield Static("[dim]omega connections[/dim]")

    def on_mount(self) -> None:
        self.refresh_status()

    def refresh_status(self) -> None:
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
            bits = [f"[{color}]{glyph}[/{color}] {name}"]
            if st.state == "connected":
                bits.append(f"[dim]{st.tools} tools[/dim]")
                if st.last_used:
                    bits.append(f"[dim]· used {format.relative_age(time.time() - st.last_used)} ago[/dim]")
            elif st.state == "configured":
                bits.append("[dim]connects on first use[/dim]")
            elif st.state == "needs_auth":
                bits.append(f"[dim]omega connections connect {name}[/dim]")
            elif st.state == "error":
                bits.append(f"[dim]{(st.error or '')[:60]}[/dim]")
            lines.append("  ".join(bits))
        self._body.update("\n".join(lines))


class Sidebar(Container):
    DEFAULT_CSS = """
    Sidebar {
        width: 1fr;
        background: $panel;
    }
    """

    def __init__(self, sess: session.Session, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._touched: set[str] = set()
        self.session_tab = SessionTab(sess, touched=self._touched)
        self.git_tab = GitTab(sess.cwd, touched=self._touched)
        self.connections_tab = ConnectionsTab()
        self._cwd = sess.cwd

    def compose(self) -> ComposeResult:
        with TabbedContent(initial=TAB_IDS[0]):
            with TabPane("Session", id=TAB_IDS[0]):
                yield self.session_tab
            with TabPane("Git", id=TAB_IDS[1]):
                yield self.git_tab
            with TabPane("Connections", id=TAB_IDS[2]):
                yield self.connections_tab

    def cycle_tab(self, forward: bool = True) -> None:
        tabs = self.query_one(TabbedContent)
        idx = TAB_IDS.index(tabs.active) if tabs.active in TAB_IDS else 0
        idx = (idx + (1 if forward else -1)) % len(TAB_IDS)
        tabs.active = TAB_IDS[idx]

    def show_tab(self, index: int) -> None:
        if 0 <= index < len(TAB_IDS):
            self.query_one(TabbedContent).active = TAB_IDS[index]
