import asyncio, json, os, shutil
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from . import tools

SERVERS: dict = {}

# Pinned: an unpinned `npx -y mcp-remote` executes whatever npm serves as
# latest, on every single run.
MCP_REMOTE_VERSION = "0.8.1"
CALL_TIMEOUT = 120

# MCP tools whose names indicate a read; these become available to plan mode
# and subagents instead of every MCP tool being treated as mutating.
READ_PREFIXES = ("get_", "list_", "search", "read_", "fetch", "query", "resolve")

DESC_LIMIT = 2000        # descriptions drive tool SELECTION -- keep them
PARAMS_LIMIT = 1500      # inputSchema is 78% of the bytes and mostly noise
ENUM_LIMIT = 12
NOISE_KEYS = {"examples", "example", "default", "$comment", "$schema", "$id",
              "additionalProperties", "readOnly", "writeOnly", "deprecated"}


def prune_schema(node, depth=0):
    """Strip JSON Schema down to what a model needs to CALL a tool.

    Measured on linear+notion+posthog: descriptions were 59.5k chars, parameters
    143k. rig previously capped descriptions and passed parameters untouched --
    exactly backwards, since descriptions are what tool selection depends on.
    """
    if isinstance(node, list):
        return [prune_schema(x, depth + 1) for x in node[:ENUM_LIMIT]]
    if not isinstance(node, dict):
        return node

    out = {}
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


def fit_params(schema: dict) -> dict:
    """Prune, then drop optional property descriptions until it fits."""
    pruned = prune_schema(schema or {"type": "object", "properties": {}})
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


def discover(paths=None) -> dict:
    """rig's own mcp block wins; Claude Code's config and installed plugins
    (which ship their own .mcp.json) are imported under it."""
    if paths is None:
        paths = [Path.home() / ".claude.json", Path.home() / ".claude" / "settings.json"]
        plugins = Path.home() / ".claude" / "plugins"
        if plugins.exists():
            for depth in range(1, 5):
                paths += sorted(plugins.glob("/".join(["*"] * depth) + "/.mcp.json"))
    found: dict = {}

    def walk(o):
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

    rig_cfg = Path(os.environ.get("RIG_CONFIG", Path.home() / ".rig" / "config.json"))
    if rig_cfg.exists():
        try:
            found.update(json.loads(rig_cfg.read_text()).get("mcp", {}))
        except json.JSONDecodeError:
            pass
    return found


def as_stdio(cfg: dict) -> dict:
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

    def __init__(self, name: str, cfg: dict):
        self.name, self.cfg = name, cfg
        self.session = None
        self.tools: list = []
        self.error = None
        self.ready = asyncio.Event()
        self._stop = asyncio.Event()
        self._task = None

    async def _run(self):
        try:
            command = self.cfg["command"]
            if not shutil.which(command):
                raise FileNotFoundError(f"{command!r} not on PATH")
            params = StdioServerParameters(
                command=command, args=self.cfg.get("args", []),
                env={**os.environ, **self.cfg.get("env", {})})
            async with stdio_client(params) as (read, write):
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

    async def start(self, timeout=60):
        self._task = asyncio.create_task(self._run())
        await asyncio.wait_for(self.ready.wait(), timeout)
        if self.error:
            raise RuntimeError(self.error)

    async def stop(self):
        self._stop.set()
        if not self._task:
            return
        try:
            # No shield: shielding guarantees this timeout can never cancel the
            # task it is waiting on, so a hung server leaks its subprocess.
            await asyncio.wait_for(self._task, 10)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)


def _register(server: Server, tool) -> str:
    name = f"mcp__{server.name}__{tool.name}"
    if len(name) > 64:
        # Truncating alone silently collapses two distinct tools into one key.
        import hashlib
        name = name[:57] + "_" + hashlib.sha1(name.encode()).hexdigest()[:6]

    reads = tool.name.lower().startswith(READ_PREFIXES)

    async def call(**kwargs):
        try:
            result = await asyncio.wait_for(
                server.session.call_tool(tool.name, kwargs), CALL_TIMEOUT)
        except asyncio.TimeoutError:
            return f"error: MCP server {server.name!r} timed out after {CALL_TIMEOUT}s"
        parts = [c.text for c in result.content if getattr(c, "text", None)]
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
    tools.REGISTRY[name] = {
        "fn": call, "locks_path": None, "mutates": not reads, "deferred": True,
        "schema": {"type": "function", "function": {
            "name": name,
            "description": (tool.description or "")[:DESC_LIMIT],
            "parameters": fit_params(tool.inputSchema),
        }},
    }
    return name


async def load(only=None, timeout=60) -> dict:
    report = {}
    for name, cfg in discover().items():
        if only and name not in only:
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


async def shutdown():
    for s in SERVERS.values():
        try:
            await s.stop()
        except Exception:
            pass
