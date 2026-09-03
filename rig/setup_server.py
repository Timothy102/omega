import asyncio
import json
import os
import re
import secrets
import shlex
import threading
import webbrowser
from collections.abc import Callable
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import ParseResult, parse_qs, urlparse

import httpx

from . import config, events, integrations, mcp

Body = dict[str, Any]

UI = Path(__file__).parent / "setup.html"
TOKEN = secrets.token_urlsafe(16)


def _cfg() -> Body:
    p = config.CONFIG_PATH
    if p.exists():
        return dict(json.loads(config._strip_jsonc(p.read_text())))
    return config.DEFAULTS


ALLOWED_TOP = {"providers", "models", "roles", "mcp"}


def _validate(data: Body) -> Body:
    if not isinstance(data, dict):
        raise ValueError("config must be an object")
    unknown = set(data) - ALLOWED_TOP
    if unknown:
        raise ValueError(f"unexpected keys: {sorted(unknown)}")
    for name, prov in (data.get("providers") or {}).items():
        if prov.get("type") == "anthropic":
            continue  # no baseUrl needed -- the SDK talks to api.anthropic.com directly
        url = prov.get("baseUrl", "")
        if not re.match(r"^https://[A-Za-z0-9.\-]+(:\d+)?(/[\w./\-]*)?$", url):
            raise ValueError(f"provider {name!r}: baseUrl must be a plain https URL")
    return data


def _save(data: Body) -> Body:
    _validate(data)
    p = config.CONFIG_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2) + "\n")
    p.chmod(0o600)
    return {"ok": True, "path": str(p)}


def _provider(body: Body) -> tuple[str, str]:
    cfg = _cfg()
    name = body.get("provider") or next(iter(cfg["providers"]))
    p = cfg["providers"][name]
    key = body.get("apiKey") or p.get("apiKey") or ""
    if not key and p.get("apiKeyEnv"):
        import os
        key = os.environ.get(p["apiKeyEnv"], "")
    return (p.get("baseUrl") or "").rstrip("/"), key


def _provider_type(body: Body) -> str:
    cfg = _cfg()
    name = body.get("provider") or next(iter(cfg["providers"]))
    return str(cfg["providers"][name].get("type", "openai"))


def _anthropic_catalog() -> list[str]:
    return sorted({m["model"] for m in config.DEFAULTS["models"].values()
                  if m["model"].startswith("claude")})


async def _probe_anthropic(model: str, key: str, prompt: str) -> Body:
    """The latency/reachability probe, routed through llm.stream instead of a
    raw httpx POST -- an anthropic provider speaks the Messages API, not
    OpenAI's /chat/completions."""
    import time

    from . import llm
    from .config import Provider, Role
    if not key:
        return {"model": model, "ok": False, "error": "no API key set"}
    role = Role(model=model, provider=Provider(name="setup-probe", type="anthropic", api_key_literal=key),
               context=200000)
    t0 = time.perf_counter()
    try:
        async for kind, _payload in llm.stream(role, [{"role": "user", "content": prompt}]):
            if kind == "done":
                break
        return {"model": model, "ok": True, "ms": round((time.perf_counter() - t0) * 1000)}
    except Exception as e:
        return {"model": model, "ok": False, "ms": round((time.perf_counter() - t0) * 1000),
                "error": f"{type(e).__name__}: {e}"[:120]}


def api_models(body: Body) -> Body:
    if _provider_type(body) == "anthropic":
        return {"models": _anthropic_catalog()}
    base, key = _provider(body)
    if not key:
        return {"error": "no API key set"}
    r = httpx.get(f"{base}/models", headers={"Authorization": f"Bearer {key}"}, timeout=30)
    if r.status_code != 200:
        return {"error": f"HTTP {r.status_code}: {r.text[:200]}"}
    return {"models": sorted(m["id"] for m in r.json().get("data", []))}


