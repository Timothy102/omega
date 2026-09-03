"""Server-side PTY terminals: `pty.fork()` runs the user's login shell, kept
alive for the life of the daemon (surviving Omega.app restarts, per Phase 9
of the plan) and relayed byte-for-byte over `/ws/terminals/{id}`."""
from __future__ import annotations

import asyncio
import fcntl
import json
import os
import pty
import secrets
import signal
import struct
import termios
import time
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect

from . import auth
from .models import TerminalCreateIn, TerminalOut

READ_CHUNK = 4096


@dataclass
class Terminal:
    id: str
    task_id: str | None
    cwd: str
    created: float
    pid: int
    master_fd: int
    subscribers: set[asyncio.Queue[bytes]] = field(default_factory=set)
    reader_task: asyncio.Task[None] | None = None
    alive: bool = True

    def to_out(self) -> TerminalOut:
        return TerminalOut(id=self.id, task_id=self.task_id, pid=self.pid,
                           cwd=self.cwd, created=self.created)


class TerminalManager:
    def __init__(self) -> None:
        self._terminals: dict[str, Terminal] = {}

    def list(self) -> list[Terminal]:
        return sorted(self._terminals.values(), key=lambda t: t.created)

    def get(self, terminal_id: str) -> Terminal | None:
        return self._terminals.get(terminal_id)

    def create(self, task_id: str | None, cwd: str | None) -> Terminal:
        work_dir = os.path.expanduser(cwd) if cwd else str(Path.home())
        if not Path(work_dir).is_dir():
            raise ValueError(f"{work_dir!r} is not a directory")
        shell = os.environ.get("SHELL", "/bin/zsh")

        pid, master_fd = pty.fork()
        if pid == 0:
            # Child: exec the login shell in the requested cwd. os._exit on
            # any failure here -- a raised exception in the forked child would
            # otherwise re-enter the parent's (already-forked) asyncio state.
            try:
                os.chdir(work_dir)
                os.execvp(shell, [shell, "-l"])
            except Exception:
                os._exit(1)

        term = Terminal(id=secrets.token_hex(6), task_id=task_id, cwd=work_dir,
                        created=time.time(), pid=pid, master_fd=master_fd)
        self._terminals[term.id] = term
        term.reader_task = asyncio.create_task(self._pump(term))
        return term

    async def _pump(self, term: Terminal) -> None:
        """One background reader per terminal, fanning bytes out to every
        subscribed WS connection -- lets several viewers watch the same shell."""
        loop = asyncio.get_event_loop()
        try:
            while True:
                try:
                    data = await loop.run_in_executor(None, os.read, term.master_fd, READ_CHUNK)
                except OSError:
                    break
                if not data:
                    break
                for q in term.subscribers:
                    q.put_nowait(data)
        finally:
            term.alive = False

    def write(self, terminal_id: str, data: bytes) -> bool:
        term = self._terminals.get(terminal_id)
        if term is None or not term.alive:
            return False
        try:
            os.write(term.master_fd, data)
        except OSError:
            return False
        return True

    def resize(self, terminal_id: str, cols: int, rows: int) -> bool:
        term = self._terminals.get(terminal_id)
        if term is None or not term.alive:
            return False
        packed = struct.pack("HHHH", rows, cols, 0, 0)
        try:
            fcntl.ioctl(term.master_fd, termios.TIOCSWINSZ, packed)
        except OSError:
            return False
        return True

    def subscribe(self, terminal_id: str) -> asyncio.Queue[bytes] | None:
        term = self._terminals.get(terminal_id)
        if term is None:
            return None
        q: asyncio.Queue[bytes] = asyncio.Queue()
        term.subscribers.add(q)
        return q

    def unsubscribe(self, terminal_id: str, q: asyncio.Queue[bytes]) -> None:
        term = self._terminals.get(terminal_id)
        if term is not None:
            term.subscribers.discard(q)

    def kill(self, terminal_id: str) -> bool:
        term = self._terminals.pop(terminal_id, None)
        if term is None:
            return False
        if term.reader_task is not None:
            term.reader_task.cancel()
        try:
            os.killpg(os.getpgid(term.pid), signal.SIGHUP)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            os.close(term.master_fd)
        except OSError:
            pass
        return True

    def kill_all(self) -> None:
        for terminal_id in list(self._terminals):
            self.kill(terminal_id)


router = APIRouter(tags=["terminals"])


def _manager(request: Request) -> TerminalManager:
    mgr: TerminalManager = request.app.state.terminals
    return mgr


@router.get("/api/terminals", dependencies=[Depends(auth.require_token)])
async def list_terminals(request: Request) -> list[TerminalOut]:
    return [t.to_out() for t in _manager(request).list()]


@router.post("/api/terminals", dependencies=[Depends(auth.require_token)])
async def create_terminal(body: TerminalCreateIn, request: Request) -> TerminalOut:
    try:
        term = _manager(request).create(body.task_id, body.cwd)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    await _publish_overview(request)
    return term.to_out()


@router.delete("/api/terminals/{terminal_id}", dependencies=[Depends(auth.require_token)])
async def delete_terminal(terminal_id: str, request: Request) -> dict[str, bool]:
    ok = _manager(request).kill(terminal_id)
    if not ok:
        raise HTTPException(status_code=404, detail="no such terminal")
    await _publish_overview(request)
    return {"deleted": True}


async def _publish_overview(request: Request) -> None:
    from .manager import TaskManager
    tasks_mgr: TaskManager = request.app.state.tasks_manager
    terms = [t.to_out().model_dump() for t in _manager(request).list()]
    tasks_mgr.publish_overview_terminals(terms)


@router.websocket("/ws/terminals/{terminal_id}")
async def terminal_ws(websocket: WebSocket, terminal_id: str) -> None:
    if not auth.check_ws_token(websocket):
        await websocket.close(code=1008)
        return
    mgr: TerminalManager = websocket.app.state.terminals
    term = mgr.get(terminal_id)
    if term is None:
        await websocket.close(code=1008, reason="no such terminal")
        return

    await websocket.accept()
    queue = mgr.subscribe(terminal_id)
    assert queue is not None

    async def pump_out() -> None:
        while True:
            data = await queue.get()
            await websocket.send_bytes(data)

    sender = asyncio.create_task(pump_out())
    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            data = message.get("bytes")
            if data is not None:
                mgr.write(terminal_id, data)
                continue
            text = message.get("text")
            if text is None:
                continue
            resized = _try_resize(text)
            if resized is not None:
                mgr.resize(terminal_id, *resized)
            else:
                mgr.write(terminal_id, text.encode())
    except WebSocketDisconnect:
        pass
    finally:
        sender.cancel()
        mgr.unsubscribe(terminal_id, queue)


def _try_resize(text: str) -> tuple[int, int] | None:
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return None
    resize = obj.get("resize") if isinstance(obj, dict) else None
    if not (isinstance(resize, list) and len(resize) == 2):
        return None
    try:
        cols, rows = int(resize[0]), int(resize[1])
    except (TypeError, ValueError):
        return None
    return cols, rows
