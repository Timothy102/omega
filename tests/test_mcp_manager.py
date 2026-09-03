import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from mcp.types import TextContent

from rig import config, mcp, tools

AUTH_URL = "https://example.com/oauth/authorize?token=abc123"


@dataclass
class FakeTool:
    name: str
    description: str = "a fake tool"
    inputSchema: dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})


class FakeToolsResult:
    def __init__(self, tools: list[FakeTool]):
        self.tools = tools


class FakeCallResult:
    def __init__(self, text: str, is_error: bool = False):
        self.content = [TextContent(type="text", text=text)]
        self.isError = is_error


class FakeSession:
    """Stands in for mcp.ClientSession. Behavior is driven by a `marker`
    smuggled through as the "read" stream by fake_stdio_client below --
    there's no other channel to tell two concurrent fake connections apart."""

    def __init__(self, read: Any, write: Any):
        self.marker = read

    async def __aenter__(self) -> "FakeSession":
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False

    async def initialize(self) -> None:
        if self.marker == "fail":
            raise RuntimeError("boom")
        if self.marker == "hang":
            await asyncio.sleep(999)

    async def list_tools(self) -> FakeToolsResult:
        return FakeToolsResult([FakeTool("get_thing"), FakeTool("do_thing")])

    async def call_tool(self, name: str, args: dict[str, Any]) -> FakeCallResult:
        return FakeCallResult(f"called {name} with {args}")


@asynccontextmanager
async def fake_stdio_client(params: Any, errlog: Any = None):
    marker = params.args[0] if params.args else "ok"
    if marker == "hang" and errlog is not None:
        errlog.write(f"Please authorize this client by visiting: {AUTH_URL}\n")
        errlog.flush()
    yield (marker, None)


def write_config(tmp_path: Path, mcp_block: dict[str, dict[str, Any]]) -> None:
    (tmp_path / "config.json").write_text(
        json.dumps({"providers": {}, "models": {}, "roles": {}, "mcp": mcp_block}))


@pytest.fixture(autouse=True)
async def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(mcp, "stdio_client", fake_stdio_client)
    monkeypatch.setattr(mcp, "ClientSession", FakeSession)
    # Fresh, test-local dicts -- monkeypatch restores the real (shared,
    # process-lifetime) ones afterwards, so nothing leaks between tests.
    monkeypatch.setattr(mcp, "SERVERS", {})
    monkeypatch.setattr(mcp, "LAST_USED", {})
    monkeypatch.setattr(mcp, "_REGISTERED", {})
    monkeypatch.setattr(tools, "REGISTRY", dict(tools.REGISTRY))
    monkeypatch.setattr(tools, "READ_ONLY", set(tools.READ_ONLY))
    yield
    # A "hang" server's task is stuck inside initialize(), before it ever
    # reaches `await self._stop.wait()` -- stop() would wait out its own 10s
    # grace timeout for nothing, so cancel directly instead.
    for s in list(mcp.SERVERS.values()):
        if s._task and not s._task.done():
            s._task.cancel()
    pending = [s._task for s in mcp.SERVERS.values() if s._task]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


async def test_status_disabled_server_never_attempts_connection(tmp_path):
    write_config(tmp_path, {"a": {"command": "echo", "args": ["ok"], "enabled": False}})
    st = mcp.status()["a"]
    assert st.state == "disabled"
    assert st.enabled is False
    assert "a" not in mcp.SERVERS


async def test_status_unattempted_server_is_configured(tmp_path):
    write_config(tmp_path, {"a": {"command": "echo", "args": ["ok"]}})
    st = mcp.status()["a"]
    assert st.state == "configured"
    assert st.tools == 0


async def test_connect_success_reaches_connected_state_and_registers_tools(tmp_path):
    write_config(tmp_path, {"a": {"command": "echo", "args": ["ok"]}})
    st = await mcp.connect("a")
    assert st.state == "connected"
    assert st.tools == 2
    assert "mcp__a__get_thing" in tools.REGISTRY
    assert "mcp__a__get_thing" in tools.READ_ONLY  # get_ prefix -> read-only
    assert mcp.status()["a"].state == "connected"


async def test_connect_failure_reaches_error_state(tmp_path):
    write_config(tmp_path, {"a": {"command": "echo", "args": ["fail"]}})
    st = await mcp.connect("a")
    assert st.state == "error"
    assert "boom" in (st.error or "")
    assert mcp.status()["a"].state == "error"


async def test_connect_unknown_server_is_an_error_not_an_exception(tmp_path):
    write_config(tmp_path, {})
    st = await mcp.connect("nope")
    assert st.state == "error"


