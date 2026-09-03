"""The right-hand panel: tabbed Session / Git / Connections. Closed by
default (`OmegaApp` starts it hidden); toggled with ctrl+b or `/sidebar`."""
from __future__ import annotations

import time
from collections import Counter
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Container, VerticalScroll
from textual.widgets import Collapsible, Static, TabbedContent, TabPane

from ... import events, session

TAB_IDS = ["tab-session", "tab-git", "tab-connections"]


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


def _capped(paths: list[str], limit: int = 15) -> str:
    if not paths:
        return "[dim](none)[/dim]"
    shown = ", ".join(paths[:limit])
    more = f"  [dim]+{len(paths) - limit} more[/dim]" if len(paths) > limit else ""
    return f"[dim]{shown}[/dim]{more}"


def _fmt_tokens(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


class SessionTab(VerticalScroll):
    def __init__(self, sess: session.Session) -> None:
        super().__init__()
        self._sess = sess
        self._body = Static("")
        self._tool_turn: Counter[str] = Counter()
        self._tool_session: Counter[str] = Counter()
        self._files_turn: list[str] = []
        self._files_session: list[str] = []
        self._subagents_turn: list[tuple[str, str]] = []
        self._sub_start: dict[str, float] = {}
        self._memory_recalls = 0
        self._memory_writes = 0
        self._artifacts = 0
        self._mcp_servers: set[str] = set()
        self._usage: events.Usage | None = None
        self._last_model: tuple[str | None, str] = (None, "?")

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
            for bucket in (self._files_turn, self._files_session):
                if path not in bucket:
                    bucket.append(path)
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

    def _counts(self, counter: Counter[str]) -> str:
        if not counter:
            return "[dim](none)[/dim]"
        return "[dim]" + " · ".join(f"{n} ×{c}" for n, c in counter.most_common()) + "[/dim]"

    def _repaint(self) -> None:
        alias, model = self._last_model
        header = f"[bold]{alias}[/bold] · {model}" if alias else model
        used = _fmt_tokens(self._usage.used) if self._usage else "–"
        limit = _fmt_tokens(self._usage.limit) if self._usage else "–"

        lines = [header, f"[dim]tokens {used}/{limit} · {self._sess.turns} turns[/dim]", ""]
        lines.append("[bold]This turn[/bold]")
        lines.append(self._counts(self._tool_turn))
        lines.append(_capped(self._files_turn))
        for sid, tier in self._subagents_turn:
            started = self._sub_start.get(sid)
            elapsed = time.monotonic() - started if started is not None else 0.0
            lines.append(f"[dim]{tier} · {sid} · {elapsed:.0f}s[/dim]")
        lines.append("")
        lines.append("[bold]Session totals[/bold]")
        lines.append(self._counts(self._tool_session))
        lines.append(f"[dim]{self._memory_recalls} recalls · {self._memory_writes} writes[/dim]")
        lines.append(f"[dim]{self._artifacts} artifacts[/dim]")
        lines.append(_capped(self._files_session))
        if self._mcp_servers:
            lines.append(f"[dim]mcp: {', '.join(sorted(self._mcp_servers))}[/dim]")
        self._body.update("\n".join(lines))


class GitTab(VerticalScroll):
    def __init__(self, cwd: str) -> None:
        super().__init__()
        self._cwd = cwd

    def on_mount(self) -> None:
        self.mount(Static("[dim]loading…[/dim]"))
        self.refresh_repos()

    def refresh_repos(self) -> None:
        self.run_worker(self._load(), exclusive=True)

    async def _load(self) -> None:
        from ... import gitlog
        repos = await gitlog.discover_repos_async(Path(self._cwd))
        await self.remove_children()
        if not repos:
            self.mount(Static(f"[dim]no git repositories under {self._cwd}[/dim]"))
            return
        for repo in repos:
            commits = await gitlog.recent_commits_async(repo, limit=10)
            dirty = "  [red]●dirty[/red]" if repo.dirty else ""
            title = f"{repo.name}  {repo.branch}{dirty}"
            body = ("\n".join(f"[dim]{c.short_sha}  {c.age}  {c.subject}[/dim]" for c in commits)
                   or "[dim](no commits)[/dim]")
            self.mount(Collapsible(Static(body), title=title, collapsed=False))


class ConnectionsTab(VerticalScroll):
    def compose(self) -> ComposeResult:
        yield Static(self._summary())

    def _summary(self) -> str:
        from ... import mcp
        fn = getattr(mcp, "summary_line", None)
        if fn is None:
            return "[dim]no integrations connected[/dim]"
        try:
            text = str(fn())
        except Exception:
            return "[dim]no integrations connected[/dim]"
        return text if text else "[dim]no integrations connected[/dim]"


class Sidebar(Container):
    DEFAULT_CSS = """
    Sidebar {
        width: 1fr;
        background: $panel;
    }
    """

    def __init__(self, sess: session.Session, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self.session_tab = SessionTab(sess)
        self._cwd = sess.cwd

    def compose(self) -> ComposeResult:
        with TabbedContent(initial=TAB_IDS[0]):
            with TabPane("Session", id=TAB_IDS[0]):
                yield self.session_tab
            with TabPane("Git", id=TAB_IDS[1]):
                yield GitTab(self._cwd)
            with TabPane("Connections", id=TAB_IDS[2]):
                yield ConnectionsTab()

    def cycle_tab(self, forward: bool = True) -> None:
        tabs = self.query_one(TabbedContent)
        idx = TAB_IDS.index(tabs.active) if tabs.active in TAB_IDS else 0
        idx = (idx + (1 if forward else -1)) % len(TAB_IDS)
        tabs.active = TAB_IDS[idx]

    def show_tab(self, index: int) -> None:
        if 0 <= index < len(TAB_IDS):
            self.query_one(TabbedContent).active = TAB_IDS[index]
