"""OmegaApp: composition, key bindings, and turn execution. Rendering logic
lives in transcript.py/sidebar.py/status.py; this module only translates
events into calls on their typed widget methods, never the reverse."""
from __future__ import annotations

import asyncio
import difflib
import time
from pathlib import Path
from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.timer import Timer
from textual.widgets import Input, Static
from textual.worker import Worker

from ... import compact, config, events, export, gitlog, loop, session, trace
from ...memory import consolidate
from .. import format
from . import prefs
from .history import InputHistory
from .modals import AskUserScreen, ConfirmScreen, DiffScreen, ModelPickerScreen, SessionsScreen
from .sidebar import Sidebar
from .status import StatusBar, StatusState
from .transcript import Transcript

_MODE_ROLE = {"build": "main", "plan": "plan", "discuss": "discuss"}
_MODE_TAG_STYLE = {"build": "dim", "plan": "yellow", "discuss": "$accent"}

_COMMANDS = ["/plan", "/build", "/discuss", "/mode", "/memory-gc", "/model", "/sidebar",
            "/cost", "/export", "/compact", "/undo", "/diff", "/verify", "/sessions",
            "/help", "/quit"]

_HELP_TEXT = (
    "ctrl+c cancel turn  ·  ctrl+o /model switch model  ·  ctrl+b toggle side panel\n"
    "ctrl+1/2/3 or [ ] switch side-panel tab  ·  up/down input history\n"
    "/plan /build /discuss switch modes  ·  /model <alias>  ·  /mode  ·  /sidebar  ·  /memory-gc\n"
    "/cost tokens & $  ·  /export [path]  ·  /compact  ·  /undo [n]  ·  /diff  ·  /verify  ·  "
    "/sessions  ·  /quit")

# `events.Checkpoint`/`Verified`/`JobStarted`/`JobFinished` are B1's, added to
# events.py concurrently with this file -- resolved once at import time so
# `emit()` below can dispatch to them without a hard import dependency.
_Checkpoint = getattr(events, "Checkpoint", None)
_Verified = getattr(events, "Verified", None)
_JobStarted = getattr(events, "JobStarted", None)
_JobFinished = getattr(events, "JobFinished", None)

# Git branch per cwd, cached for the life of the process and refreshed
# explicitly at turn end -- a `git symbolic-ref` per header repaint would be
# wasteful for something that only ever changes between turns.
_branch_cache: dict[str, str] = {}


async def _lookup_branch(cwd: str) -> str:
    path = Path(cwd)
    for d in (path, *path.parents):
        if (d / ".git").exists():
            return await asyncio.to_thread(gitlog._branch, d)
    return ""


class HeaderBar(Static):
    """The top chrome line: wordmark, cwd, git branch, session id -- all dim
    but the wordmark (Part R9)."""

    DEFAULT_CSS = """
    HeaderBar { height: 1; color: $text-muted; padding: 0 1; }
    """

    def set_state(self, cwd: str, branch: str, session_id: str) -> None:
        bits = [format.abbrev_cwd(cwd)]
        if branch:
            bits.append(branch)
        bits.append(session_id)
        self.update(f"[bold]⌘ omega[/bold]  [dim]{' · '.join(bits)}[/dim]")