async def test_needs_auth_detected_from_child_stderr(tmp_path, monkeypatch):
    """A server whose child hangs (simulating mcp-remote's browser OAuth wait)
    but printed an authorize-me URL to stderr must surface needs_auth with
    that URL, not a bare timeout error."""
    monkeypatch.setattr(mcp, "POLL_INTERVAL", 0.05)
    write_config(tmp_path, {"a": {"command": "echo", "args": ["hang"]}})
    st = await mcp.connect("a", timeout=0.5)
    assert st.state == "needs_auth"
    assert st.error == AUTH_URL
    assert mcp.status()["a"].state == "needs_auth"


async def test_connect_timeout_without_auth_url_is_a_plain_error(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp, "POLL_INTERVAL", 0.05)
    write_config(tmp_path, {"a": {"command": "echo", "args": ["silent-hang"]}})

    @asynccontextmanager
    async def hang_no_output(params: Any, errlog: Any = None):
        yield ("hang-no-output", None)

    class HangingNoOutputSession(FakeSession):
        async def initialize(self) -> None:
            await asyncio.sleep(999)

    monkeypatch.setattr(mcp, "stdio_client", hang_no_output)
    monkeypatch.setattr(mcp, "ClientSession", HangingNoOutputSession)
    st = await mcp.connect("a", timeout=0.3)
    assert st.state == "error"
    assert "timed out" in (st.error or "")


async def test_disconnect_removes_server_and_its_tools(tmp_path):
    write_config(tmp_path, {"a": {"command": "echo", "args": ["ok"]}})
    await mcp.connect("a")
    assert "mcp__a__get_thing" in tools.REGISTRY
    await mcp.disconnect("a")
    assert "a" not in mcp.SERVERS
    assert "mcp__a__get_thing" not in tools.REGISTRY
    assert mcp.status()["a"].state == "configured"


async def test_enable_false_persists_and_disconnects(tmp_path):
    write_config(tmp_path, {"a": {"command": "echo", "args": ["ok"]}})
    await mcp.connect("a")
    await mcp.enable("a", False)
    assert config.mcp_config()["a"]["enabled"] is False
    assert "a" not in mcp.SERVERS
    assert mcp.status()["a"].state == "disabled"


async def test_enable_unknown_server_raises_keyerror(tmp_path):
    write_config(tmp_path, {})
    with pytest.raises(KeyError):
        await mcp.enable("nope", True)


async def test_add_then_remove_round_trips_through_config(tmp_path):
    write_config(tmp_path, {})
    mcp.add("a", {"command": "echo", "args": ["ok"]})
    assert config.mcp_config()["a"]["enabled"] is True
    await mcp.connect("a")
    await mcp.remove("a")
    assert "a" not in config.mcp_config()
    assert "a" not in mcp.SERVERS


async def test_ensure_loaded_connects_each_enabled_server_exactly_once(tmp_path):
    write_config(tmp_path, {
        "a": {"command": "echo", "args": ["ok"]},
        "b": {"command": "echo", "args": ["ok"]},
        "c": {"command": "echo", "args": ["ok"], "enabled": False},
    })
    await mcp.ensure_loaded(timeout=5)
    assert set(mcp.SERVERS) == {"a", "b"}
    first_a, first_b = mcp.SERVERS["a"], mcp.SERVERS["b"]

    await mcp.ensure_loaded(timeout=5)
    # Same Server objects -- nothing reconnected on the second pass.
    assert mcp.SERVERS["a"] is first_a
    assert mcp.SERVERS["b"] is first_b


async def test_find_tools_triggers_lazy_connect_on_first_use(tmp_path):
    write_config(tmp_path, {"a": {"command": "echo", "args": ["ok"]}})
    assert "a" not in mcp.SERVERS

    result = await tools._find_tools("thing")
    assert "a" in mcp.SERVERS
    assert "get_thing" in result or "mcp__a__get_thing" in result


async def test_call_tool_triggers_lazy_connect_and_records_last_used(tmp_path):
    write_config(tmp_path, {"a": {"command": "echo", "args": ["ok"]}})
    assert "a" not in mcp.LAST_USED

    out = await tools._call_tool("mcp__a__get_thing", {})
    assert "called get_thing" in out
    assert "a" in mcp.LAST_USED


async def test_summary_line_reports_tools_auth_and_errors(tmp_path):
    write_config(tmp_path, {
        "linear": {"command": "echo", "args": ["ok"]},
        "broken": {"command": "echo", "args": ["fail"]},
        "off": {"command": "echo", "args": ["ok"], "enabled": False},
    })
    await mcp.connect("linear")
    await mcp.connect("broken")

    line = mcp.summary_line()
    assert "linear (2 tools)" in line
    assert "broken (error)" in line
    assert "off" not in line
