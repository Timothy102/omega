"""OmegaApp: composition, key bindings, and turn execution. Rendering logic
lives in transcript.py/sidebar.py/status.py; this module only translates
events into calls on their typed widget methods, never the reverse."""
from __future__ import annotations

import asyncio
import difflib
import time
from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Input
from textual.worker import Worker

from ... import config, events, loop, session
from ...memory import consolidate
from . import prefs
from .history import InputHistory
from .modals import AskUserScreen, ConfirmScreen, ModelPickerScreen
from .sidebar import Sidebar
from .status import StatusBar, StatusState
from .transcript import Transcript

_MODE_ROLE = {"build": "main", "plan": "plan"}

_COMMANDS = ["/plan", "/build", "/mode", "/memory-gc", "/model", "/sidebar", "/help", "/quit"]

_HELP_TEXT = (
    "ctrl+c cancel turn  ·  ctrl+o /model switch model  ·  ctrl+b toggle side panel\n"
    "ctrl+1/2/3 or [ ] switch side-panel tab  ·  up/down input history\n"
    "/plan /build switch modes  ·  /model <alias>  ·  /mode  ·  /sidebar  ·  /memory-gc  ·  /quit")


def _relative_age(seconds: float) -> str:
    seconds = max(0.0, seconds)
    if seconds < 60:
        return f"{int(seconds)}s"
    minutes = seconds / 60
    if minutes < 60:
        return f"{int(minutes)}m"
    hours = minutes / 60
    if hours < 24:
        return f"{int(hours)}h"
    return f"{int(hours / 24)}d"


class OmegaApp(App[None]):
    CSS = """
    Screen { layout: vertical; }
    #body { height: 1fr; }
    #prompt { dock: bottom; }
    #prompt.-plan-mode { color: $warning; }
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
        self._last_model: tuple[str | None, str] | None = None
        self._phase = "idle"
        self._turn_worker: Worker[None] | None = None
        self._input_history: InputHistory | None = None
        self._prefs = prefs.load()
        self._first_launch = not prefs.PATH.exists()
        self._sidebar_visible = bool(self._prefs.get("sidebar", False))

    def compose(self) -> ComposeResult:
        with Horizontal(id="body"):
            yield Transcript(id="transcript")
            yield Sidebar(self.sess, id="sidebar")
        yield StatusBar(id="status")
        yield Input(id="prompt", placeholder=f"{self.mode}› ")

    def on_mount(self) -> None:
        # Deferred import: reads the CURRENT `omega.ui.tui.HISTORY`, so tests
        # that monkeypatch it before construction are respected.
        from . import HISTORY
        self._input_history = InputHistory(HISTORY)
        self.query_one(Sidebar).display = self._sidebar_visible
        self.query_one(Transcript).set_session(self.sess.id)
        self._refresh_status()
        self._apply_mode_style()
        if self.history:
            self._show_resumed()
        if self._first_launch:
            self.query_one(Transcript).add_dim("ctrl+b toggles the side panel")
        self.set_focus(self.query_one("#prompt", Input))

    def _show_resumed(self) -> None:
        hist = self.history
        if hist and str(hist[-1].get("content", "")).startswith(session.RESUME_PREFIX):
            hist = hist[:-1]
        turns = sum(1 for m in hist if m.get("role") == "user")
        ago = _relative_age(time.time() - self.sess.updated) if self.sess.updated else ""
        self.query_one(Transcript).add_resumed(self.sess.id, turns, len(hist), self.sess.cwd, ago)

    def _role_name(self) -> str:
        return _MODE_ROLE.get(self.mode, "main")

    def _apply_mode_style(self) -> None:
        prompt = self.query_one("#prompt", Input)
        prompt.placeholder = f"{self.mode}› "
        prompt.set_class(self.mode == "plan", "-plan-mode")

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

    def emit(self, ev: events.Event) -> None:
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
            case events.ToolEnd():
                transcript.add_tool_end(ev)
                sidebar.session_tab.record_tool_end(ev)
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
                sidebar.session_tab.record_usage(ev)
                self._refresh_status()
            case events.ModelUsed(alias=alias, model=model):
                self._last_model = (alias, model)
                sidebar.session_tab.record_model(alias, model)
                self._refresh_status()
            case events.Done(text=text):
                transcript.finalize_turn(text)

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
        if text in ("/plan", "/build"):
            self.mode = text[1:]
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
