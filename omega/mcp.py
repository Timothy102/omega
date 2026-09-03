import asyncio
import json
import os
import re
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import TextContent
from mcp.types import Tool as McpTool

from . import config, tools

SERVERS: dict[str, "Server"] = {}
LAST_USED: dict[str, float] = {}
# Names this process has registered into tools.REGISTRY, per server -- lets
# disconnect()/remove() clean those entries back out.
_REGISTERED: dict[str, list[str]] = {}

# Pinned: an unpinned `npx -y mcp-remote` executes whatever npm serves as
# latest, on every single run.
MCP_REMOTE_VERSION = "0.8.1"
CALL_TIMEOUT = 120

# How long an explicit `connect()` (a user sitting at a browser, doing OAuth)
# waits before giving up; lazy background loading uses a much shorter budget
# since nobody is there to click "authorize".
AUTH_TIMEOUT = 90.0
LAZY_TIMEOUT = 20.0
POLL_INTERVAL = 1.0
AUTH_URL_RE = re.compile(r"https?://\S+")

# MCP tools whose names indicate a read; these become available to plan mode
# and subagents instead of every MCP tool being treated as mutating.
READ_PREFIXES = ("get_", "list_", "search", "read_", "fetch", "query", "resolve")

DESC_LIMIT = 2000        # descriptions drive tool SELECTION -- keep them
PARAMS_LIMIT = 1500      # inputSchema is 78% of the bytes and mostly noise
ENUM_LIMIT = 12
NOISE_KEYS = {"examples", "example", "default", "$comment", "$schema", "$id",
              "additionalProperties", "readOnly", "writeOnly", "deprecated"}


def prune_schema(node: Any, depth: int = 0) -> Any:
    """Strip JSON Schema down to what a model needs to CALL a tool.

    Measured on linear+notion+posthog: descriptions were 59.5k chars, parameters
    143k. omega previously capped descriptions and passed parameters untouched --
    exactly backwards, since descriptions are what tool selection depends on.
    """
    if isinstance(node, list):
        return [prune_schema(x, depth + 1) for x in node[:ENUM_LIMIT]]
    if not isinstance(node, dict):
        return node

    out: dict[str, Any] = {}
    for k, v in node.items():
        if k in NOISE_KEYS:
            continue
        if k == "enum" and isinstance(v, list):
            out[k] = v[:ENUM_LIMIT] if len(v) <= ENUM_LIMIT else v[:ENUM_LIMIT]
            continue
        if k == "description" and isinstance(v, str):
            out[k] = v[:300]
            continue
        if k in ("anyOf", "oneOf", "allOf") and isinstance(v, list):
            # deep unions explode; the first branch is enough to call the tool
            if depth >= 2:
                continue
            out[k] = [prune_schema(x, depth + 1) for x in v[:3]]
            continue
        if depth >= 6:
            continue
        out[k] = prune_schema(v, depth + 1)
    return out


def fit_params(schema: dict[str, Any]) -> dict[str, Any]:
    """Prune, then drop optional property descriptions until it fits."""
    pruned: dict[str, Any] = prune_schema(schema or {"type": "object", "properties": {}})
    if len(json.dumps(pruned)) <= PARAMS_LIMIT:
        return pruned
    required = set(pruned.get("required") or [])
    for name, prop in (pruned.get("properties") or {}).items():
        if name not in required and isinstance(prop, dict):
            prop.pop("description", None)
    if len(json.dumps(pruned)) <= PARAMS_LIMIT:
        return pruned
    props = pruned.get("properties") or {}
    kept = {k: v for k, v in props.items() if k in required}
    for k, v in props.items():
        if k in kept:
            continue
        if len(json.dumps({**kept, k: v})) > PARAMS_LIMIT:
            break
        kept[k] = v
    pruned["properties"] = kept
    return pruned


