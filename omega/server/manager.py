"""Owns the daemon's live task state: one child worker process per running
task (see worker.py for the child side), WebSocket subscriber fanout, and the
answer/confirm relay between HTTP callers and a worker blocked on
ask_user/confirm.

Why a child process per task at all: `tools.CONFIRM`/`ASK_USER`/`SESSION_ID`/
`TAINTED`, `subagent.EMIT` and `os.getcwd()` (bash's cwd) are process-wide
globals in omega's agent loop -- running two tasks in one process would have
them race and clobber each other. A worker is spawned with
`_worker_argv(task)` (overridable so tests can point it at a scripted stub
instead of the real `python -m omega.server.worker`) and talked to over
newline-delimited JSON on stdin/stdout.
"""
from __future__ import annotations

import asyncio
import json
import secrets
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .. import tasks
from ..eval import prices

WorkerArgv = Callable[[tasks.Task], list[str]]

# Event types that change something a client would want to see on the task
# list or overview -- persisted immediately. Everything else (TextDelta,
# ToolStart/End, ...) is still forwarded live to WS subscribers but does not
# hit disk on every line, which streaming token deltas would do many times a
# second.
_PERSIST_ON = {"Phase", "ModelUsed", "Usage", "Done", "Error", "Checkpoint",
              "Verified", "ask_user_request", "confirm_request", "fatal"}


def default_worker_argv(task: tasks.Task) -> list[str]:
    return [sys.executable, "-m", "omega.server.worker", task.id]


@dataclass
class RunningTask:
    proc: asyncio.subprocess.Process
    reader_task: asyncio.Task[None] | None = None
    stderr_task: asyncio.Task[None] | None = None
    turn_busy: bool = False
    turn_started: float | None = None
    last_alias: str | None = None
    # Pending RPC-style requests (currently just "jobs") keyed by request_id --
    # unlike prompt/answer/confirm these expect exactly one reply back.
    pending_replies: dict[str, asyncio.Future[Any]] = field(default_factory=dict)


