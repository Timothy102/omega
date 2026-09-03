import asyncio
import inspect
import os
import re
import secrets
import shutil as _shutil
import signal
import subprocess
import threading
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, cast

from . import events, hooks, mcp, permissions
from .config import HookRule
from .events import Option
from .llm import ToolCall

ToolArgs = dict[str, Any]
ToolFn = Callable[..., Any]


@dataclass(frozen=True)
class ToolEntry:
    fn: ToolFn
    locks_path: str | None
    mutates: bool
    deferred: bool
    schema: dict[str, Any]


MAX_OUTPUT = 30000
REGISTRY: dict[str, ToolEntry] = {}
_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
_OFFLOAD_RE = re.compile(r"saved as artifact ([0-9a-f]{8})")

# Hard cap on any string that re-enters the model's context through tools.run
# (a plain result, an offload preview, or a fetch_result page) -- independent
# of whether it was offloaded, so a huge `limit` on fetch_result can't defeat
# it either.
MAX_INLINE_CHARS = 24_000

# Total chars written to artifacts this turn (a "turn" = one run_turn call,
# spanning every round and every subagent dispatched within it). Once this is
# exceeded, further large results skip offload entirely -- an artifact this
# session can no longer read back in full is worse than a longer inline
# truncation.
TURN_RESULT_BUDGET_CHARS = 2_000_000
_turn_chars_stored = 0

# Set by the CLI. None means no prompting (scripted / --yolo).
CONFIRM: Callable[[str, ToolArgs, str], Awaitable[bool]] | None = None
_confirm_lock = asyncio.Lock()
TAINTED = False

# Set by the CLI right after the session is created/loaded. None means no
# session context (e.g. subagent-only test contexts) -- offload is skipped.
SESSION_ID: str | None = None

# Set by the CLI. None means no interactive session (scripted / --yolo).
ASK_USER: Callable[[str, list[Option], bool], Awaitable[str]] | None = None

# Set by run_turn from cfg.hooks. Empty dict means no hooks configured.
HOOK_RULES: dict[str, list[HookRule]] = {}

# Set by run_turn, mirroring subagent.EMIT -- lets background-job lifecycle
# events reach the UI without threading an emit callback through every tool.
EMIT: Callable[[events.Event], None] | None = None

# ~/.omega/sessions -- a session's background-job logs live under
# <JOBS_DIR>/<session id>/jobs/<job id>.log. A separate module attribute (not
# shared with session.DIR/artifacts.DIR) so tests can monkeypatch it in isolation.
JOBS_DIR = Path.home() / ".omega" / "sessions"


@dataclass
class Job:
    id: str
    command: str
    proc: subprocess.Popen[bytes]
    log_path: Path
    started: float
    finished: bool = False
    exit_code: int | None = None


_JOBS: dict[str, Job] = {}
_JOBS_LOCK = threading.Lock()


def set_tainted(value: bool) -> None:
    global TAINTED
    TAINTED = value


def reset_turn_budget() -> None:
    """Called once at the start of every `run_turn` -- the budget is per
    user-visible turn, shared across every round and subagent within it."""
    global _turn_chars_stored
    _turn_chars_stored = 0


def _cap_inline(text: str) -> str:
    if len(text) <= MAX_INLINE_CHARS:
        return text
    marker = (f"\n[inline result capped at {MAX_INLINE_CHARS} chars; narrow "
              f"the query or page with offset/limit for more]")
    return text[:MAX_INLINE_CHARS - len(marker)] + marker


def offload_info(result: str) -> tuple[bool, str | None]:
    m = _OFFLOAD_RE.search(result)
    return (bool(m), m.group(1) if m else None)


def is_error_result(text: str) -> bool:
    """Shared with loop.py's repeat-fail guard: a result counts as an error
    either because run() itself prefixed it with "error: " (a raised
    exception, a permissions denial, an MCP isError result -- see mcp.py's
    call()), or because a remote tool's own untrusted payload carries an
    "error" field even without that prefix."""
    if text.startswith("error:"):
        return True
    return "<untrusted" in text and '"error"' in text


def truncate(text: str, limit: int = MAX_OUTPUT) -> str:
    """Head+tail. Head-only loses the end of failing output, which is where the
    error almost always is."""
    if len(text) <= limit:
        return text
    head, tail = int(limit * 0.6), int(limit * 0.4)
    dropped = len(text) - head - tail
    return (f"{text[:head]}\n"
            f"... [truncated {dropped} chars from the middle] ...\n{text[-tail:]}")