def discover(paths: list[Path] | None = None, include_omega: bool = True) -> dict[str, dict[str, Any]]:
    """omega's own mcp block wins; Claude Code's config and installed plugins
    (which ship their own .mcp.json) are imported under it. `include_omega=False`
    returns Claude Code's servers only -- used to show what's importable
    without mixing in what omega already manages."""
    if paths is None:
        paths = [Path.home() / ".claude.json", Path.home() / ".claude" / "settings.json"]
        plugins = Path.home() / ".claude" / "plugins"
        if plugins.exists():
            for depth in range(1, 5):
                paths += sorted(plugins.glob("/".join(["*"] * depth) + "/.mcp.json"))
    found: dict[str, dict[str, Any]] = {}

    def walk(o: Any) -> None:
        if isinstance(o, dict):
            for k, v in o.items():
                if k == "mcpServers" and isinstance(v, dict):
                    found.update(v)
                else:
                    walk(v)
        elif isinstance(o, list):
            for x in o:
                walk(x)

    for p in paths:
        if p.exists():
            try:
                walk(json.loads(p.read_text()))
            except json.JSONDecodeError:
                continue

    if not include_omega:
        return found
    omega_cfg = Path(os.environ.get("OMEGA_CONFIG", Path.home() / ".omega" / "config.json"))
    if omega_cfg.exists():
        try:
            found.update(json.loads(omega_cfg.read_text()).get("mcp", {}))
        except json.JSONDecodeError:
            pass
    return found


@dataclass(frozen=True)
class ServerStatus:
    name: str
    enabled: bool
    state: Literal["connected", "configured", "needs_auth", "error", "disabled"]
    tools: int
    error: str | None
    last_used: float | None


def as_stdio(cfg: dict[str, Any]) -> dict[str, Any]:
    """Any remote MCP server can be reached by proxying it through mcp-remote,
    which owns the OAuth dance and caches tokens in ~/.mcp-auth."""
    if "command" in cfg:
        return cfg
    url = cfg.get("url") or cfg.get("serverUrl")
    if not url:
        return cfg
    args = ["-y", f"mcp-remote@{MCP_REMOTE_VERSION}", url]
    for k, v in (cfg.get("headers") or {}).items():
        args += ["--header", f"{k}:{v}"]
    return {"command": "npx", "args": args}


class Server:
    """Owns one MCP connection. anyio requires the context managers be entered
    and exited in the same task, so the whole lifecycle lives in _run."""

    def __init__(self, name: str, cfg: dict[str, Any]):
        self.name, self.cfg = name, cfg
        self.session: ClientSession | None = None
        self.tools: list[McpTool] = []
        self.error: str | None = None
        # Set once a poller (see connect()) spots an authorize-me URL in the
        # child's stderr -- distinct from `error`, since the task is still
        # alive and waiting, not dead.
        self.auth_url: str | None = None
        self.ready = asyncio.Event()
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._errlog_path: Path | None = None

    async def _run(self) -> None:
        # NamedTemporaryFile's wrapper type isn't a TextIO as far as mypy is
        # concerned; fdopen over its fd is, and gives the same file.
        fd, path = tempfile.mkstemp(prefix="omega-mcp-", suffix=".log")
        self._errlog_path = Path(path)
        errlog = os.fdopen(fd, "w")
        try:
            command = self.cfg["command"]
            if not shutil.which(command):
                raise FileNotFoundError(f"{command!r} not on PATH")
            params = StdioServerParameters(
                command=command, args=self.cfg.get("args", []),
                env={**os.environ, **self.cfg.get("env", {})})
            async with stdio_client(params, errlog=errlog) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    self.session = session
                    self.tools = (await session.list_tools()).tools
                    self.ready.set()
                    await self._stop.wait()
        except Exception as e:
            self.error = f"{type(e).__name__}: {e}"[:120]
        finally:
            self.ready.set()
            errlog.close()
            try:
                self._errlog_path.unlink(missing_ok=True)
            except OSError:
                pass

    def peek_auth_url(self) -> str | None:
        """Best-effort read of the child's stderr so far, looking for an
        "authorize this client" link. Opens the path fresh each time rather
        than reusing the write handle, whose fd is shared with the child
        process and must not have its offset disturbed."""
        if self._errlog_path is None or not self._errlog_path.exists():
            return None
        try:
            content = self._errlog_path.read_text(errors="replace")
        except OSError:
            return None
        m = AUTH_URL_RE.search(content)
        return m.group(0) if m else None

    async def start(self, timeout: float = 60) -> None:
        self._task = asyncio.create_task(self._run())
        await asyncio.wait_for(self.ready.wait(), timeout)
        if self.error:
            raise RuntimeError(self.error)

    async def stop(self) -> None:
        self._stop.set()
        if not self._task:
            return
        try:
            # No shield: shielding guarantees this timeout can never cancel the
            # task it is waiting on, so a hung server leaks its subprocess.
            await asyncio.wait_for(self._task, 10)
        except (TimeoutError, asyncio.CancelledError):
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)