class TaskManager:
    def __init__(self, worker_argv: WorkerArgv = default_worker_argv) -> None:
        self._worker_argv = worker_argv
        self._running: dict[str, RunningTask] = {}
        self._task_subs: dict[str, set[asyncio.Queue[str]]] = {}
        self._overview_subs: set[asyncio.Queue[str]] = set()

    # -- subscriptions ----------------------------------------------------

    def subscribe_task(self, task_id: str) -> asyncio.Queue[str]:
        q: asyncio.Queue[str] = asyncio.Queue()
        self._task_subs.setdefault(task_id, set()).add(q)
        return q

    def unsubscribe_task(self, task_id: str, q: asyncio.Queue[str]) -> None:
        self._task_subs.get(task_id, set()).discard(q)

    def subscribe_overview(self) -> asyncio.Queue[str]:
        q: asyncio.Queue[str] = asyncio.Queue()
        self._overview_subs.add(q)
        return q

    def unsubscribe_overview(self, q: asyncio.Queue[str]) -> None:
        self._overview_subs.discard(q)

    def _publish_task(self, task_id: str, line: str) -> None:
        for q in self._task_subs.get(task_id, ()):
            q.put_nowait(line)

    def publish_overview_task(self, task: tasks.Task) -> None:
        from .models import TaskOut
        line = json.dumps({"type": "task", "task": TaskOut.from_task(task).model_dump()})
        for q in self._overview_subs:
            q.put_nowait(line)

    def publish_overview_terminals(self, terminals: list[dict[str, Any]]) -> None:
        line = json.dumps({"type": "terminals", "terminals": terminals})
        for q in self._overview_subs:
            q.put_nowait(line)

    def is_running(self, task_id: str) -> bool:
        running = self._running.get(task_id)
        return running is not None and running.proc.returncode is None

    # -- worker lifecycle ---------------------------------------------------

    async def _ensure_started(self, task: tasks.Task) -> RunningTask:
        running = self._running.get(task.id)
        if running is not None and running.proc.returncode is None:
            return running
        argv = self._worker_argv(task)
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        running = RunningTask(proc=proc)
        self._running[task.id] = running
        running.reader_task = asyncio.create_task(self._read_worker(task.id, running))
        running.stderr_task = asyncio.create_task(self._drain_stderr(proc))
        return running

    async def _drain_stderr(self, proc: asyncio.subprocess.Process) -> None:
        assert proc.stderr is not None
        async for _line in proc.stderr:
            pass  # worker tracebacks land in the daemon's own log via app.py

    async def _send(self, running: RunningTask, msg: dict[str, Any]) -> None:
        assert running.proc.stdin is not None
        running.proc.stdin.write((json.dumps(msg) + "\n").encode())
        await running.proc.stdin.drain()

    async def send_prompt(self, task: tasks.Task, text: str) -> None:
        running = await self._ensure_started(task)
        running.turn_started = time.time()
        task.status = "running"
        task.save()
        self.publish_overview_task(task)
        await self._send(running, {"cmd": "prompt", "text": text})

    async def send_answer(self, task_id: str, request_id: str, answer: str) -> bool:
        running = self._running.get(task_id)
        if running is None:
            return False
        await self._send(running, {"cmd": "answer", "request_id": request_id, "answer": answer})
        self._mark_resumed(task_id)
        return True

    async def send_confirm(self, task_id: str, request_id: str, allow: bool) -> bool:
        running = self._running.get(task_id)
        if running is None:
            return False
        await self._send(running, {"cmd": "confirm", "request_id": request_id, "allow": allow})
        self._mark_resumed(task_id)
        return True

    def _mark_resumed(self, task_id: str) -> None:
        task = tasks.get(task_id)
        if task is not None and task.status == "waiting_input":
            task.status = "running"
            task.save()
            self.publish_overview_task(task)

    async def cancel(self, task_id: str) -> bool:
        running = self._running.get(task_id)
        if running is None:
            return False
        await self._send(running, {"cmd": "cancel"})
        return True

    async def set_model(self, task_id: str, model: str | None) -> None:
        running = self._running.get(task_id)
        if running is not None:
            await self._send(running, {"cmd": "set_model", "model": model})

    async def set_mode(self, task_id: str, mode: str) -> None:
        running = self._running.get(task_id)
        if running is not None:
            await self._send(running, {"cmd": "set_mode", "mode": mode})

    async def shutdown_task(self, task_id: str) -> None:
        running = self._running.get(task_id)
        if running is None:
            return
        try:
            await self._send(running, {"cmd": "shutdown"})
        except (BrokenPipeError, ConnectionResetError):
            pass
        try:
            await asyncio.wait_for(running.proc.wait(), timeout=5)
        except TimeoutError:
            running.proc.kill()

    async def shutdown_all(self) -> None:
        await asyncio.gather(*(self.shutdown_task(tid) for tid in list(self._running)),
                             return_exceptions=True)

    async def get_jobs(self, task_id: str, timeout: float = 5.0) -> list[dict[str, Any]]:
        """Background `bash(..., background=True)` jobs live in the worker
        process's `tools._JOBS`, not here -- ask it over the same stdio
        channel and wait for the one "jobs" reply that request_id names."""
        running = self._running.get(task_id)
        if running is None:
            return []
        request_id = secrets.token_hex(6)
        fut: asyncio.Future[list[dict[str, Any]]] = asyncio.get_event_loop().create_future()
        running.pending_replies[request_id] = fut
        await self._send(running, {"cmd": "jobs", "request_id": request_id})
        try:
            return await asyncio.wait_for(fut, timeout)
        except TimeoutError:
            return []
        finally:
            running.pending_replies.pop(request_id, None)

    # -- event stream ---------------------------------------------------

    async def _read_worker(self, task_id: str, running: RunningTask) -> None:
        assert running.proc.stdout is not None
        try:
            async for raw in running.proc.stdout:
                line = raw.decode(errors="replace").strip()
                if not line:
                    continue
                if self._resolve_reply(running, line):
                    continue
                self._publish_task(task_id, line)
                self._apply_event(task_id, running, line)
        finally:
            self._running.pop(task_id, None)
            task = tasks.get(task_id)
            if task is not None and task.status in ("running", "waiting_input"):
                task.status = "failed"
                task.save()
                self.publish_overview_task(task)

    def _resolve_reply(self, running: RunningTask, line: str) -> bool:
        """Handles an RPC-style reply (currently only "jobs") -- these are not
        `events.Event`s and must not be published to WS subscribers or
        mistaken for task state."""
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            return False
        if obj.get("type") != "jobs":
            return False
        fut = running.pending_replies.get(obj.get("request_id"))
        if fut is not None and not fut.done():
            fut.set_result(obj.get("jobs", []))
        return True

    def _apply_event(self, task_id: str, running: RunningTask, line: str) -> None:
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            return
        kind = obj.get("type")
        if kind not in _PERSIST_ON:
            return
        task = tasks.get(task_id)
        if task is None:
            return

        if kind == "Phase":
            state = str(obj.get("state", ""))
            task.phase = state
            if state != "idle" and task.status != "waiting_input":
                task.status = "running"
        elif kind == "ModelUsed":
            running.last_alias = obj.get("alias")
            task.model = running.last_alias or obj.get("model") or task.model
        elif kind == "Usage":
            prompt_tokens = int(obj.get("prompt_tokens") or 0)
            completion_tokens = int(obj.get("completion_tokens") or 0)
            task.tokens_in += prompt_tokens
            task.tokens_out += completion_tokens
            if running.last_alias:
                delta = prices.estimate_cost(running.last_alias, prompt_tokens, completion_tokens)
                if delta is not None:
                    task.cost_usd = (task.cost_usd or 0.0) + delta
        elif kind in ("ask_user_request", "confirm_request"):
            task.status = "waiting_input"
        elif kind in ("Done", "Error", "fatal"):
            task.status = "failed" if kind != "Done" else "done"
            task.phase = "idle"
            if running.turn_started is not None:
                task.elapsed_s += time.time() - running.turn_started
                running.turn_started = None

        task.save()
        self.publish_overview_task(task)
