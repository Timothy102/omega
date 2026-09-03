import io
import sys

import pytest
from rich.console import Console

from omega import __main__ as m
from omega import config


@pytest.fixture(autouse=True)
def isolate_console(monkeypatch):
    buf = io.StringIO()
    monkeypatch.setattr(m, "console", Console(file=buf, force_terminal=False, width=200))
    yield buf


def _forbid_config_load(monkeypatch):
    def boom():
        raise AssertionError("config.load() must not run for this invocation")
    monkeypatch.setattr(config, "load", boom)


@pytest.mark.asyncio
async def test_help_flag_prints_usage_without_loading_config(monkeypatch, isolate_console):
    monkeypatch.setattr(sys, "argv", ["omega", "--help"])
    _forbid_config_load(monkeypatch)
    await m.main()
    out = isolate_console.getvalue()
    assert "usage:" in out and "subcommands:" in out


@pytest.mark.asyncio
async def test_h_flag_prints_usage(monkeypatch, isolate_console):
    monkeypatch.setattr(sys, "argv", ["omega", "-h"])
    _forbid_config_load(monkeypatch)
    await m.main()
    assert "usage:" in isolate_console.getvalue()


@pytest.mark.asyncio
async def test_help_word_prints_usage(monkeypatch, isolate_console):
    monkeypatch.setattr(sys, "argv", ["omega", "help"])
    _forbid_config_load(monkeypatch)
    await m.main()
    assert "usage:" in isolate_console.getvalue()


@pytest.mark.asyncio
async def test_version_flag_prints_version_without_loading_config(monkeypatch, isolate_console):
    monkeypatch.setattr(sys, "argv", ["omega", "--version"])
    _forbid_config_load(monkeypatch)
    await m.main()
    out = isolate_console.getvalue()
    assert "omega" in out


@pytest.mark.asyncio
async def test_unknown_leading_flag_errors_instead_of_becoming_a_prompt(monkeypatch, isolate_console):
    monkeypatch.setattr(sys, "argv", ["omega", "--bogus"])
    _forbid_config_load(monkeypatch)
    await m.main()
    out = isolate_console.getvalue()
    assert "unknown flag" in out
    assert "--bogus" in out


@pytest.mark.asyncio
async def test_prompt_starting_with_plain_word_still_reaches_run_prompt(monkeypatch, isolate_console):
    """Regression guard for the fix above: a real one-shot prompt (no leading
    flag) must still be dispatched normally, not accidentally rejected."""
    from omega import tools

    monkeypatch.setattr(sys, "argv", ["omega", "--yolo", "fix", "the", "bug"])
    monkeypatch.setattr(tools, "SESSION_ID", None)

    class FakeRole:
        provider = type("P", (), {"has_key": True})()

    class FakeConfigPath:
        @staticmethod
        def exists() -> bool:
            return True

    class FakeCfg:
        def role(self, name):
            return FakeRole()

    monkeypatch.setattr(config, "load", lambda: FakeCfg())
    monkeypatch.setattr(config, "CONFIG_PATH", FakeConfigPath())

    called = {}

    async def fake_run_prompt(cfg, history, prompt, mode, sess=None, model=None):
        called["prompt"] = prompt

    async def fake_consolidate_on_close(cfg):
        return None

    monkeypatch.setattr(m.plain, "run_prompt", fake_run_prompt)
    monkeypatch.setattr(m, "_consolidate_on_close", fake_consolidate_on_close)

    await m.main()
    assert called.get("prompt") == "fix the bug"