def tool(name: str, description: str, params: dict[str, Any], required: list[str],
        locks_path: str | None = None, mutates: bool = False,
        deferred: bool = False) -> Callable[[ToolFn], ToolFn]:
    def wrap(fn: ToolFn) -> ToolFn:
        REGISTRY[name] = ToolEntry(
            fn=fn, locks_path=locks_path, mutates=mutates, deferred=deferred,
            schema={
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": {"type": "object", "properties": params,
                                   "required": required},
                },
            },
        )
        return fn
    return wrap


READ_ONLY = {"read", "grep", "glob", "recall", "subagent", "find_tools", "call_tool",
             "fetch_result", "list_artifacts", "ask_user", "skill", "bash_status"}

# Tiny confirmations, or themselves the retrieval path -- offloading
# fetch_result's own output would be an infinite regress.
_NO_OFFLOAD = {"write", "edit", "remember", "supersede", "link", "ask_user",
               "save_artifact", "update_artifact", "fetch_result", "list_artifacts"}


def schemas(names: set[str] | None = None) -> list[dict[str, Any]]:
    """Deferred tools stay out of the prefix: they are reachable via
    find_tools/call_tool instead of costing tokens on every request."""
    if names is None:
        return [t.schema for t in REGISTRY.values() if not t.deferred]
    return [t.schema for n, t in REGISTRY.items() if n in names and not t.deferred]


def deferred() -> dict[str, ToolEntry]:
    return {n: t for n, t in REGISTRY.items() if t.deferred}


S = {"type": "string"}
I = {"type": "integer"}


@tool("read", "Read a file. Returns numbered lines.",
      {"path": S, "offset": I, "limit": I}, ["path"])
def _read(path: str, offset: int = 0, limit: int = 2000) -> str:
    # Files on this machine are the user's own; only remote (MCP) content
    # taints the turn. Tainting on any read outside cwd meant every worktree
    # edit session prompted for bash on every turn.
    p = Path(path).expanduser()
    lines = p.read_text(errors="replace").splitlines()
    chosen = lines[offset:offset + limit]
    width = len(str(offset + len(chosen)))
    return truncate("\n".join(f"{i + offset + 1:>{width}}\t{l}"
                              for i, l in enumerate(chosen)))


@tool("write", "Write a file, creating parent directories.",
      {"path": S, "content": S}, ["path", "content"], locks_path="path", mutates=True)
def _write(path: str, content: str) -> str:
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return f"wrote {len(content)} chars to {p}"


@tool("edit", "Replace an exact unique string in a file.",
      {"path": S, "old": S, "new": S}, ["path", "old", "new"], locks_path="path", mutates=True)
def _edit(path: str, old: str, new: str) -> str:
    p = Path(path).expanduser()
    text = p.read_text()
    n = text.count(old)
    if n == 0:
        raise ValueError("string not found")
    if n > 1:
        raise ValueError(f"string appears {n} times; make it unique")
    p.write_text(text.replace(old, new))
    return f"edited {p}"


@tool("bash", "Run a shell command. Returns combined stdout+stderr. Pass "
      "background=true for a long-running command (a server, a watch loop) -- it "
      "starts the process and returns immediately with a job id; poll it with "
      "bash_status(id).",
      {"command": S, "timeout": I, "background": {"type": "boolean"}}, ["command"], mutates=True)
def _bash(command: str, timeout: int | None = 120, background: bool = False) -> str:
    if background:
        return _bash_background(command)
    # start_new_session puts the child in its own process group, so a timeout
    # can kill the whole tree; subprocess.run would only kill the shell and
    # leave its children running forever.
    # `timeout or 120` would treat an explicit 0 as "not supplied".
    timeout = max(1, min(int(120 if timeout is None else timeout), 600))
    p = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True, cwd=os.getcwd(),
                         start_new_session=True)
    try:
        stdout, stderr = p.communicate(timeout=timeout)
        code = p.returncode
    except subprocess.TimeoutExpired:
        # start_new_session makes the child its own group leader, so pgid == pid.
        # Look it up via getpgid instead and it fails once the shell has exited,
        # which is exactly when backgrounded children are still alive.
        _kill_group(p.pid)
        for cleanup in (p.kill, p.wait):
            try:
                cleanup()
            except Exception:
                pass
        return truncate(f"(timed out after {timeout}s; process group killed)")

    parts: list[str] = []
    if (stdout or "").strip():
        parts.append(truncate(stdout.strip(), int(MAX_OUTPUT * 0.75)))
    if (stderr or "").strip():
        # own budget: stderr is small and carries the failure reason
        parts.append("[stderr]\n" + truncate(stderr.strip(), int(MAX_OUTPUT * 0.25)))
    if code != 0:
        parts.append(f"[exit {code}]")
    return "\n".join(parts) or "(no output, exit 0)"


