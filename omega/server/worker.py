"""The child process spawned per running task (see manager.py). One process =
one task, so the process-wide globals `tools.CONFIRM`/`ASK_USER`/`SESSION_ID`
and `os.getcwd()` are safe to set here without racing any other task.

Wire protocol, newline-delimited JSON both ways:

Parent -> child (stdin):
  {"cmd": "prompt", "text": str}
  {"cmd": "answer", "request_id": str, "answer": str}
  {"cmd": "confirm", "request_id": str, "allow": bool}
  {"cmd": "cancel"}
  {"cmd": "set_model", "model": str | None}
  {"cmd": "set_mode", "mode": str}
  {"cmd": "shutdown"}

Child -> parent (stdout), one JSON object per line:
  {"type": "worker_ready"}
  {"type": <events.Event dataclass name>, ...fields, "t": epoch, "turn": n}
  {"type": "ask_user_request", "request_id", "question", "options", "multi_select"}
  {"type": "confirm_request", "request_id", "tool", "args_preview", "why"}
  {"type": "fatal", "message": str}   -- unrecoverable startup failure
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import secrets
import sys
import time
from typing import Any

from .. import config, events, loop, session, tasks, tools
from ..session import Message
from ..ui import format


class Worker:
    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        self.turn = 0
        self.mode = "build"
        self.model: str | None = None
        self.history: list[Message] = []
        self.cfg: config.Config | None = None
        self.sess: session.Session | None = None
        self._pending: dict[str, asyncio.Future[Any]] = {}
        self._turn_task: asyncio.Task[None] | None = None

    def _emit_line(self, obj: dict[str, Any]) -> None:
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()

    def emit(self, ev: events.Event) -> None:
        self._emit_line({"type": type(ev).__name__, **dataclasses.asdict(ev),
                         "t": time.time(), "turn": self.turn})

    async def confirm(self, name: str, args: dict[str, Any], why: str) -> bool:
        request_id = secrets.token_hex(8)
        fut: asyncio.Future[bool] = asyncio.get_event_loop().create_future()
        self._pending[request_id] = fut
        try:
            preview = format.describe_call(name, args)
        except Exception:
            preview = str(args)
        self._emit_line({"type": "confirm_request", "request_id": request_id,
                         "tool": name, "args_preview": preview, "why": why})
        return await fut

    async def ask_user(self, question: str, options: list[events.Option],
                       multi_select: bool) -> str:
        request_id = secrets.token_hex(8)
        fut: asyncio.Future[str] = asyncio.get_event_loop().create_future()
        self._pending[request_id] = fut
        self._emit_line({"type": "ask_user_request", "request_id": request_id,
                         "question": question, "options": list(options),
                         "multi_select": multi_select})
        return await fut

    def _resolve(self, request_id: object, value: Any) -> None:
        if not isinstance(request_id, str):
            return
        fut = self._pending.pop(request_id, None)
        if fut is not None and not fut.done():
            fut.set_result(value)

    async def _run_turn(self, text: str) -> None:
        assert self.cfg is not None and self.sess is not None
        self.turn += 1
        self.history.append({"role": "user", "content": text})
        interrupted = False
        try:
            await loop.run_turn(self.cfg, self.history, mode=self.mode,
                                emit=self.emit, model=self.model)
        except asyncio.CancelledError:
            interrupted = True
        except Exception as e:
            self.emit(events.Error(message=f"{type(e).__name__}: {e}"))
        finally:
            try:
                self.sess.close_turn(self.history, self.mode, interrupted)
            except Exception as e:
                self._emit_line({"type": "fatal", "message": f"could not save session: {e}"})
            self._turn_task = None

    async def _dispatch(self, cmd: dict[str, Any]) -> bool:
        """Returns False when the worker should exit."""
        kind = cmd.get("cmd")
        if kind == "prompt":
            if self._turn_task is not None and not self._turn_task.done():
                self._emit_line({"type": "error", "message": "a turn is already running"})
            else:
                self._turn_task = asyncio.create_task(self._run_turn(str(cmd.get("text", ""))))
        elif kind == "answer":
            self._resolve(cmd.get("request_id"), str(cmd.get("answer", "")))
        elif kind == "confirm":
            self._resolve(cmd.get("request_id"), bool(cmd.get("allow", False)))
        elif kind == "cancel":
            if self._turn_task is not None:
                self._turn_task.cancel()
        elif kind == "set_mode":
            self.mode = str(cmd.get("mode", self.mode))
        elif kind == "set_model":
            self.model = cmd.get("model") or None
        elif kind == "jobs":
            self._emit_line({"type": "jobs", "request_id": cmd.get("request_id"),
                             "jobs": tools.list_jobs()})
        elif kind == "shutdown":
            if self._turn_task is not None:
                self._turn_task.cancel()
                await asyncio.gather(self._turn_task, return_exceptions=True)
            return False
        return True

    async def run(self) -> None:
        task = tasks.get(self.task_id)
        if task is None:
            self._emit_line({"type": "fatal", "message": f"no such task {self.task_id!r}"})
            return
        os.chdir(task.cwd)
        self.cfg = config.load()
        self.mode = task.mode
        self.model = task.model
        self.sess = session.load(self.task_id)
        self.history = self.sess.history
        tools.SESSION_ID = self.sess.id
        tools.CONFIRM = self.confirm
        tools.ASK_USER = self.ask_user
        self.turn = self.sess.turns

        reader = await _stdin_reader()
        self._emit_line({"type": "worker_ready"})
        while True:
            raw = await reader.readline()
            if not raw:
                break
            try:
                cmd = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not await self._dispatch(cmd):
                break


async def _stdin_reader() -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    loop_ = asyncio.get_event_loop()
    await loop_.connect_read_pipe(lambda: protocol, sys.stdin)
    return reader


async def _amain(task_id: str) -> None:
    await Worker(task_id).run()


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: python -m omega.server.worker <task_id>", file=sys.stderr)
        raise SystemExit(2)
    asyncio.run(_amain(sys.argv[1]))


if __name__ == "__main__":
    main()
