import io
import os
import sys

import pytest
from rich.console import Console

from omega import __main__ as m
from omega import config, session


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


@pytest.mark.asyncio
async def test_trace_subcommand_prints_the_timeline(monkeypatch, isolate_console, tmp_path):
    from omega import events as ev_mod
    from omega import trace as trace_mod

    monkeypatch.setattr(session, "DIR", tmp_path)
    sess = session.Session.new(cwd=str(tmp_path))
    sess.save()
    trace_mod.append(sess.id, ev_mod.Done(text="all done"), turn=1)

    monkeypatch.setattr(sys, "argv", ["omega", "trace", sess.id])
    await m.main()
    assert "Done" in isolate_console.getvalue()


@pytest.mark.asyncio
async def test_trace_subcommand_json_flag(monkeypatch, isolate_console, tmp_path):
    from omega import events as ev_mod
    from omega import trace as trace_mod

    monkeypatch.setattr(session, "DIR", tmp_path)
    sess = session.Session.new(cwd=str(tmp_path))
    sess.save()
    trace_mod.append(sess.id, ev_mod.Done(text="all done"), turn=1)

    monkeypatch.setattr(sys, "argv", ["omega", "trace", sess.id, "--json"])
    await m.main()
    out = isolate_console.getvalue()
    assert '"type": "Done"' in out or '"type":"Done"' in out


def test_installed_from_pypi_true_when_no_git_annotation():
    assert m._installed_from_pypi("omega-code v0.3.0\n- omega\n") is True


def test_installed_from_pypi_false_when_git_annotation_present():
    listing = "omega-code v0.3.0 [required:  git+https://github.com/Timothy102/omega.git@main]\n- omega\n"
    assert m._installed_from_pypi(listing) is False


def test_installed_from_pypi_defaults_true_when_not_a_uv_tool():
    assert m._installed_from_pypi("modal v1.0.0\n- modal\n") is True


@pytest.mark.asyncio
async def test_update_installs_from_pypi_when_uv_tool_list_has_no_git(monkeypatch, isolate_console):
    calls = []

    async def fake_run(cmd, merge_stderr=False):
        calls.append(cmd)
        if cmd[:2] == ["uv", "tool"] and cmd[2] == "list":
            return "omega-code v0.3.0\n- omega\n"
        if cmd[:3] == ["uv", "tool", "install"]:
            return "installed"
        if cmd == ["omega", "--version"]:
            return "omega 0.4.0\n"
        return ""

    monkeypatch.setattr(m, "_run", fake_run)
    await m._update()
    install_cmd = next(c for c in calls if c[:3] == ["uv", "tool", "install"])
    assert install_cmd[-1] == "omega-code"
    assert "omega 0.4.0" in isolate_console.getvalue()


@pytest.mark.asyncio
async def test_update_installs_from_git_when_uv_tool_list_shows_git_url(monkeypatch, isolate_console):
    calls = []

    async def fake_run(cmd, merge_stderr=False):
        calls.append(cmd)
        if cmd[:2] == ["uv", "tool"] and cmd[2] == "list":
            return "omega-code v0.3.0 [required:  git+https://github.com/Timothy102/omega.git@main]\n"
        if cmd[:3] == ["uv", "tool", "install"]:
            return "installed"
        if cmd == ["omega", "--version"]:
            return "omega 0.4.0\n"
        return ""

    monkeypatch.setattr(m, "_run", fake_run)
    await m._update()
    install_cmd = next(c for c in calls if c[:3] == ["uv", "tool", "install"])
    assert install_cmd[-1] == "git+https://github.com/Timothy102/omega.git@main"


def test_doctor_checks_report_a_missing_tool(monkeypatch):
    import shutil

    monkeypatch.setattr(shutil, "which", lambda name: None if name == "npx" else f"/usr/bin/{name}")

    class FakeConfigPath:
        @staticmethod
        def exists() -> bool:
            return False

    monkeypatch.setattr(config, "CONFIG_PATH", FakeConfigPath())
    rows = m._doctor_checks()
    by_label = {label: (ok, detail) for label, ok, detail in rows}
    assert by_label["npx"][0] is False
    assert by_label["rg"][0] is True
    assert by_label["config valid"] == (True, "no config file yet")


def test_doctor_checks_config_permissions_and_provider_keys(tmp_path, monkeypatch):
    import json
    import shutil

    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({
        "providers": {"anthropic": {"type": "anthropic", "apiKeyEnv": "ANTHROPIC_API_KEY"}},
        "models": {}, "roles": {},
    }))
    cfg_path.chmod(0o600)
    monkeypatch.setattr(config, "CONFIG_PATH", cfg_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    rows = m._doctor_checks()
    by_label = {label: (ok, detail) for label, ok, detail in rows}
    assert by_label["config permissions (0600)"] == (True, "0o600")
    assert by_label["provider key: anthropic"] == (False, "missing")


@pytest.mark.asyncio
async def test_resume_command_with_id_rewrites_to_resume_flag():
    assert await m._resume_command(["abc123"]) == ["--resume", "abc123"]
    assert await m._resume_command(["abc123", "extra"]) == ["--resume", "abc123", "extra"]


@pytest.mark.asyncio
async def test_resume_command_no_id_and_no_sessions(monkeypatch, isolate_console, tmp_path):
    monkeypatch.setattr(session, "DIR", tmp_path / "sessions")
    monkeypatch.setattr(os, "getcwd", lambda: str(tmp_path))
    result = await m._resume_command([])
    assert result is None
    assert "no sessions for this directory" in isolate_console.getvalue()


@pytest.mark.asyncio
async def test_resume_command_no_id_picks_a_numbered_session(monkeypatch, isolate_console, tmp_path):
    monkeypatch.setattr(session, "DIR", tmp_path / "sessions")
    cwd = str(tmp_path)
    monkeypatch.setattr(os, "getcwd", lambda: cwd)
    session.Session.new(cwd=cwd).save()
    session.Session.new(cwd=cwd).save()
    rows = [s for s in session.all_sessions() if s.cwd == cwd]

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *a, **k: "1")
    result = await m._resume_command([])
    assert result == ["--resume", rows[0].id]


@pytest.mark.asyncio
async def test_resume_command_no_id_non_tty_lists_only(monkeypatch, isolate_console, tmp_path):
    monkeypatch.setattr(session, "DIR", tmp_path / "sessions")
    cwd = str(tmp_path)
    monkeypatch.setattr(os, "getcwd", lambda: cwd)
    session.Session.new(cwd=cwd).save()
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    result = await m._resume_command([])
    assert result is None


@pytest.mark.asyncio
async def test_continue_subcommand_resolves_like_continue_flag(monkeypatch, isolate_console):
    monkeypatch.setattr(sys, "argv", ["omega", "continue"])

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
    monkeypatch.setattr(session, "latest", lambda: None)

    await m.main()
    assert "no session for this directory" in isolate_console.getvalue()