def _kill_group(pgid: int) -> None:
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
        except (ProcessLookupError, PermissionError):
            return
        for _ in range(6):
            try:
                os.killpg(pgid, 0)
            except (ProcessLookupError, PermissionError):
                return
            time.sleep(0.25)


def _jobs_dir() -> Path:
    d = JOBS_DIR / (SESSION_ID or "no-session") / "jobs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _bash_background(command: str) -> str:
    job_id = secrets.token_hex(3)
    log_path = _jobs_dir() / f"{job_id}.log"
    log_file = log_path.open("wb")
    try:
        proc = subprocess.Popen(command, shell=True, stdout=log_file, stderr=subprocess.STDOUT,
                                cwd=os.getcwd(), start_new_session=True)
    except OSError as e:
        log_file.close()
        return f"error: could not start background job: {e}"
    job = Job(id=job_id, command=command, proc=proc, log_path=log_path, started=time.time())
    with _JOBS_LOCK:
        _JOBS[job_id] = job
    if EMIT:
        EMIT(events.JobStarted(id=job_id, command=command))
    threading.Thread(target=_watch_job, args=(job, log_file), daemon=True).start()
    return f"started background job {job_id} (pid {proc.pid}) -- check with bash_status({job_id!r})"


def _watch_job(job: Job, log_file: BinaryIO) -> None:
    code = job.proc.wait()
    try:
        log_file.close()
    except OSError:
        pass
    with _JOBS_LOCK:
        job.finished = True
        job.exit_code = code
    if EMIT:
        EMIT(events.JobFinished(id=job.id, exit_code=code))


def _tail_log(path: Path, lines: int = 40) -> str:
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return "(no output yet)"
    return "\n".join(text.splitlines()[-lines:]) or "(no output yet)"


def list_jobs() -> list[dict[str, Any]]:
    """Background jobs started this process, running or finished -- used to
    tell the user what's still going at session end (they are left running,
    not killed: they're the user's own processes)."""
    with _JOBS_LOCK:
        return [{"id": j.id, "command": j.command, "finished": j.finished,
                 "exit_code": j.exit_code, "pid": j.proc.pid} for j in _JOBS.values()]


@tool("bash_status", "Check a background job started with bash(..., background=True): "
      "running/finished state, exit code, and a tail of its output.",
      {"id": S}, ["id"])
def _bash_status(id: str) -> str:
    with _JOBS_LOCK:
        job = _JOBS.get(id)
    if job is None:
        return f"error: no background job {id!r}"
    tail = _tail_log(job.log_path)
    if job.finished:
        return f"job {id}: finished, exit {job.exit_code}\n{tail}"
    return f"job {id}: running (pid {job.proc.pid})\n{tail}"


@tool("grep", "Search file contents by regex. Returns path:line:text.",
      {"pattern": S, "path": S, "glob": S}, ["pattern"])
def _grep(pattern: str, path: str = ".", glob: str = "*") -> str:
    if not _shutil.which("rg"):
        raise RuntimeError("ripgrep (rg) not installed; use bash with grep instead")
    cmd = ["rg", "--line-number", "--no-heading", "--color=never",
           "--glob", glob, "-e", pattern, path]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if r.returncode >= 2:
        return f"error: ripgrep failed: {(r.stderr or '').strip()[:300]}"
    return truncate(r.stdout.strip() or "(no matches)", 12000)


@tool("glob", "Find files by name pattern, newest first.",
      {"pattern": S, "path": S}, ["pattern"])
def _glob(pattern: str, path: str = ".") -> str:
    root = Path(path).expanduser()
    # pathlib.glob understands "**" as spanning directories; fnmatch does not.
    matches = root.glob(pattern) if "/" in pattern else root.rglob(pattern)
    skip = {".git", "node_modules", "__pycache__", ".venv"}
    hits = [f for f in matches if f.is_file() and not skip & set(f.parts)]
    hits.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return truncate("\n".join(str(p) for p in hits[:200]) or "(no matches)")


