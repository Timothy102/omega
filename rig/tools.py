import asyncio, fnmatch, inspect, os, shutil as _shutil, signal, subprocess, time
from collections import defaultdict

from . import permissions
from pathlib import Path

MAX_OUTPUT = 30000
REGISTRY: dict = {}
_locks: dict = defaultdict(asyncio.Lock)

# Set by the CLI. None means no prompting (scripted / --yolo).
CONFIRM = None
_confirm_lock = asyncio.Lock()
TAINTED = False


def set_tainted(value: bool):
    global TAINTED
    TAINTED = value


def truncate(text: str, limit: int = MAX_OUTPUT) -> str:
    """Head+tail. Head-only loses the end of failing output, which is where the
    error almost always is."""
    if len(text) <= limit:
        return text
    head, tail = int(limit * 0.6), int(limit * 0.4)
    dropped = len(text) - head - tail
    return (f"{text[:head]}\n"
            f"... [truncated {dropped} chars from the middle] ...\n{text[-tail:]}")


def tool(name, description, params, required, locks_path=None, mutates=False,
         deferred=False):
    def wrap(fn):
        REGISTRY[name] = {
            "fn": fn,
            "locks_path": locks_path,
            "mutates": mutates,
            "deferred": deferred,
            "schema": {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": {"type": "object", "properties": params,
                                   "required": required},
                },
            },
        }
        return fn
    return wrap


READ_ONLY = {"read", "grep", "glob", "recall", "subagent", "find_tools", "call_tool"}


def schemas(names=None) -> list:
    """Deferred tools stay out of the prefix: they are reachable via
    find_tools/call_tool instead of costing tokens on every request."""
    if names is None:
        return [t["schema"] for n, t in REGISTRY.items() if not t.get("deferred")]
    return [t["schema"] for n, t in REGISTRY.items()
            if n in names and not t.get("deferred")]


def deferred() -> dict:
    return {n: t for n, t in REGISTRY.items() if t.get("deferred")}


S = {"type": "string"}
I = {"type": "integer"}


@tool("read", "Read a file. Returns numbered lines.",
      {"path": S, "offset": I, "limit": I}, ["path"])
def _read(path, offset=0, limit=2000):
    p = Path(path).expanduser()
    try:
        p.resolve().relative_to(Path(os.getcwd()).resolve())
    except ValueError:
        set_tainted(True)
    lines = p.read_text(errors="replace").splitlines()
    chosen = lines[offset:offset + limit]
    width = len(str(offset + len(chosen)))
    return truncate("\n".join(f"{i + offset + 1:>{width}}\t{l}"
                              for i, l in enumerate(chosen)))


@tool("write", "Write a file, creating parent directories.",
      {"path": S, "content": S}, ["path", "content"], locks_path="path", mutates=True)
def _write(path, content):
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return f"wrote {len(content)} chars to {p}"


@tool("edit", "Replace an exact unique string in a file.",
      {"path": S, "old": S, "new": S}, ["path", "old", "new"], locks_path="path", mutates=True)
def _edit(path, old, new):
    p = Path(path).expanduser()
    text = p.read_text()
    n = text.count(old)
    if n == 0:
        raise ValueError("string not found")
    if n > 1:
        raise ValueError(f"string appears {n} times; make it unique")
    p.write_text(text.replace(old, new))
    return f"edited {p}"


@tool("bash", "Run a shell command. Returns combined stdout+stderr.",
      {"command": S, "timeout": I}, ["command"], mutates=True)
def _bash(command, timeout=120):
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

    parts = []
    if (stdout or "").strip():
        parts.append(truncate(stdout.strip(), int(MAX_OUTPUT * 0.75)))
    if (stderr or "").strip():
        # own budget: stderr is small and carries the failure reason
        parts.append("[stderr]\n" + truncate(stderr.strip(), int(MAX_OUTPUT * 0.25)))
    if code != 0:
        parts.append(f"[exit {code}]")
    return "\n".join(parts) or "(no output, exit 0)"


def _kill_group(pgid):
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


@tool("grep", "Search file contents by regex. Returns path:line:text.",
      {"pattern": S, "path": S, "glob": S}, ["pattern"])
def _grep(pattern, path=".", glob="*"):
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
def _glob(pattern, path="."):
    root = Path(path).expanduser()
    # pathlib.glob understands "**" as spanning directories; fnmatch does not.
    matches = root.glob(pattern) if "/" in pattern else root.rglob(pattern)
    skip = {".git", "node_modules", "__pycache__", ".venv"}
    hits = [f for f in matches if f.is_file() and not skip & set(f.parts)]
    hits.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return truncate("\n".join(str(p) for p in hits[:200]) or "(no matches)")


async def run(call, allowed=None) -> str:
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
                        f"rig is running non-interactively. Re-run without --yolo "
                        f"in a terminal, or narrow the command.")
            # Serialized: parallel dispatch would interleave prompts.
            async with _confirm_lock:
                ok = await CONFIRM(call.name, args, why)
            if not ok:
                return "error: denied by user"
        fn = entry["fn"]
        call_it = ((lambda: fn(**args)) if inspect.iscoroutinefunction(fn)
                   else (lambda: asyncio.to_thread(fn, **args)))
        lock_key = args.get(entry["locks_path"]) if entry["locks_path"] else None
        if lock_key:
            async with _locks[str(Path(lock_key).expanduser().resolve())]:
                return await call_it()
        return await call_it()
    except Exception as e:
        return f"error: {type(e).__name__}: {e}"


@tool("remember", "Save a durable fact about the user or their projects to persistent memory.",
      {"title": S, "body": S}, ["title", "body"], mutates=True)
def _remember(title, body):
    from . import memory
    return memory.save(title, body)


@tool("recall", "Search persistent memory by regex.", {"query": S}, ["query"])
def _recall(query):
    from . import memory
    return truncate(memory.recall(query))


@tool("find_tools",
      "Search the deferred tool catalog (Linear, Notion, and other connected "
      "MCP servers) by keyword. Returns tool names, descriptions and parameters. "
      "Use this BEFORE call_tool when you need an integration, then pass the "
      "exact name to call_tool.",
      {"query": S, "limit": I}, ["query"])
def _find_tools(query, limit=8):
    terms = [w for w in query.lower().split() if len(w) > 2]
    scored = []
    for name, entry in deferred().items():
        fn = entry["schema"]["function"]
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
async def _call_tool(name, arguments=None):
    entry = REGISTRY.get(name)
    if entry is None or not entry.get("deferred"):
        return (f"error: {name!r} is not a deferred tool. "
                f"Use find_tools first to get an exact name.")
    fn = entry["fn"]
    args = arguments or {}
    if inspect.iscoroutinefunction(fn):
        return await fn(**args)
    return await asyncio.to_thread(fn, **args)