class OmegaApp(App[None]):
    CSS = """
    Screen { layout: vertical; }
    #header { height: 1; }
    #body { height: 1fr; }
    #input-rule { height: 1; }
    #input-row { height: 1; }
    #prompt { width: 1fr; }
    #mode-tag { width: auto; padding: 0 1; }
    #prompt.-plan-mode { color: $warning; }
    #prompt.-discuss-mode { color: $accent; }
    """
    BINDINGS = [
        Binding("ctrl+c", "interrupt", "Cancel turn", show=False, priority=True),
        Binding("ctrl+d", "quit_app", "Quit", show=False, priority=True),
        # ctrl+m is not usable here: terminals send the same byte for it as
        # for Enter, so it would fire the input's submit binding instead.
        Binding("ctrl+o", "pick_model", "Model", show=False, priority=True),
        Binding("ctrl+b", "toggle_sidebar", "Sidebar", show=False, priority=True),
        Binding("ctrl+1", "show_tab(0)", "Session", show=False, priority=True),
        Binding("ctrl+2", "show_tab(1)", "Git", show=False, priority=True),
        Binding("ctrl+3", "show_tab(2)", "Connections", show=False, priority=True),
        Binding("[", "cycle_tab(False)", "Prev tab", show=False),
        Binding("]", "cycle_tab(True)", "Next tab", show=False),
        Binding("g", "expand_latest", "Expand group", show=False),
        Binding("G", "jump_end", "Jump to end", show=False),
        Binding("up", "history_prev", "Prev", show=False),
        Binding("down", "history_next", "Next", show=False),
    ]

    def __init__(self, cfg: config.Config, sess: session.Session, mode: str,
                 history: list[dict[str, Any]], model_alias: str | None = None) -> None:
        super().__init__()
        self.cfg = cfg
        self.sess = sess
        self.mode = mode
        self.history = history
        self.model_alias = model_alias
        self._usage: events.Usage | None = None
        # Cumulative tokens for this session, by model alias (or the raw
        # model id when no alias applies) -- `/cost` sums these against
        # `eval.prices`, unlike `_usage` above which only holds the latest event.
        self._usage_totals: dict[str, dict[str, int]] = {}
        self._last_model: tuple[str | None, str] | None = None
        self._phase = "idle"
        self._turn_worker: Worker[None] | None = None
        self._input_history: InputHistory | None = None
        self._prefs = prefs.load()
        self._first_launch = not prefs.PATH.exists()
        self._sidebar_visible = bool(self._prefs.get("sidebar", False))
        self._git_refresh_timer: Timer | None = None

    def compose(self) -> ComposeResult:
        yield HeaderBar(id="header")
        with Horizontal(id="body"):
            yield Transcript(id="transcript")
            yield Sidebar(self.sess, id="sidebar")
        yield Static("", id="input-rule")
        with Horizontal(id="input-row"):
            yield Input(id="prompt", placeholder="❯ ", compact=True)
            yield Static("", id="mode-tag")
        yield StatusBar(id="status")

    def on_mount(self) -> None:
        # Deferred import: reads the CURRENT `omega.ui.tui.HISTORY`, so tests
        # that monkeypatch it before construction are respected.
        from . import HISTORY
        self._input_history = InputHistory(HISTORY)
        self.query_one(Sidebar).display = self._sidebar_visible
        self.query_one(Transcript).set_session(self.sess.id)
        self._refresh_status()
        self._apply_mode_style()
        self._update_input_rule()
        self._refresh_header()
        if self.history:
            self._show_resumed()
        else:
            self.query_one(Transcript).show_empty_state()
        self.set_focus(self.query_one("#prompt", Input))

    def _show_resumed(self) -> None:
        hist = self.history
        if hist and str(hist[-1].get("content", "")).startswith(session.RESUME_PREFIX):
            hist = hist[:-1]
        turns = sum(1 for m in hist if m.get("role") == "user")
        ago = format.relative_age(time.time() - self.sess.updated) if self.sess.updated else ""
        self.query_one(Transcript).add_resumed(self.sess.id, turns, len(hist), self.sess.cwd, ago)

    def _update_input_rule(self) -> None:
        rule = self.query_one("#input-rule", Static)
        basename = Path(self.sess.cwd).name or self.sess.cwd
        width = rule.size.width or 78
        bar = "─" * max(1, width - len(basename) - 1)
        rule.update(f"[dim]{bar} {basename}[/dim]")

    def _refresh_header(self, *, force: bool = False) -> None:
        self.run_worker(self._load_header(force=force), exclusive=False, thread=False)

    async def _load_header(self, *, force: bool) -> None:
        cwd = self.sess.cwd
        branch = None if force else _branch_cache.get(cwd)
        if branch is None:
            branch = await _lookup_branch(cwd)
            _branch_cache[cwd] = branch
        self.query_one(HeaderBar).set_state(cwd, branch, self.sess.id)

    def _role_name(self) -> str:
        return _MODE_ROLE.get(self.mode, "main")

    def _apply_mode_style(self) -> None:
        prompt = self.query_one("#prompt", Input)
        prompt.set_class(self.mode == "plan", "-plan-mode")
        prompt.set_class(self.mode == "discuss", "-discuss-mode")
        style = _MODE_TAG_STYLE.get(self.mode, "dim")
        self.query_one("#mode-tag", Static).update(f"[{style}]{self.mode}[/{style}]")

    def _refresh_status(self) -> None:
        role_name = self._role_name()
        alias, model = self._last_model or (None, "?")
        if self._last_model is None:
            # Before the first ModelUsed event of the session, fall back to a
            # static lookup so the status bar isn't blank on first render.
            try:
                role = self.cfg.model(self.model_alias) if self.model_alias else self.cfg.role(role_name)
                alias, model = role.alias, str(role.model)
            except Exception:
                alias, model = self.model_alias, "?"
        turns = sum(1 for m in self.history if m.get("role") == "user")
        state = StatusState(mode=self.mode, role_name=role_name, model=model, alias=alias,
                            session_id=self.sess.id, turns=turns, usage=self._usage,
                            phase=self._phase)
        self.query_one(StatusBar).set_state(state)

    def _schedule_git_refresh(self) -> None:
        if self._git_refresh_timer is not None:
            self._git_refresh_timer.stop()
        self._git_refresh_timer = self.set_timer(0.3, self._run_git_refresh)

    def _run_git_refresh(self) -> None:
        self._git_refresh_timer = None
        self.query_one(Sidebar).git_tab.refresh_repos()

    def emit(self, ev: events.Event) -> None:
        trace.append(self.sess.id, ev, self.sess.turns)
        # A rendering bug (an unescaped model string reaching rich's markup
        # parser, say) must never abort the turn the model is mid-way through
        # -- show one dim marker and keep going instead of propagating. The
        # inner try/except is deliberate: even the marker line's own render
        # must not be able to re-raise and kill the turn a second time.
        try:
            self._dispatch(ev)
        except Exception as e:
            # `add_dim` escapes its own text, so pass the raw message through --
            # escaping it here too would double-escape.
            try:
                self.query_one(Transcript).add_dim(
                    f"⚠ render error: {type(e).__name__}: {str(e)[:120]}")
            except Exception:
                pass

    def _dispatch(self, ev: events.Event) -> None:
        transcript = self.query_one(Transcript)
        sidebar = self.query_one(Sidebar)
        match ev:
            case events.Phase(state=state):
                self._phase = state
                transcript.note_phase(state)
                self._refresh_status()
            case events.TextDelta(text=text):
                transcript.add_text_delta(text)
            case events.ToolStart():
                transcript.add_tool_start(ev)
                sidebar.session_tab.record_tool_start(ev)
                if ev.name in ("find_tools", "call_tool"):
                    sidebar.connections_tab.refresh_status()
            case events.ToolEnd():
                transcript.add_tool_end(ev)
                sidebar.session_tab.record_tool_end(ev)
                if ev.name in ("write", "edit", "bash"):
                    self._schedule_git_refresh()
                if ev.name in ("find_tools", "call_tool"):
                    sidebar.connections_tab.refresh_status()
            case events.SubagentSpawned():
                transcript.add_subagent_spawned(ev)
                sidebar.session_tab.record_subagent_spawned(ev)
            case events.SubagentDone():
                transcript.add_subagent_done(ev)
                sidebar.session_tab.record_subagent_done(ev)
            case events.Compacted(note=note):
                transcript.add_compacted(ev)
                if not note.startswith("compaction skipped"):
                    self.sess.compactions += 1
            case events.MemoryWrite():
                transcript.add_memory_write(ev)
            case events.MemoryConsolidated():
                transcript.add_memory_consolidated(ev)
            case events.Error():
                transcript.add_error(ev)
            case events.Usage():
                self._usage = ev
                self._accumulate_usage(ev)
                sidebar.session_tab.record_usage(ev)
                transcript.note_usage(ev)
                self._refresh_status()
            case events.ModelUsed(alias=alias, model=model):
                self._last_model = (alias, model)
                sidebar.session_tab.record_model(alias, model)
                self._refresh_status()
            case events.Done(text=text):
                transcript.finalize_turn(text)
        if _Checkpoint is not None and isinstance(ev, _Checkpoint):
            transcript.add_checkpoint(ev)
        elif _Verified is not None and isinstance(ev, _Verified):
            transcript.add_verified(ev)
        elif _JobStarted is not None and isinstance(ev, _JobStarted):
            transcript.add_job_started(ev)
        elif _JobFinished is not None and isinstance(ev, _JobFinished):
            transcript.add_job_finished(ev)

    def _accumulate_usage(self, ev: events.Usage) -> None:
        alias = (self._last_model[0] or self._last_model[1]) if self._last_model else "?"
        totals = self._usage_totals.setdefault(
            alias, {"prompt": 0, "completion": 0, "cache_read": 0, "cache_write": 0})
        totals["prompt"] += ev.prompt_tokens
        totals["completion"] += ev.completion_tokens
        totals["cache_read"] += ev.cache_read
        totals["cache_write"] += ev.cache_write

    def _cost_text(self) -> str:
        if not self._usage_totals:
            return "no usage yet this session"
        from ...eval import prices

        lines = []
        grand_total = 0.0
        any_unpriced = False
        for alias, t in sorted(self._usage_totals.items()):
            cost = prices.estimate_cost(alias, t["prompt"], t["completion"])
            if cost is None:
                any_unpriced = True
                cost_text = "unknown"
            else:
                grand_total += cost
                cost_text = f"${cost:.4f}"
            lines.append(f"{alias}: {t['prompt']} in / {t['completion']} out "
                        f"(cache {t['cache_read']} read / {t['cache_write']} write) · {cost_text}")
        if len(self._usage_totals) > 1:
            suffix = " + unpriced models" if any_unpriced else ""
            lines.append(f"total: ${grand_total:.4f}{suffix}")
        return "\n".join(lines)

    def on_input_submitted(self, message: Input.Submitted) -> None:
        if message.input.id != "prompt":
            return
        text = message.value.strip()
        message.input.value = ""
        if not text:
            return
        if self._input_history is not None:
            self._input_history.append(text)
        if text == "?" or text.startswith("/"):
            self._handle_command(text)
        else:
            self._start_turn(text)

    def _unknown_command(self, text: str) -> str:
        word = text.split()[0] if text.split() else text
        match = difflib.get_close_matches(word, _COMMANDS, n=1)
        suggestion = f" — did you mean {match[0]}?" if match else ""
        return f"unknown command: {text}{suggestion}"

    def _handle_command(self, text: str) -> None:
        transcript = self.query_one(Transcript)
        if text in ("/plan", "/build", "/discuss"):
            new_mode = text[1:]
            if new_mode == "discuss" and "discuss" not in loop.MODES:
                transcript.add_dim("discuss mode not available in this build")
                return
            self.mode = new_mode
            transcript.add_mode_switch(self.mode)
            self._apply_mode_style()
            self._refresh_status()
        elif text == "/mode":
            transcript.add_dim(f"mode: {self.mode}")
        elif text == "/memory-gc":
            self.run_worker(self._memory_gc(), exclusive=False, thread=False)
        elif text == "/model":
            self.run_worker(self._pick_model(), exclusive=False, thread=False)
        elif text.startswith("/model "):
            self._set_model(text[len("/model "):].strip())
        elif text == "/sidebar":
            self.action_toggle_sidebar()
        elif text == "/cost":
            transcript.add_dim(self._cost_text())
        elif text == "/export" or text.startswith("/export "):
            path = text[len("/export"):].strip() or None
            out = export.write(self.history, self.sess.id, path)
            transcript.add_dim(f"exported to {out}")
        elif text == "/compact":
            self.run_worker(self._force_compact(), exclusive=False, thread=False)
        elif text == "/undo" or text.startswith("/undo "):
            arg = text[len("/undo"):].strip()
            steps = int(arg) if arg.isdigit() else 1
            self.run_worker(self._undo(steps), exclusive=False, thread=False)
        elif text == "/diff":
            self.run_worker(self._diff(), exclusive=False, thread=False)
        elif text == "/verify":
            self.run_worker(self._verify(), exclusive=False, thread=False)
        elif text == "/sessions":
            self.run_worker(self._pick_session(), exclusive=False, thread=False)
        elif text in ("/help", "?"):
            transcript.add_dim(_HELP_TEXT)
        elif text == "/quit":
            self.action_quit_app()
        else:
            transcript.add_dim(self._unknown_command(text))

    async def _memory_gc(self) -> None:
        transcript = self.query_one(Transcript)
        transcript.add_dim(await consolidate.run(self.cfg, "project", force=True))
        transcript.add_dim(await consolidate.run(self.cfg, "global", force=True))

    def _context_limit(self) -> int:
        role = self.cfg.model(self.model_alias) if self.model_alias else self.cfg.role(self._role_name())
        return role.context

    async def _force_compact(self) -> None:
        transcript = self.query_one(Transcript)
        limit = self._context_limit()
        note = await compact.maybe_compact(self.cfg, self.history, used=limit, limit=limit)
        transcript.add_dim(note or "nothing to compact")

    async def _undo(self, steps: int) -> None:
        transcript = self.query_one(Transcript)
        try:
            from ... import checkpoint
        except ImportError:
            transcript.add_dim("not available in this build")
            return
        plural = "" if steps == 1 else "s"
        confirmed = await self.push_screen_wait(
            ConfirmScreen("undo", {}, f"revert the last {steps} turn{plural}?"))
        if not confirmed:
            transcript.add_dim("undo cancelled")
            return
        note = await asyncio.to_thread(checkpoint.undo, self.sess.id, steps, cwd=self.sess.cwd)
        transcript.add_dim(note)

    async def _diff(self) -> None:
        transcript = self.query_one(Transcript)
        try:
            from ... import checkpoint
        except ImportError:
            transcript.add_dim("not available in this build")
            return
        text = await asyncio.to_thread(checkpoint.diff, self.sess.id, cwd=self.sess.cwd)
        await self.push_screen_wait(DiffScreen(None, "checkpoint diff", diff_text=text))

    async def _verify(self) -> None:
        transcript = self.query_one(Transcript)
        try:
            from ... import verify
        except ImportError:
            transcript.add_dim("not available in this build")
            return
        checks = await asyncio.to_thread(verify.detect, self.sess.cwd)
        if not checks:
            transcript.add_dim("no checks detected for this project")
            return
        results = await asyncio.to_thread(verify.run, checks, self.sess.cwd)
        ok = all(r.ok for r in results)
        summary = ", ".join(f"{r.check.name} {'✓' if r.ok else '✗'}" for r in results)
        transcript.add_dim(("✓ verified: " if ok else "✗ verification failed: ") + summary)

    async def _pick_session(self) -> None:
        transcript = self.query_one(Transcript)
        rows = [s for s in session.all_sessions() if s.cwd == self.sess.cwd and s.id != self.sess.id][:20]
        if not rows:
            transcript.add_dim("no other sessions for this directory")
            return
        chosen = await self.push_screen_wait(SessionsScreen(rows))
        if chosen:
            self._resume_session(chosen)

    def _resume_session(self, sid: str) -> None:
        new_sess = session.load(sid)
        self.sess = new_sess
        self.mode = new_sess.mode
        self.history.clear()
        self.history.extend(new_sess.history)
        self.model_alias = new_sess.model_override
        self._usage = None
        self._usage_totals = {}
        self._last_model = None
        transcript = self.query_one(Transcript)
        transcript.set_session(new_sess.id)
        turns = sum(1 for m in self.history if m.get("role") == "user")
        ago = format.relative_age(time.time() - new_sess.updated) if new_sess.updated else ""
        transcript.add_resumed(new_sess.id, turns, len(self.history), new_sess.cwd, ago)
        self.query_one(Sidebar).session_tab.set_session(new_sess)
        self._apply_mode_style()
        self._refresh_status()
        self._refresh_header(force=True)

    async def _pick_model(self) -> None:
        alias = await self.push_screen_wait(ModelPickerScreen(self.cfg.models, self.model_alias))
        if alias:
            self._set_model(alias)

    def _set_model(self, arg: str) -> None:
        try:
            alias = self.cfg.resolve_alias(arg)
        except SystemExit as e:
            self.query_one(Transcript).add_dim(str(e))
            return
        self.model_alias = alias
        self.sess.model_override = alias
        role = self.cfg.model(alias)
        self._last_model = (alias, role.model)
        # The stale token/limit pairing belongs to the model being replaced --
        # showing it against the new model's name would misreport its context
        # window until the next Usage event (which may be rounds away).
        self._usage = None
        self.query_one(Transcript).add_dim(f"model: {alias} · {role.model}")
        self._refresh_status()

    def action_pick_model(self) -> None:
        self.run_worker(self._pick_model(), exclusive=False, thread=False)

    def action_toggle_sidebar(self) -> None:
        self._sidebar_visible = not self._sidebar_visible
        self.query_one(Sidebar).display = self._sidebar_visible
        self._prefs["sidebar"] = self._sidebar_visible
        prefs.save(self._prefs)

    def action_show_tab(self, index: int) -> None:
        self.query_one(Sidebar).show_tab(index)

    def action_cycle_tab(self, forward: bool) -> None:
        self.query_one(Sidebar).cycle_tab(forward)

    def action_expand_latest(self) -> None:
        self.query_one(Transcript).expand_latest()

    def action_jump_end(self) -> None:
        self.query_one(Transcript).jump_to_end()

    def _start_turn(self, text: str) -> None:
        self.history.append({"role": "user", "content": text})
        self.query_one(Transcript).add_user_message(text, self.mode)
        self.query_one(Sidebar).session_tab.reset_turn()
        self.query_one("#prompt", Input).disabled = True
        self._turn_worker = self.run_worker(self._run_turn(), exclusive=True, thread=False)

    async def _run_turn(self) -> None:
        interrupted = False
        try:
            await loop.run_turn(self.cfg, self.history, mode=self.mode, emit=self.emit,
                                model=self.model_alias)
        except asyncio.CancelledError:
            interrupted = True
        except Exception as e:
            self.emit(events.Error(message=f"{type(e).__name__}: {e}"))
        finally:
            try:
                self.sess.close_turn(self.history, self.mode, interrupted)
            except Exception as e:
                self.query_one(Transcript).add_error(events.Error(f"could not save session: {e}"))
            self._turn_worker = None
            self.emit(events.Phase("idle"))
            if self._git_refresh_timer is not None:
                self._git_refresh_timer.stop()
                self._git_refresh_timer = None
            sidebar = self.query_one(Sidebar)
            sidebar.git_tab.refresh_repos()
            sidebar.connections_tab.refresh_status()
            self._refresh_header(force=True)
            prompt = self.query_one("#prompt", Input)
            prompt.disabled = False
            self.set_focus(prompt)

    def action_interrupt(self) -> None:
        if self._turn_worker is not None:
            self._turn_worker.cancel()

    def action_quit_app(self) -> None:
        prompt = self.query_one("#prompt", Input)
        if prompt.value:
            prompt.action_delete_right()
            return
        if self._turn_worker is not None:
            self._turn_worker.cancel()
        self.exit()

    def action_history_prev(self) -> None:
        self._cycle_history(forward=False)

    def action_history_next(self) -> None:
        self._cycle_history(forward=True)

    def _cycle_history(self, forward: bool) -> None:
        if self._input_history is None:
            return
        prompt = self.query_one("#prompt", Input)
        value = (self._input_history.next(prompt.value) if forward
                 else self._input_history.prev(prompt.value))
        if value is not None:
            prompt.value = value
            prompt.action_end()

    async def confirm(self, name: str, args: dict[str, Any], why: str) -> bool:
        return await self.push_screen_wait(ConfirmScreen(name, args, why))

    async def ask_user(self, question: str, options: list[events.Option], multi_select: bool) -> str:
        return await self.push_screen_wait(AskUserScreen(question, options, multi_select))