async def run(call: ToolCall, allowed: set[str] | None = None) -> str:
    """`allowed` is enforced here, not merely omitted from the schema list.
    Filtering schemas only hides a tool; a model can still emit the call."""
    if allowed is not None and call.name not in allowed:
        return (f"error: tool {call.name!r} is not permitted in this mode "
                f"(allowed: {', '.join(sorted(allowed))})")
    entry = REGISTRY.get(call.name)
    if entry is None:
        return f"error: unknown tool {call.name!r}"
    try:
        args = call.args()

        verdict, why = permissions.decide(call.name, args, tainted=TAINTED)
        if verdict == permissions.DENY:
            return (f"error: refused -- {why}. This is a hard limit; do not "
                    f"retry it or work around it. Tell the user instead.")
        if verdict == permissions.ASK:
            if CONFIRM is None:
                return (f"error: {call.name} requires confirmation ({why}) but "
                        f"omega is running non-interactively. Re-run without --yolo "
                        f"in a terminal, or narrow the command.")
            # Serialized: parallel dispatch would interleave prompts. Every
            # waiter re-decides once it holds the lock -- a sibling call that
            # was ASK when this one was dispatched may have just answered
            # "always" and stored an ALLOW rule (permissions.remember), in
            # which case prompting again for the identical rule is pure noise.
            async with _confirm_lock:
                verdict, why = permissions.decide(call.name, args, tainted=TAINTED)
                if verdict == permissions.DENY:
                    return (f"error: refused -- {why}. This is a hard limit; do not "
                            f"retry it or work around it. Tell the user instead.")
                ok = True if verdict == permissions.ALLOW else await CONFIRM(call.name, args, why)
            if not ok:
                return "error: denied by user"

        cwd = os.getcwd()
        pre_rules = HOOK_RULES.get("pre_tool", [])
        if pre_rules:
            blocked, why = await asyncio.to_thread(hooks.run_pre, pre_rules, call.name, args, cwd)
            if blocked:
                return f"error: blocked by hook: {why}"

        fn = entry.fn
        call_it = ((lambda: fn(**args)) if inspect.iscoroutinefunction(fn)
                   else (lambda: asyncio.to_thread(fn, **args)))
        lock_key = args.get(entry.locks_path) if entry.locks_path else None
        if lock_key:
            async with _locks[str(Path(lock_key).expanduser().resolve())]:
                result = await call_it()
        else:
            result = await call_it()
    except Exception as e:
        return f"error: {type(e).__name__}: {e}"

    result = cast(str, result)
    post_rules = HOOK_RULES.get("post_tool", [])
    if post_rules:
        appended = await asyncio.to_thread(hooks.run_post, post_rules, call.name, args, cwd, result)
        if appended:
            result = f"{result}\n{appended}"

    if call.name in _NO_OFFLOAD or SESSION_ID is None:
        return _cap_inline(result)

    from . import artifacts
    global _turn_chars_stored
    if len(result) > artifacts.OFFLOAD_THRESHOLD:
        if _turn_chars_stored >= TURN_RESULT_BUDGET_CHARS:
            return _cap_inline(
                f"{truncate(result)}\n"
                f"[turn result budget of {TURN_RESULT_BUDGET_CHARS} chars "
                f"exceeded; not offloaded to an artifact this turn]")
        _turn_chars_stored += min(len(result), artifacts.RESULT_MAX_CHARS)
    return _cap_inline(artifacts.offload_if_large(result, SESSION_ID))


@tool("find_tools",
      "Search the deferred tool catalog (Linear, Notion, and other connected "
      "MCP servers) by keyword. Returns tool names, descriptions and parameters. "
      "Use this BEFORE call_tool when you need an integration, then pass the "
      "exact name to call_tool.",
      {"query": S, "limit": I}, ["query"])