def api_test_model(body: Body) -> Body:
    import time
    model = body["model"]
    if _provider_type(body) == "anthropic":
        _base, key = _provider(body)
        return asyncio.run(_probe_anthropic(model, key, "Reply with the single word: ok"))
    base, key = _provider(body)
    t0 = time.perf_counter()
    try:
        r = httpx.post(f"{base}/chat/completions",
                       headers={"Authorization": f"Bearer {key}"},
                       json={"model": model, "max_tokens": 40, "messages":
                             [{"role": "user", "content": "Reply with the single word: ok"}],
                             "tools": [{"type": "function", "function": {
                                 "name": "noop", "description": "no-op",
                                 "parameters": {"type": "object", "properties": {}}}}]},
                       timeout=60)
    except Exception as e:
        return {"model": model, "ok": False, "error": f"{type(e).__name__}"}
    dt = (time.perf_counter() - t0) * 1000
    if r.status_code != 200:
        return {"model": model, "ok": False, "ms": round(dt),
                "error": f"HTTP {r.status_code}: {r.text[:120]}"}
    return {"model": model, "ok": True, "ms": round(dt)}


def api_mcp_discover(body: Body) -> Body:
    out = []
    for name, cfg in mcp.discover().items():
        out.append({"name": name, "stdio": "command" in cfg,
                    "detail": cfg.get("command", cfg.get("url", ""))})
    return {"servers": sorted(out, key=lambda s: (not s["stdio"], s["name"]))}


def api_mcp_test(body: Body) -> Body:
    name, spec = body["name"], body["config"]
    async def go() -> Body:
        s = mcp.Server(name, spec)
        try:
            await s.start(timeout=120)
            return {"ok": True, "tools": [t.name for t in s.tools]}
        except Exception as e:
            return {"ok": False, "error": str(e)[:200]}
        finally:
            await s.stop()
    return asyncio.run(go())


def _status_json(st: "mcp.ServerStatus") -> Body:
    return {"name": st.name, "enabled": st.enabled, "state": st.state,
            "tools": st.tools, "error": st.error, "lastUsed": st.last_used}


def api_connections_list(_b: Body) -> Body:
    configured = mcp.status()
    raw = config.mcp_config()
    known = set(configured)
    claude = {n: c for n, c in integrations.imported_from_claude_code().items() if n not in known}

    def row(name: str, st: "mcp.ServerStatus") -> Body:
        key = raw.get(name, {}).get("catalog")
        cat = integrations.CATALOG.get(key) if key else None
        out = _status_json(st)
        out["auth"] = cat.auth if cat else None
        out["category"] = cat.category if cat else None
        return out

    configured_rows = [row(n, st) for n, st in sorted(configured.items())]
    catalog_rows = [{"key": i.key, "name": i.name, "category": i.category, "blurb": i.blurb,
                     "auth": i.auth, "transport": i.transport, "verified": i.verified,
                     "env": list(i.env), "docs": i.docs}
                    for i in sorted(integrations.CATALOG.values(), key=lambda x: x.key)
                    if i.key not in known]
    claude_rows = [{"name": n, **c} for n, c in sorted(claude.items())]
    return {"configured": configured_rows, "catalog": catalog_rows, "claudeCode": claude_rows}


def api_connections_add(body: Body) -> Body:
    name = body.get("name")
    if not name:
        return {"error": "name required"}

    if body.get("source") == "claude-code":
        # Never round-trip a real secret through the browser: read the
        # unredacted spec server-side and write it straight into rig's config.
        raw = mcp.discover(include_rig=False)
        found = raw.get(name)
        if found is None:
            return {"error": f"{name!r} not found in Claude Code's config"}
        mcp.add(name, dict(found))
        return {"ok": True}

    key = body.get("key")
    catalog = integrations.CATALOG.get(key) if key else integrations.CATALOG.get(name)
    spec: Body = {}
    if catalog is not None:
        spec["catalog"] = catalog.key
        if catalog.transport == "remote" and catalog.url:
            spec["url"] = catalog.url
        elif catalog.command:
            cmdline = [c.replace("<cwd>", os.getcwd()) for c in catalog.command]
            spec["command"], spec["args"] = cmdline[0], cmdline[1:]

    if body.get("url"):
        spec["url"] = body["url"]
        spec.pop("command", None)
        spec.pop("args", None)
    if body.get("command"):
        parts = shlex.split(body["command"])
        spec["command"], spec["args"] = parts[0], parts[1:]
        spec.pop("url", None)
    if body.get("env"):
        spec["env"] = body["env"]

    if not spec.get("command") and not spec.get("url"):
        return {"error": "give a url, command, or known catalog key"}
    mcp.add(name, spec)
    return {"ok": True}