def _register(server: Server, tool: McpTool) -> str:
    name = f"mcp__{server.name}__{tool.name}"
    if len(name) > 64:
        # Truncating alone silently collapses two distinct tools into one key.
        import hashlib
        name = name[:57] + "_" + hashlib.sha1(name.encode()).hexdigest()[:6]

    reads = tool.name.lower().startswith(READ_PREFIXES)

    async def call(**kwargs: Any) -> str:
        session = server.session
        assert session is not None, "call() invoked before the server finished starting"
        LAST_USED[server.name] = time.time()
        try:
            result = await asyncio.wait_for(
                session.call_tool(tool.name, kwargs), CALL_TIMEOUT)
        except TimeoutError:
            return f"error: MCP server {server.name!r} timed out after {CALL_TIMEOUT}s"
        parts = [c.text for c in result.content if isinstance(c, TextContent) and c.text]
        body = tools.truncate("\n".join(parts) or "(no content)")
        # Remote content is writable by anyone in that workspace: mark the turn
        # so bash drops to ASK for the rest of it.
        tools.set_tainted(True)
        body = (f"<untrusted source=\"mcp:{server.name}/{tool.name}\">\n{body}\n"
                f"</untrusted>")
        if getattr(result, "isError", False):
            return f"error: {body}"
        return body

    if reads:
        tools.READ_ONLY.add(name)
    tools.REGISTRY[name] = tools.ToolEntry(
        fn=call, locks_path=None, mutates=not reads, deferred=True,
        schema={"type": "function", "function": {
            "name": name,
            "description": (tool.description or "")[:DESC_LIMIT],
            "parameters": fit_params(tool.inputSchema),
        }},
    )
    _REGISTERED.setdefault(server.name, []).append(name)
    return name


def _unregister(name: str) -> None:
    for tool_name in _REGISTERED.pop(name, []):
        tools.REGISTRY.pop(tool_name, None)
        tools.READ_ONLY.discard(tool_name)


async def load(only: set[str] | None = None, timeout: float = 60) -> dict[str, str]:
    """Eager path: connect everything now, in a loop (`omega --mcp`). Lazy
    loading (ensure_loaded, below) is what a normal turn uses instead."""
    report: dict[str, str] = {}
    for name, cfg in discover().items():
        if only and name not in only:
            continue
        if not cfg.get("enabled", True):
            report[name] = "disabled"
            continue
        if "command" not in cfg and not (cfg.get("url") or cfg.get("serverUrl")):
            report[name] = "skipped: no command or url"
            continue
        server = Server(name, as_stdio(cfg))
        try:
            await server.start(timeout)
            for t in server.tools:
                _register(server, t)
            SERVERS[name] = server
            report[name] = f"{len(server.tools)} tools"
        except Exception as e:
            # A failed start still leaves the _run task and its npx/node
            # children alive; the common failure is a hung server.
            try:
                await server.stop()
            except Exception:
                pass
            report[name] = f"failed: {type(e).__name__}: {e}"[:110]
    return report


async def shutdown() -> None:
    for s in SERVERS.values():
        try:
            await s.stop()
        except Exception:
            pass


def _clean_spec(spec: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in spec.items() if k not in ("enabled", "catalog")}


def _write_mcp(data: dict[str, dict[str, Any]]) -> None:
    """Same write pattern as permissions.remember: merge into the config the
    onboarding/setup flows already own, touch nothing but the "mcp" key."""
    raw = config._json_or_default()
    raw["mcp"] = data
    config.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.CONFIG_PATH.write_text(json.dumps(raw, indent=2) + "\n")
    config.CONFIG_PATH.chmod(0o600)


def status() -> dict[str, ServerStatus]:
    """One row per server omega itself has configured (config.mcp_config()) --
    not what discover() would merge in from Claude Code, which the caller
    surfaces separately as "importable"."""
    out: dict[str, ServerStatus] = {}
    for name, cfg in config.mcp_config().items():
        enabled = bool(cfg.get("enabled", True))
        last_used = LAST_USED.get(name)
        if not enabled:
            out[name] = ServerStatus(name, False, "disabled", 0, None, last_used)
            continue
        server = SERVERS.get(name)
        if server is None:
            out[name] = ServerStatus(name, True, "configured", 0, None, last_used)
        elif server.error:
            out[name] = ServerStatus(name, True, "error", 0, server.error, last_used)
        elif server.ready.is_set():
            out[name] = ServerStatus(name, True, "connected", len(server.tools), None, last_used)
        elif server.auth_url:
            out[name] = ServerStatus(name, True, "needs_auth", 0, server.auth_url, last_used)
        else:
            out[name] = ServerStatus(name, True, "configured", 0, None, last_used)
    return out


