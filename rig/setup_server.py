import asyncio
import json
import re
import secrets
import threading
import webbrowser
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx

from . import config, events, mcp

UI = Path(__file__).parent / "setup.html"
TOKEN = secrets.token_urlsafe(16)


def _cfg() -> dict:
    p = config.CONFIG_PATH
    if p.exists():
        return json.loads(config._strip_jsonc(p.read_text()))
    return config.DEFAULTS


ALLOWED_TOP = {"providers", "roles", "mcp"}


def _validate(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError("config must be an object")
    unknown = set(data) - ALLOWED_TOP
    if unknown:
        raise ValueError(f"unexpected keys: {sorted(unknown)}")
    for name, prov in (data.get("providers") or {}).items():
        url = prov.get("baseUrl", "")
        if not re.match(r"^https://[A-Za-z0-9.\-]+(:\d+)?(/[\w./\-]*)?$", url):
            raise ValueError(f"provider {name!r}: baseUrl must be a plain https URL")
    return data


def _save(data: dict) -> dict:
    _validate(data)
    p = config.CONFIG_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2) + "\n")
    p.chmod(0o600)
    return {"ok": True, "path": str(p)}


def _provider(body) -> tuple:
    cfg = _cfg()
    name = body.get("provider") or next(iter(cfg["providers"]))
    p = cfg["providers"][name]
    key = body.get("apiKey") or p.get("apiKey") or ""
    if not key and p.get("apiKeyEnv"):
        import os
        key = os.environ.get(p["apiKeyEnv"], "")
    return p["baseUrl"].rstrip("/"), key


def api_models(body) -> dict:
    base, key = _provider(body)
    if not key:
        return {"error": "no API key set"}
    r = httpx.get(f"{base}/models", headers={"Authorization": f"Bearer {key}"}, timeout=30)
    if r.status_code != 200:
        return {"error": f"HTTP {r.status_code}: {r.text[:200]}"}
    return {"models": sorted(m["id"] for m in r.json().get("data", []))}


def api_test_model(body) -> dict:
    import time
    base, key = _provider(body)
    model = body["model"]
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


def api_mcp_discover(body) -> dict:
    out = []
    for name, cfg in mcp.discover().items():
        out.append({"name": name, "stdio": "command" in cfg,
                    "detail": cfg.get("command", cfg.get("url", ""))})
    return {"servers": sorted(out, key=lambda s: (not s["stdio"], s["name"]))}


def api_mcp_test(body) -> dict:
    name, spec = body["name"], body["config"]
    async def go():
        s = mcp.Server(name, spec)
        try:
            await s.start(timeout=120)
            return {"ok": True, "tools": [t.name for t in s.tools]}
        except Exception as e:
            return {"ok": False, "error": str(e)[:200]}
        finally:
            await s.stop()
    return asyncio.run(go())


BENCH_CANDIDATES = ["glm-5.3-flash", "kimi-k3", "gemini-2.5-flash-lite",
                    "gemini-3.5-flash-lite", "claude-haiku-4-5", "gpt-4.1-mini",
                    "deepseek-v4-flash", "kimi-k3-fast"]


def api_benchmark(body) -> dict:
    """Measure time-to-first-token and tool-calling for candidate models."""
    import time
    base, key = _provider(body)
    models = body.get("models") or BENCH_CANDIDATES
    catalog = api_models(body).get("models", [])
    if catalog:
        models = [m for m in models if m in catalog]

    async def one(client, model):
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

    async def go():
        # Sequential on purpose: concurrent requests contend and scramble the
        # ranking, which is the one thing this measurement exists to produce.
        async with httpx.AsyncClient() as c:
            return [await one(c, m) for m in models]

    rows = asyncio.run(go())
    rows.sort(key=lambda r: (not r["ok"], r.get("ms", 9e9)))
    return {"results": rows}


PROBE_PROMPT = ("Run 'pwd' and 'date' with bash, then say in one short "
                "sentence where you are and what day it is.")


def api_agent(body) -> dict:
    """Run one real rig turn so onboarding ends on proof it works."""
    from . import loop
    calls: list = []
    def emit(ev):
        if isinstance(ev, events.ToolStart):
            calls.append(ev.name)
    async def go():
        cfg = config.load()
        history = [{"role": "user", "content": PROBE_PROMPT}]
        text = await loop.run_agent(cfg, "main",
                                    loop.BUILD_SYSTEM, history,
                                    emit=emit)
        return text
    try:
        return {"ok": True, "text": asyncio.run(go()), "tools": calls}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:250]}


def _redacted_cfg(_b=None) -> dict:
    import copy
    c = copy.deepcopy(_cfg())
    for prov in (c.get("providers") or {}).values():
        if prov.get("apiKey"):
            prov["apiKey"] = ""
            prov["hasKey"] = True
            prov["keyMask"] = "••••••••"
    return c


ROUTES = {
    "/api/config": _redacted_cfg,
    "/api/save": _save,
    "/api/models": api_models,
    "/api/test-model": api_test_model,
    "/api/mcp/discover": api_mcp_discover,
    "/api/mcp/test": api_mcp_test,
    "/api/benchmark": api_benchmark,
    "/api/agent": api_agent,
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _authed(self, url) -> bool:
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

    def _send(self, code, body, ctype="application/json"):
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
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

    def do_POST(self):
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


def serve(port=0, open_browser=True):
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