def api_connections_connect(body: Body) -> Body:
    st = asyncio.run(mcp.connect(body["name"], timeout=body.get("timeout")))
    return _status_json(st)


def api_connections_toggle(body: Body) -> Body:
    try:
        asyncio.run(mcp.enable(body["name"], bool(body.get("enabled"))))
    except KeyError as e:
        return {"error": str(e)}
    return {"ok": True}


def api_connections_test(body: Body) -> Body:
    async def go() -> "mcp.ServerStatus":
        st = await mcp.connect(body["name"], timeout=body.get("timeout", 30))
        await mcp.disconnect(body["name"])
        return st
    return _status_json(asyncio.run(go()))


def api_connections_remove(body: Body) -> Body:
    asyncio.run(mcp.remove(body["name"]))
    return {"ok": True}


BENCH_CANDIDATES = ["glm-5.3-flash", "kimi-k3", "gemini-2.5-flash-lite",
                    "gemini-3.5-flash-lite", "claude-haiku-4-5", "gpt-4.1-mini",
                    "deepseek-v4-flash", "kimi-k3-fast"]


def api_benchmark(body: Body) -> Body:
    """Measure time-to-first-token and tool-calling for candidate models."""
    import time
    if _provider_type(body) == "anthropic":
        _base, key = _provider(body)
        models = body.get("models") or _anthropic_catalog()

        async def go_anthropic() -> list[Body]:
            return [await _probe_anthropic(m, key, "Say ok.") for m in models]

        rows = asyncio.run(go_anthropic())
        rows.sort(key=lambda r: (not r["ok"], r.get("ms", 9e9)))
        return {"results": rows}

    base, key = _provider(body)
    models = body.get("models") or BENCH_CANDIDATES
    catalog = api_models(body).get("models", [])
    if catalog:
        models = [m for m in models if m in catalog]

    async def one(client: httpx.AsyncClient, model: str) -> Body:
        t0 = time.perf_counter()
        try:
            r = await client.post(f"{base}/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={"model": model, "max_tokens": 30, "messages":
                      [{"role": "user", "content": "Say ok."}],
                      "tools": [{"type": "function", "function": {"name": "noop",
                                 "description": "no-op",
                                 "parameters": {"type": "object", "properties": {}}}}]},
                timeout=45)
            ms = round((time.perf_counter() - t0) * 1000)
            if r.status_code != 200:
                return {"model": model, "ok": False, "error": f"HTTP {r.status_code}"}
            return {"model": model, "ok": True, "ms": ms}
        except Exception as e:
            return {"model": model, "ok": False, "error": type(e).__name__}

    async def go() -> list[Body]:
        # Sequential on purpose: concurrent requests contend and scramble the
        # ranking, which is the one thing this measurement exists to produce.
        async with httpx.AsyncClient() as c:
            return [await one(c, m) for m in models]

    rows = asyncio.run(go())
    rows.sort(key=lambda r: (not r["ok"], r.get("ms", 9e9)))
    return {"results": rows}


PROBE_PROMPT = ("Run 'pwd' and 'date' with bash, then say in one short "
                "sentence where you are and what day it is.")


def api_agent(body: Body) -> Body:
    """Run one real rig turn so onboarding ends on proof it works."""
    from . import loop
    calls: list[str] = []
    def emit(ev: events.Event) -> None:
        if isinstance(ev, events.ToolStart):
            calls.append(ev.name)
    async def go() -> str:
        cfg = config.load()
        history: list[Body] = [{"role": "user", "content": PROBE_PROMPT}]
        text = await loop.run_agent(cfg, "main",
                                    loop.BUILD_SYSTEM, history,
                                    emit=emit)
        return text
    try:
        return {"ok": True, "text": asyncio.run(go()), "tools": calls}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:250]}


