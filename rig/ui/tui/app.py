"""RigApp: composition, key bindings, and turn execution. Rendering logic
lives in transcript.py/activity.py/status.py; this module only translates
events into calls on their typed widget methods, never the reverse."""
from __future__ import annotations

import asyncio
from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Input
from textual.worker import Worker

from ... import config, events, loop, session
from ...memory import consolidate
from .activity import ActivityPanel
from .history import InputHistory
from .modals import AskUserScreen, ConfirmScreen
from .status import StatusBar, StatusState
from .transcript import Transcript

_MODE_ROLE = {"build": "main", "plan": "plan"}


class RigApp(App[None]):
    CSS = """
    Screen { layout: vertical; }
    #body { height: 1fr; }
    #prompt { dock: bottom; }
    """
    BINDINGS = [
        Binding("ctrl+c", "interrupt", "Cancel turn", show=False, priority=True),
        Binding("ctrl+d", "quit_app", "Quit", show=False, priority=True),
        Binding("up", "history_prev", "Prev", show=False),
        Binding("down", "history_next", "Next", show=False),
    ]

    def __init__(self, cfg: config.Config, sess: session.Session, mode: str,
                 history: list[dict[str, Any]]) -> None:
        super().__init__()
        self.cfg = cfg
        self.sess = sess
        self.mode = mode
        self.history = history
        self._usage: events.Usage | None = None
        self._turn_worker: Worker[None] | None = None
        self._input_history: InputHistory | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="body"):
            yield Transcript(id="transcript")
            yield ActivityPanel(id="activity")
        yield StatusBar(id="status")
        yield Input(id="prompt", placeholder=f"{self.mode}› ")

    def on_mount(self) -> None:
        # Deferred import: reads the CURRENT `rig.ui.tui.HISTORY`, so tests
        # that monkeypatch it before construction are respected.
        from . import HISTORY
        self._input_history = InputHistory(HISTORY)
        self._refresh_status()
        if self.history:
            self._show_resumed()
        self.set_focus(self.query_one("#prompt", Input))

    def _show_resumed(self) -> None:
        hist = self.history
        if hist and str(hist[-1].get("content", "")).startswith(session.RESUME_PREFIX):
            hist = hist[:-1]
        turns = sum(1 for m in hist if m.get("role") == "user")
        self.query_one(Transcript).add_resumed(self.sess.id, turns, len(hist), self.sess.cwd)

    def _role_name(self) -> str:
        return _MODE_ROLE.get(self.mode, "main")

    def _refresh_status(self) -> None:
        role_name = self._role_name()
        try:
            model = str(getattr(self.cfg.role(role_name), "model", "?"))
        except Exception:
            model = "?"
        turns = sum(1 for m in self.history if m.get("role") == "user")
        state = StatusState(mode=self.mode, role_name=role_name, model=model,
                            session_id=self.sess.id, turns=turns, usage=self._usage)
        self.query_one(StatusBar).set_state(state)

    def emit(self, ev: events.Event) -> None:
        transcript = self.query_one(Transcript)
        activity = self.query_one(ActivityPanel)
        match ev:
            case events.TextDelta(text=text):
                transcript.add_text_delta(text)
            case events.ToolStart():
                transcript.add_tool_start(ev)
                activity.start_tool(ev)
            case events.ToolEnd():
                transcript.add_tool_end(ev)
                activity.end_tool(ev)
            case events.SubagentSpawned():
                transcript.add_subagent_spawned(ev)
                activity.start_subagent(ev)
            case events.SubagentDone():
                transcript.add_subagent_done(ev)
                activity.end_subagent(ev)
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
        if text.startswith("/"):
            self._handle_command(text)
        else:
            self._start_turn(text)

    def _handle_command(self, text: str) -> None:
        transcript = self.query_one(Transcript)
        if text in ("/plan", "/build"):
            self.mode = text[1:]
            transcript.add_mode_switch(self.mode)
            self._refresh_status()
        elif text == "/memory-gc":
            self.run_worker(self._memory_gc(), exclusive=False, thread=False)
        elif text == "/quit":
            self.action_quit_app()
        else:
            transcript.add_dim(f"unknown command: {text}")

    async def _memory_gc(self) -> None:
        transcript = self.query_one(Transcript)
        transcript.add_dim(await consolidate.run(self.cfg, "project", force=True))
        transcript.add_dim(await consolidate.run(self.cfg, "global", force=True))

    def _start_turn(self, text: str) -> None:
        self.history.append({"role": "user", "content": text})
        self.query_one(Transcript).add_user_message(text)
        self.query_one("#prompt", Input).disabled = True
        self._turn_worker = self.run_worker(self._run_turn(), exclusive=True, thread=False)

    async def _run_turn(self) -> None:
        interrupted = False
        try:
            await loop.run_turn(self.cfg, self.history, mode=self.mode, emit=self.emit)
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
            self._refresh_status()
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