async def connect(name: str, timeout: float | None = None) -> ServerStatus:
    """Connect one server. For a remote oauth server this is what triggers
    mcp-remote's browser flow; if it hasn't finished within `timeout` we
    check the child's stderr for an authorize-me URL and report needs_auth
    instead of failing outright -- the caller re-runs connect() once the
    user has clicked through it."""
    timeout = AUTH_TIMEOUT if timeout is None else timeout
    all_cfg = config.mcp_config()
    cfg = all_cfg.get(name)
    if cfg is None:
        return ServerStatus(name, False, "error", 0, f"no such server {name!r}", None)
    if not cfg.get("enabled", True):
        return ServerStatus(name, False, "disabled", 0, None, LAST_USED.get(name))

    server = SERVERS.get(name)
    if server is None or server.error:
        if server is not None:
            await server.stop()
        server = Server(name, as_stdio(_clean_spec(cfg)))
        SERVERS[name] = server
        server._task = asyncio.create_task(server._run())

    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while not server.ready.is_set() and loop.time() < deadline:
        remaining = max(deadline - loop.time(), 0.01)
        try:
            await asyncio.wait_for(server.ready.wait(), min(POLL_INTERVAL, remaining))
        except TimeoutError:
            url = server.peek_auth_url()
            if url:
                server.auth_url = url

    if not server.ready.is_set():
        if server.auth_url:
            return ServerStatus(name, True, "needs_auth", 0, server.auth_url, LAST_USED.get(name))
        server.error = f"timed out connecting after {timeout:.0f}s"
        return ServerStatus(name, True, "error", 0, server.error, LAST_USED.get(name))

    if server.error:
        return ServerStatus(name, True, "error", 0, server.error, LAST_USED.get(name))

    server.auth_url = None
    if name not in _REGISTERED:
        for t in server.tools:
            _register(server, t)
    return ServerStatus(name, True, "connected", len(server.tools), None, LAST_USED.get(name))


async def disconnect(name: str) -> None:
    server = SERVERS.pop(name, None)
    if server is not None:
        await server.stop()
    _unregister(name)


async def enable(name: str, value: bool) -> None:
    data = config.mcp_config()
    if name not in data:
        raise KeyError(f"no such server {name!r}")
    data[name]["enabled"] = value
    _write_mcp(data)
    if not value:
        await disconnect(name)


def add(name: str, spec: dict[str, Any]) -> None:
    data = config.mcp_config()
    entry = dict(spec)
    entry.setdefault("enabled", True)
    data[name] = entry
    _write_mcp(data)


async def remove(name: str) -> None:
    await disconnect(name)
    data = config.mcp_config()
    if name in data:
        del data[name]
        _write_mcp(data)


async def ensure_loaded(timeout: float = LAZY_TIMEOUT) -> None:
    """Called by find_tools/call_tool on first use each process: connects
    every enabled server that hasn't been attempted yet, in parallel.
    Failures are recorded as `error`/`needs_auth` state, never raised --
    a broken integration must not break every other tool call."""
    pending = [name for name, cfg in config.mcp_config().items()
              if cfg.get("enabled", True) and name not in SERVERS]
    if not pending:
        return
    await asyncio.gather(*(connect(n, timeout=timeout) for n in pending),
                        return_exceptions=True)


def summary_line() -> str:
    """For the system prompt: `linear (23 tools), notion (14 tools), slack
    (needs auth)`. Disabled servers are omitted."""
    parts = []
    for name, st in sorted(status().items()):
        if st.state == "disabled":
            continue
        if st.state == "connected":
            parts.append(f"{name} ({st.tools} tools)")
        elif st.state == "needs_auth":
            parts.append(f"{name} (needs auth)")
        elif st.state == "error":
            parts.append(f"{name} (error)")
        else:
            parts.append(f"{name} (not connected)")
    return ", ".join(parts)