async def _find_tools(query: str, limit: int = 8) -> str:
    await mcp.ensure_loaded()
    terms = [w for w in query.lower().split() if len(w) > 2]
    scored: list[tuple[int, str, dict[str, Any]]] = []
    for name, entry in deferred().items():
        fn = entry.schema["function"]
        hay = (name + " " + (fn.get("description") or "")).lower()
        score = sum(hay.count(w) for w in terms)
        if score:
            scored.append((score, name, fn))
    if not scored:
        return (f"no tools matched {query!r}. "
                f"{len(deferred())} tools available across "
                f"{len({n.split('__')[1] for n in deferred()})} servers.")
    scored.sort(key=lambda x: -x[0])
    out = []
    for _, name, fn in scored[:limit]:
        params = list((fn.get("parameters") or {}).get("properties") or {})
        req = (fn.get("parameters") or {}).get("required") or []
        out.append(f"{name}\n  {(fn.get('description') or '')[:280]}\n"
                   f"  params: {', '.join(params[:14]) or '(none)'}"
                   f"{'  required: ' + ', '.join(req) if req else ''}")
    return truncate("\n\n".join(out), 8000)


@tool("call_tool",
      "Invoke a tool found via find_tools. `name` must be an exact name from "
      "find_tools output; `arguments` is a JSON object of its parameters.",
      {"name": S, "arguments": {"type": "object"}}, ["name"], mutates=True)
async def _call_tool(name: str, arguments: ToolArgs | None = None) -> str:
    await mcp.ensure_loaded()
    entry = REGISTRY.get(name)
    if entry is None or not entry.deferred:
        return (f"error: {name!r} is not a deferred tool. "
                f"Use find_tools first to get an exact name.")
    fn = entry.fn
    args = arguments or {}
    if inspect.iscoroutinefunction(fn):
        return cast(str, await fn(**args))
    return cast(str, await asyncio.to_thread(fn, **args))


@tool("ask_user",
      "The right tool for a design conversation -- batching 2-4 decisions "
      "with options is expected before building anything non-trivial on an "
      "open-ended request. Never use it for things you can look up yourself.",
      {"question": S, "header": S,
       "options": {"type": "array", "items": {"type": "object",
                    "properties": {"label": S, "description": S}}},
       "multi_select": {"type": "boolean"}},
      ["question"])
async def _ask_user(question: str, header: str = "", options: list[Option] | None = None,
                    multi_select: bool = False) -> str:
    if ASK_USER is None:
        return ("error: ask_user requires an interactive session; omega is "
                "running non-interactively. State your assumption and "
                "proceed instead.")
    async with _confirm_lock:
        return await ASK_USER(question, options or [], multi_select)


@tool("fetch_result",
      "Read more of a large tool result that was saved as an artifact -- "
      "the id is in the `[full output: ... saved as artifact <id>]` footer. "
      "Each page ends with a trailer showing `next_offset` for the next call, "
      "or `[end]`.",
      {"id": S, "offset": I, "limit": I}, ["id"])
def _fetch_result(id: str, offset: int = 0, limit: int = 0) -> str:
    if SESSION_ID is None:
        return "error: no active session"
    from . import artifacts
    # 0 means "not specified" -- the actual default (PAGE_CHARS) lives in
    # artifacts.py, which tools.py cannot import at module scope (artifacts
    # imports `truncate` from here, so a top-level cycle would deadlock init).
    return artifacts.fetch(SESSION_ID, id, offset, limit or artifacts.PAGE_CHARS)


@tool("list_artifacts",
      "List the artifacts saved so far this session (offloaded tool "
      "outputs and content saved with save_artifact) -- id, kind, size, title.",
      {}, [])
def _list_artifacts() -> str:
    if SESSION_ID is None:
        return "(no artifacts this session)"
    from . import artifacts
    rows = artifacts.list_artifacts(SESSION_ID)
    if not rows:
        return "(no artifacts this session)"
    return "\n".join(f"{r['id']}  {r['kind']}  {r['size']} chars  {r['title']}"
                     for r in rows)


@tool("save_artifact",
      "Persist long-form content you are building up (a plan, a report) so "
      "you don't have to re-emit it every turn; returns an id you can update "
      "later.",
      {"title": S, "content": S}, ["title", "content"], mutates=True)
def _save_artifact(title: str, content: str) -> str:
    if SESSION_ID is None:
        return "error: no active session"
    from . import artifacts
    artifact_id = artifacts.save(SESSION_ID, content, title=title, kind="authored")
    return f"saved artifact {artifact_id} ({len(content)} chars)"


@tool("update_artifact",
      "Replace the full content of an artifact you previously created with "
      "save_artifact (full replace, no diffing).",
      {"id": S, "content": S}, ["id", "content"], mutates=True)
def _update_artifact(id: str, content: str) -> str:
    if SESSION_ID is None:
        return "error: no active session"
    from . import artifacts
    return artifacts.update(SESSION_ID, id, content)