def _redacted_cfg(_b: Body | None = None) -> Body:
    import copy
    c = copy.deepcopy(_cfg())
    for prov in (c.get("providers") or {}).values():
        if prov.get("apiKey"):
            prov["apiKey"] = ""
            prov["hasKey"] = True
            prov["keyMask"] = "••••••••"
    # A role may be written as a catalog alias ({"alias": "opus"}); the wizard
    # only understands the inline {model, provider, context} shape, so resolve
    # every role to that shape for the page regardless of how it's stored.
    try:
        resolved = config.load()
        c["roles"] = {name: {"model": r.model, "provider": r.provider.name,
                             "context": r.context, "alias": r.alias}
                      for name, r in resolved.roles.items()}
    except SystemExit:
        pass
    return c


ROUTES: dict[str, Callable[[Body], Body]] = {
    "/api/config": _redacted_cfg,
    "/api/save": _save,
    "/api/models": api_models,
    "/api/test-model": api_test_model,
    "/api/mcp/discover": api_mcp_discover,
    "/api/mcp/test": api_mcp_test,
    "/api/connections/list": api_connections_list,
    "/api/connections/add": api_connections_add,
    "/api/connections/connect": api_connections_connect,
    "/api/connections/toggle": api_connections_toggle,
    "/api/connections/test": api_connections_test,
    "/api/connections/remove": api_connections_remove,
    "/api/benchmark": api_benchmark,
    "/api/agent": api_agent,
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a: Any) -> None:
        pass

    def _authed(self, url: ParseResult) -> bool:
        supplied = (self.headers.get("X-Rig-Token")
                    or parse_qs(url.query).get("t", [""])[0])
        return secrets.compare_digest(supplied, TOKEN)

    def _same_origin(self) -> bool:
        """Blocks DNS rebinding and cross-origin CSRF. Without this, a page you
        visit can drive these endpoints even without reading the response."""
        host = (self.headers.get("Host") or "").split(",")[0].strip()
        if host.rsplit(":", 1)[0] not in ("127.0.0.1", "localhost", "[::1]"):
            return False
        origin = self.headers.get("Origin")
        if origin and origin not in (f"http://{host}",):
            return False
        if (self.headers.get("Sec-Fetch-Site") or "same-origin") not in (
                "same-origin", "none"):
            return False
        return True

    def _send(self, code: int, body: bytes | Body, ctype: str = "application/json") -> None:
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        url = urlparse(self.path)
        if not self._same_origin():
            return self._send(403, {"error": "bad origin"})
        if not self._authed(url):
            return self._send(403, {"error": "bad token"})
        if url.path == "/":
            return self._send(200, UI.read_bytes(), "text/html; charset=utf-8")
        # API is POST-only: GET dispatch let <img src="/api/agent?t=..."> run
        # a full agent turn with bash from any page you happened to visit.
        if url.path in ROUTES:
            return self._send(405, {"error": "use POST"})
        self._send(404, {"error": "not found"})

    def do_POST(self) -> None:
        url = urlparse(self.path)
        if not self._same_origin():
            return self._send(403, {"error": "bad origin"})
        if not self._authed(url):
            return self._send(403, {"error": "bad token"})
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        fn = ROUTES.get(url.path)
        if not fn:
            return self._send(404, {"error": "not found"})
        try:
            self._send(200, fn(body))
        except Exception as e:
            self._send(200, {"error": f"{type(e).__name__}: {e}"[:300]})


def serve(port: int = 0, open_browser: bool = True) -> None:
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    port = httpd.server_address[1]
    url = f"http://127.0.0.1:{port}/?t={TOKEN}"
    print(f"rig setup → {url}\nctrl-c to stop")
    if open_browser:
        threading.Timer(0.4, partial(webbrowser.open, url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print()
