"""Scripted worker stub for server tests: speaks the same NDJSON stdio
protocol as omega.server.worker (see its docstring) but with canned behavior
driven by the prompt text, so tests never need a real LLM. Not part of the
omega package -- spawned directly as a script by test fixtures that override
TaskManager's worker_argv.
"""
import asyncio
import json
import sys
import time
from typing import Any


def emit(obj: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


async def stdin_reader() -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    loop = asyncio.get_event_loop()
    await loop.connect_read_pipe(lambda: protocol, sys.stdin)
    return reader


class Stub:
    def __init__(self) -> None:
        self.turn = 0
        self.pending: dict[str, asyncio.Future[Any]] = {}
        self.turn_task: asyncio.Task[None] | None = None

    async def run_turn(self, text: str) -> None:
        self.turn += 1
        turn = self.turn

        def e(t: str, **fields: Any) -> None:
            emit({"type": t, **fields, "t": time.time(), "turn": turn})

        try:
            e("Phase", state="waiting")
            e("ModelUsed", alias="stub", model="stub-1", provider="stub")
            if text == "ASK":
                fut: asyncio.Future[str] = asyncio.get_event_loop().create_future()
                self.pending["req1"] = fut
                emit({"type": "ask_user_request", "request_id": "req1",
                     "question": "pick one",
                     "options": [{"label": "a"}, {"label": "b"}], "multi_select": False})
                answer = await fut
                e("Done", text=f"answered: {answer}")
            elif text == "CONFIRM":
                fut_c: asyncio.Future[bool] = asyncio.get_event_loop().create_future()
                self.pending["req2"] = fut_c
                emit({"type": "confirm_request", "request_id": "req2", "tool": "bash",
                     "args_preview": "rm -rf /", "why": "dangerous"})
                allow = await fut_c
                e("Done", text="allowed" if allow else "denied")
            elif text == "SLEEP":
                for _ in range(200):
                    e("Phase", state="tools")
                    await asyncio.sleep(0.05)
                e("Done", text="finished without cancel")
            else:
                e("ToolStart", call_id="c1", name="read", args_preview="a.txt")
                e("ToolEnd", call_id="c1", name="read", result_preview="ok",
                  duration_s=0.01, offloaded=False)
                e("Usage", prompt_tokens=10, completion_tokens=5, used=15, limit=1000)
                e("Done", text=f"echo: {text}")
            e("Phase", state="idle")
        except asyncio.CancelledError:
            e("Error", message="cancelled")
        finally:
            self.turn_task = None

    def resolve(self, request_id: object, value: Any) -> None:
        fut = self.pending.pop(request_id, None) if isinstance(request_id, str) else None
        if fut is not None and not fut.done():
            fut.set_result(value)

    async def dispatch(self, cmd: dict[str, Any]) -> bool:
        kind = cmd.get("cmd")
        if kind == "prompt":
            self.turn_task = asyncio.create_task(self.run_turn(cmd.get("text", "")))
        elif kind == "answer":
            self.resolve(cmd.get("request_id"), cmd.get("answer", ""))
        elif kind == "confirm":
            self.resolve(cmd.get("request_id"), bool(cmd.get("allow", False)))
        elif kind == "cancel":
            if self.turn_task is not None:
                self.turn_task.cancel()
        elif kind == "jobs":
            emit({"type": "jobs", "request_id": cmd.get("request_id"),
                 "jobs": [{"id": "j1", "command": "sleep 1", "finished": False,
                          "exit_code": None, "pid": 999}]})
        elif kind == "shutdown":
            if self.turn_task is not None:
                self.turn_task.cancel()
                await asyncio.gather(self.turn_task, return_exceptions=True)
            return False
        return True


async def main() -> None:
    stub = Stub()
    reader = await stdin_reader()
    emit({"type": "worker_ready"})
    while True:
        line = await reader.readline()
        if not line:
            break
        try:
            cmd = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not await stub.dispatch(cmd):
            break


if __name__ == "__main__":
    asyncio.run(main())
