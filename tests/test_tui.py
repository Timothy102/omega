import asyncio
import io
import json
import time
from pathlib import Path

import pytest
from rich.console import Console
from rich.markdown import Markdown
from rich.syntax import Syntax
from rich.table import Table
from textual.widgets import Input, Static

from omega import artifacts, compact, events, gitlog, loop, session
from omega.config import Model
from omega.ui import tui
from omega.ui.tui import app as app_module
from omega.ui.tui import prefs
from omega.ui.tui.modals import (
    AskUserScreen,
    ConfirmScreen,
    DiffScreen,
    ModelPickerScreen,
    SessionsScreen,
)
from omega.ui.tui.sidebar import ConnectionsTab, GitTab, Sidebar
from omega.ui.tui.status import StatusBar
from omega.ui.tui.transcript import Transcript, _MoreLine


class FakeProvider:
    name = "fake-provider"


class FakeRole:
    context = 1_000_000
    model = "fake-model"
    alias = None
    provider = FakeProvider()


class FakeCfg:
    models = {
        "opus": Model(alias="opus", model="claude-opus-5", provider="anthropic",
                      context=1_000_000, effort="high"),
        "haiku": Model(alias="haiku", model="claude-haiku-4-5", provider="anthropic",
                       context=200_000),
    }

    def role(self, name):
        return FakeRole()

    def model(self, alias):
        m = self.models[alias]
        return type("R", (), {"model": m.model, "alias": m.alias, "context": m.context,
                              "provider": FakeProvider(), "effort": m.effort})()

    def resolve_alias(self, text):
        if text in self.models:
            return text
        for alias, m in self.models.items():
            if m.model == text:
                return alias
        raise SystemExit(f"omega: unknown model {text!r}; have {sorted(self.models)}")


async def _fake_discover_repos(root, max_depth=2):
    return []


async def _fake_lookup_branch(cwd):
    return ""


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "DIR", tmp_path / "sessions")
    monkeypatch.setattr(tui, "HISTORY", tmp_path / "history")
    monkeypatch.setattr(artifacts, "DIR", tmp_path / "sessions")
    monkeypatch.setattr(prefs, "PATH", tmp_path / "ui.json")
    # The Git tab discovers real repos on mount in a worker; keep that fast
    # and hermetic by default, individual tests override it as needed.
    monkeypatch.setattr(gitlog, "discover_repos_async", _fake_discover_repos)
    # The header bar's branch lookup walks the real filesystem for a `.git`
    # dir; keep it hermetic and reset the process-lifetime cache per test.
    monkeypatch.setattr(app_module, "_lookup_branch", _fake_lookup_branch)
    monkeypatch.setattr(app_module, "_branch_cache", {})
    yield


def make_app() -> tui.OmegaApp:
    sess = session.Session.new(cwd=str(Path.cwd()))
    return tui.OmegaApp(FakeCfg(), sess, "build", sess.history)


def _texts(widget) -> list[str]:
    # An assistant prose block now renders as a `Table.grid` (the "●" bullet
    # column + a `Markdown` content column, for hanging-indent wrapping) --
    # `str(Table(...))` is just a repr, so render it through a real Console to
    # get back the text a person would actually see, same as the other branches.
    out = []
    for s in widget.query(Static):
        content = s.content
        if isinstance(content, Markdown):
            out.append(content.markup)
        elif isinstance(content, Syntax):
            out.append(content.code)
        elif isinstance(content, Table):
            buf = io.StringIO()
            Console(file=buf, width=200, force_terminal=False).print(content)
            out.append(buf.getvalue())
        else:
            out.append(str(content))
    return out


async def _wait_for(pilot, predicate, attempts=30):
    for _ in range(attempts):
        if predicate():
            return
        await pilot.pause()
    raise AssertionError("condition never became true")


@pytest.mark.asyncio
async def test_submit_streams_reply_offloads_and_updates_state(monkeypatch):
    app = make_app()
    close_calls = []
    monkeypatch.setattr(app.sess, "close_turn",
                        lambda h, m, i: close_calls.append((m, i)))

    async def fake_run_turn(cfg, history, mode, emit, model=None):
        emit(events.Phase("waiting"))
        emit(events.Phase("streaming"))
        emit(events.TextDelta("Hello "))
        emit(events.TextDelta("world"))
        emit(events.ToolStart(call_id="c1", name="bash", args_preview="bash  $ ls"))
        emit(events.ToolEnd(call_id="c1", name="bash", result_preview="ok",
                            duration_s=0.1, offloaded=True, artifact_id="deadbeef",
                            result_chars=4200, outcome="→ 4.2k chars · artifact deadbeef"))
        emit(events.Usage(prompt_tokens=1000, completion_tokens=200, used=1200, limit=1_000_000))
        emit(events.Done("Hello world"))
        history.append({"role": "assistant", "content": "Hello world"})
        emit(events.Phase("idle"))

    monkeypatch.setattr(loop, "run_turn", fake_run_turn)

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt", Input)
        app.set_focus(prompt)
        prompt.value = "hello"
        await pilot.press("enter")
        await _wait_for(pilot, lambda: app._turn_worker is None)

        texts = _texts(app.query_one(Transcript))
        assert any("Hello world" in t for t in texts)
        # The offload renders as its own dim "└" sub-line beneath the call.
        assert any("4.2k chars" in t and "deadbeef" in t and "└" in t for t in texts)
        assert not any(t.strip().startswith("↳") for t in texts)

        sidebar = app.query_one(Sidebar)
        assert sidebar.session_tab._tool_session["bash"] == 1

        status_text = str(app.query_one(StatusBar).content)
        assert "1.2k" in status_text
        assert app._phase == "idle"

        assert app.history[0] == {"role": "user", "content": "hello"}
        assert app.history[-1] == {"role": "assistant", "content": "Hello world"}
        assert close_calls == [("build", False)]


@pytest.mark.asyncio
async def test_live_status_line_shows_thinking_then_clears_on_idle(monkeypatch):
    app = make_app()
    reached_thinking = asyncio.Event()
    resume = asyncio.Event()

    async def fake_run_turn(cfg, history, mode, emit, model=None):
        emit(events.Phase("waiting"))
        emit(events.Phase("thinking"))
        reached_thinking.set()
        await resume.wait()
        emit(events.Phase("streaming"))
        emit(events.TextDelta("hi"))
        emit(events.Done("hi"))
        history.append({"role": "assistant", "content": "hi"})

    monkeypatch.setattr(loop, "run_turn", fake_run_turn)

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt", Input)
        app.set_focus(prompt)
        prompt.value = "hello"
        await pilot.press("enter")
        await _wait_for(pilot, lambda: reached_thinking.is_set())
        await pilot.pause()

        thinking_texts = _texts(app.query_one(Transcript))
        assert any("Thinking" in t for t in thinking_texts)

        resume.set()
        await _wait_for(pilot, lambda: app._turn_worker is None)

        assert app._phase == "idle"
        final_texts = _texts(app.query_one(Transcript))
        assert not any("Thinking" in t for t in final_texts)


@pytest.mark.asyncio
async def test_more_than_three_tool_calls_collapse_and_expand(monkeypatch):
    app = make_app()

    async def fake_run_turn(cfg, history, mode, emit, model=None):
        emit(events.Phase("waiting"))
        for i in range(5):
            emit(events.ToolStart(call_id=f"c{i}", name="read", args_preview=f"read  file{i}.py"))
        for i in range(5):
            emit(events.ToolEnd(call_id=f"c{i}", name="read", result_preview="ok",
                                duration_s=0.1, offloaded=False, outcome=f"→ {i + 1} lines"))
        emit(events.Done("done"))
        history.append({"role": "assistant", "content": "done"})

    monkeypatch.setattr(loop, "run_turn", fake_run_turn)

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt", Input)
        app.set_focus(prompt)
        prompt.value = "go"
        await pilot.press("enter")
        await _wait_for(pilot, lambda: app._turn_worker is None)

        texts = _texts(app.query_one(Transcript))
        blob = "\n".join(texts)
        assert all(f"file{i}.py" in blob for i in range(3))
        assert not any(f"file{i}.py" in blob for i in range(3, 5))
        assert any("+2 more" in t for t in texts)

        app.query_one(Transcript).expand_latest()
        await pilot.pause()

        texts = _texts(app.query_one(Transcript))
        blob = "\n".join(texts)
        assert all(f"file{i}.py" in blob for i in range(5))
        assert "5 lines" in blob
        assert not any("more" in t and "expand" in t for t in texts)


@pytest.mark.asyncio
async def test_sidebar_hidden_by_default_and_toggle_persists(tmp_path):
    app = make_app()
    async with app.run_test() as pilot:
        sidebar = app.query_one(Sidebar)
        assert sidebar.display is False

        await pilot.press("ctrl+b")
        await pilot.pause()
        assert sidebar.display is True
        assert json.loads(prefs.PATH.read_text())["sidebar"] is True

        await pilot.press("ctrl+b")
        await pilot.pause()
        assert sidebar.display is False
        assert json.loads(prefs.PATH.read_text())["sidebar"] is False


@pytest.mark.asyncio
async def test_sidebar_command_toggles_too():
    app = make_app()
    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt", Input)
        app.set_focus(prompt)
        prompt.value = "/sidebar"
        await pilot.press("enter")
        await pilot.pause()
        assert app.query_one(Sidebar).display is True


@pytest.mark.asyncio
async def test_ctrl_number_switches_sidebar_tab():
    from textual.widgets import TabbedContent

    app = make_app()
    async with app.run_test() as pilot:
        await pilot.press("ctrl+2")
        await pilot.pause()
        assert app.query_one(TabbedContent).active == "tab-git"
        await pilot.press("ctrl+1")
        await pilot.pause()
        assert app.query_one(TabbedContent).active == "tab-session"


@pytest.mark.asyncio
async def test_git_tab_renders_repos_from_gitlog(monkeypatch):
    repo = gitlog.Repo(path=Path("/tmp/proj"), name="proj", branch="main", dirty=True)
    commit = gitlog.Commit(sha="abc123def", short_sha="abc123d", author="tim",
                           age="2h", subject="fix the bug")

    async def fake_discover(root, max_depth=2):
        return [repo]

    async def fake_commits(r, limit=20):
        return [commit]

    monkeypatch.setattr(gitlog, "discover_repos_async", fake_discover)
    monkeypatch.setattr(gitlog, "recent_commits_async", fake_commits)

    app = make_app()
    async with app.run_test() as pilot:
        await _wait_for(pilot, lambda: any("proj" in t for t in _texts(app.query_one(GitTab))))
        texts = _texts(app.query_one(GitTab))
        blob = "\n".join(texts)
        assert "proj" in blob and "main" in blob and "dirty" in blob
        assert "abc123d" in blob and "fix the bug" in blob


@pytest.mark.asyncio
async def test_plan_command_flips_mode_and_status_bar():
    app = make_app()
    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt", Input)
        app.set_focus(prompt)
        prompt.value = "/plan"
        await pilot.press("enter")
        await pilot.pause()

        assert app.mode == "plan"
        assert "plan" in str(app.query_one(StatusBar).content)
        assert prompt.has_class("-plan-mode")
        assert prompt.placeholder == "Ask omega… (shift+tab to change mode)"
        assert "plan" in str(app.query_one("#mode-tag", Static).content)


@pytest.mark.asyncio
async def test_unknown_command_suggests_closest_match():
    app = make_app()
    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt", Input)
        app.set_focus(prompt)
        prompt.value = "/mdoel"
        await pilot.press("enter")
        await pilot.pause()

        texts = _texts(app.query_one(Transcript))
        assert any("did you mean /model" in t for t in texts)


@pytest.mark.asyncio
async def test_help_command_prints_cheat_sheet():
    app = make_app()
    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt", Input)
        app.set_focus(prompt)
        prompt.value = "/help"
        await pilot.press("enter")
        await pilot.pause()

        texts = _texts(app.query_one(Transcript))
        assert any("ctrl+c" in t and "cancel turn" in t for t in texts)


@pytest.mark.asyncio
async def test_ask_user_modal_arrow_down_enter_picks_second_option():
    app = make_app()
    result: dict = {}

    async def run_ask():
        result["value"] = await app.ask_user(
            "pick one",
            [{"label": "A", "description": "a"}, {"label": "B", "description": "b"}],
            False)

    async with app.run_test() as pilot:
        app.run_worker(run_ask(), exclusive=False, thread=False)
        await _wait_for(pilot, lambda: isinstance(app.screen, AskUserScreen))
        await pilot.press("down")
        await pilot.press("enter")
        await _wait_for(pilot, lambda: "value" in result)

    assert result["value"] == "B"


@pytest.mark.asyncio
async def test_confirm_modal_n_denies():
    app = make_app()
    result: dict = {}

    async def run_confirm():
        result["value"] = await app.confirm("bash", {"command": "ls"}, "not classified")

    async with app.run_test() as pilot:
        app.run_worker(run_confirm(), exclusive=False, thread=False)
        await _wait_for(pilot, lambda: isinstance(app.screen, ConfirmScreen))
        await pilot.press("n")
        await _wait_for(pilot, lambda: "value" in result)

    assert result["value"] is False


@pytest.mark.asyncio
async def test_confirm_modal_y_allows():
    app = make_app()
    result: dict = {}

    async def run_confirm():
        result["value"] = await app.confirm("bash", {"command": "ls"}, "not classified")

    async with app.run_test() as pilot:
        app.run_worker(run_confirm(), exclusive=False, thread=False)
        await _wait_for(pilot, lambda: isinstance(app.screen, ConfirmScreen))
        await pilot.press("y")
        await _wait_for(pilot, lambda: "value" in result)

    assert result["value"] is True


@pytest.mark.asyncio
async def test_model_command_opens_picker_and_selecting_updates_state():
    app = make_app()
    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt", Input)
        app.set_focus(prompt)
        prompt.value = "/model"
        await pilot.press("enter")
        await _wait_for(pilot, lambda: isinstance(app.screen, ModelPickerScreen))

        # Catalog is sorted alphabetically: haiku, then opus -- move down once.
        await pilot.press("down")
        await pilot.press("enter")
        await _wait_for(pilot, lambda: app.model_alias == "opus")

        assert app.sess.model_override == "opus"
        status_text = str(app.query_one(StatusBar).content)
        assert "opus" in status_text and "claude-opus-5" in status_text

        transcript_texts = _texts(app.query_one(Transcript))
        assert any("model: opus · claude-opus-5" in t for t in transcript_texts)


@pytest.mark.asyncio
async def test_model_switch_clears_stale_usage_limit():
    app = make_app()
    async with app.run_test() as pilot:
        app._usage = events.Usage(prompt_tokens=100, completion_tokens=20,
                                  used=120, limit=200_000)
        app._set_model("opus")
        await pilot.pause()
        assert app._usage is None


@pytest.mark.asyncio
async def test_discuss_command_switches_mode_when_available(monkeypatch):
    monkeypatch.setattr(loop, "MODES", {**loop.MODES, "discuss": ("system", None)})
    app = make_app()
    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt", Input)
        app.set_focus(prompt)
        prompt.value = "/discuss"
        await pilot.press("enter")
        await pilot.pause()

        assert app.mode == "discuss"
        assert prompt.has_class("-discuss-mode")
        assert "discuss" in str(app.query_one("#mode-tag", Static).content)


@pytest.mark.asyncio
async def test_discuss_command_falls_back_when_unavailable(monkeypatch):
    monkeypatch.setattr(loop, "MODES", {"build": ("s", None), "plan": ("s", None)})
    app = make_app()
    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt", Input)
        app.set_focus(prompt)
        prompt.value = "/discuss"
        await pilot.press("enter")
        await pilot.pause()

        assert app.mode == "build"
        texts = _texts(app.query_one(Transcript))
        assert any("discuss mode not available in this build" in t for t in texts)


@pytest.mark.asyncio
async def test_header_bar_shows_wordmark_cwd_and_session_id():
    app = make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        header_text = str(app.query_one(app_module.HeaderBar).content)
        assert "⌘ omega" in header_text
        assert app.sess.id in header_text


@pytest.mark.asyncio
async def test_empty_state_shown_on_fresh_session_and_cleared_on_first_prompt():
    app = make_app()
    async with app.run_test() as pilot:
        texts = _texts(app.query_one(Transcript))
        assert any("ask anything about this repo" in t for t in texts)

        prompt = app.query_one("#prompt", Input)
        app.set_focus(prompt)
        prompt.value = "hello"
        await pilot.press("enter")
        await pilot.pause()

        assert app.query_one(Transcript)._empty_state is None


@pytest.mark.asyncio
async def test_git_tab_renders_working_tree_changes(monkeypatch):
    repo = gitlog.Repo(path=Path("/tmp/proj"), name="proj", branch="main", dirty=True)
    change = gitlog.Change(path="omega/loop.py", status="M", added=12, removed=3)

    async def fake_discover(root, max_depth=2):
        return [repo]

    async def fake_working_tree(r):
        return [change]

    async def fake_commits(r, limit=20):
        return []

    monkeypatch.setattr(gitlog, "discover_repos_async", fake_discover)
    monkeypatch.setattr(gitlog, "working_tree_async", fake_working_tree)
    monkeypatch.setattr(gitlog, "recent_commits_async", fake_commits)

    app = make_app()
    async with app.run_test() as pilot:
        await _wait_for(pilot, lambda: any("omega/loop.py" in t for t in _texts(app.query_one(GitTab))))
        blob = "\n".join(_texts(app.query_one(GitTab)))
        assert "CHANGES" in blob
        assert "omega/loop.py" in blob
        assert "+12" in blob and "−3" in blob
        assert "HISTORY" in blob


@pytest.mark.asyncio
async def test_git_tab_change_row_opens_diff_modal(monkeypatch):
    repo = gitlog.Repo(path=Path("/tmp/proj"), name="proj", branch="main", dirty=True)
    change = gitlog.Change(path="a.py", status="M", added=1, removed=0)

    async def fake_discover(root, max_depth=2):
        return [repo]

    async def fake_working_tree(r):
        return [change]

    async def fake_commits(r, limit=20):
        return []

    async def fake_diff(r, path):
        return "--- a/a.py\n+++ b/a.py\n+new line\n"

    monkeypatch.setattr(gitlog, "discover_repos_async", fake_discover)
    monkeypatch.setattr(gitlog, "working_tree_async", fake_working_tree)
    monkeypatch.setattr(gitlog, "recent_commits_async", fake_commits)
    monkeypatch.setattr(gitlog, "diff_async", fake_diff)

    app = make_app()
    async with app.run_test() as pilot:
        await _wait_for(pilot, lambda: any("a.py" in t for t in _texts(app.query_one(GitTab))))
        row = next(w for w in app.query_one(GitTab).children if hasattr(w, "action_activate")
                   and getattr(w, "path", None) == "a.py")
        row.action_activate()
        await _wait_for(pilot, lambda: isinstance(app.screen, DiffScreen))
        await _wait_for(pilot, lambda: "new line" in "\n".join(_texts(app.screen)))


@pytest.mark.asyncio
async def test_connections_tab_shows_live_status(monkeypatch):
    from omega import mcp

    def fake_status():
        return {
            "linear": mcp.ServerStatus("linear", True, "connected", 23, None, time.time() - 120),
            "notion": mcp.ServerStatus("notion", True, "needs_auth", 0, "https://...", None),
        }

    monkeypatch.setattr(mcp, "status", fake_status)

    app = make_app()
    async with app.run_test() as pilot:
        await _wait_for(pilot, lambda: any("linear" in t for t in _texts(app.query_one(ConnectionsTab))))
        blob = "\n".join(_texts(app.query_one(ConnectionsTab)))
        assert "linear" in blob and "23 tools" in blob and "used" in blob
        assert "notion" in blob and "omega connections connect notion" in blob


@pytest.mark.asyncio
async def test_more_line_is_focusable_and_expands_via_action(monkeypatch):
    app = make_app()

    async def fake_run_turn(cfg, history, mode, emit, model=None):
        emit(events.Phase("waiting"))
        for i in range(5):
            emit(events.ToolStart(call_id=f"c{i}", name="read", args_preview=f"read  file{i}.py"))
        emit(events.Done("done"))
        history.append({"role": "assistant", "content": "done"})

    monkeypatch.setattr(loop, "run_turn", fake_run_turn)

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt", Input)
        app.set_focus(prompt)
        prompt.value = "go"
        await pilot.press("enter")
        await _wait_for(pilot, lambda: app._turn_worker is None)

        more_line = app.query_one(_MoreLine)
        assert more_line.can_focus
        more_line.action_activate()
        await pilot.pause()

        blob = "\n".join(_texts(app.query_one(Transcript)))
        assert "file4.py" in blob


@pytest.mark.asyncio
async def test_cost_command_shows_tokens_and_price_by_model(monkeypatch):
    app = make_app()

    async def fake_run_turn(cfg, history, mode, emit, model=None):
        emit(events.ModelUsed(alias="haiku", model="claude-haiku-4-5", provider="anthropic"))
        emit(events.Usage(prompt_tokens=1000, completion_tokens=200, used=1200, limit=200_000))
        emit(events.Done("ok"))
        history.append({"role": "assistant", "content": "ok"})

    monkeypatch.setattr(loop, "run_turn", fake_run_turn)

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt", Input)
        app.set_focus(prompt)
        prompt.value = "go"
        await pilot.press("enter")
        await _wait_for(pilot, lambda: app._turn_worker is None)

        prompt.value = "/cost"
        await pilot.press("enter")
        await pilot.pause()

        blob = "\n".join(_texts(app.query_one(Transcript)))
        assert "haiku" in blob
        assert "1000 in" in blob and "200 out" in blob
        assert "$0.0020" in blob


@pytest.mark.asyncio
async def test_cost_command_with_no_usage_yet():
    app = make_app()
    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt", Input)
        app.set_focus(prompt)
        prompt.value = "/cost"
        await pilot.press("enter")
        await pilot.pause()
        blob = "\n".join(_texts(app.query_one(Transcript)))
        assert "no usage yet this session" in blob


@pytest.mark.asyncio
async def test_export_command_writes_markdown_and_prints_path(tmp_path):
    app = make_app()
    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt", Input)
        app.set_focus(prompt)
        app.history.append({"role": "user", "content": "hello there"})
        target = tmp_path / "out.md"
        prompt.value = f"/export {target}"
        await pilot.press("enter")
        await pilot.pause()

        assert target.exists()
        assert "hello there" in target.read_text()
        blob = "\n".join(_texts(app.query_one(Transcript)))
        assert str(target) in blob


@pytest.mark.asyncio
async def test_compact_command_triggers_and_shows_the_note(monkeypatch):
    app = make_app()

    async def fake_maybe_compact(cfg, history, used, limit, **kw):
        assert used == limit
        return "compacted 4 messages → summary"

    monkeypatch.setattr(compact, "maybe_compact", fake_maybe_compact)

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt", Input)
        app.set_focus(prompt)
        prompt.value = "/compact"
        await pilot.press("enter")
        await pilot.pause()
        blob = "\n".join(_texts(app.query_one(Transcript)))
        assert "compacted 4 messages" in blob


@pytest.mark.asyncio
async def test_compact_command_reports_nothing_to_compact(monkeypatch):
    app = make_app()

    async def fake_maybe_compact(cfg, history, used, limit, **kw):
        return None

    monkeypatch.setattr(compact, "maybe_compact", fake_maybe_compact)

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt", Input)
        app.set_focus(prompt)
        prompt.value = "/compact"
        await pilot.press("enter")
        await pilot.pause()
        blob = "\n".join(_texts(app.query_one(Transcript)))
        assert "nothing to compact" in blob


@pytest.mark.asyncio
async def test_undo_confirms_then_calls_checkpoint_undo(monkeypatch):
    from omega import checkpoint

    calls = []

    def fake_undo(session_id, steps, cwd=None):
        calls.append((session_id, steps, cwd))
        return "reverted the last 1 turn"

    monkeypatch.setattr(checkpoint, "undo", fake_undo)

    app = make_app()
    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt", Input)
        app.set_focus(prompt)
        prompt.value = "/undo"
        await pilot.press("enter")
        await _wait_for(pilot, lambda: isinstance(app.screen, ConfirmScreen))
        await pilot.press("y")
        await _wait_for(pilot, lambda: bool(calls))

        blob = "\n".join(_texts(app.query_one(Transcript)))
        assert "reverted the last 1 turn" in blob
        assert calls[0][1] == 1


@pytest.mark.asyncio
async def test_undo_with_n_steps_is_parsed_and_cancel_skips_checkpoint(monkeypatch):
    from omega import checkpoint

    calls = []
    monkeypatch.setattr(checkpoint, "undo", lambda *a, **k: calls.append(a) or "x")

    app = make_app()
    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt", Input)
        app.set_focus(prompt)
        prompt.value = "/undo 3"
        await pilot.press("enter")
        await _wait_for(pilot, lambda: isinstance(app.screen, ConfirmScreen))
        # The confirm dialog itself names the step count -- check it while
        # still open, since its content is gone once dismissed.
        assert "3" in "\n".join(_texts(app.screen))
        await pilot.press("n")
        await pilot.pause()

        blob = "\n".join(_texts(app.query_one(Transcript)))
        assert "undo cancelled" in blob
        assert not calls


def _block_relative_import(name: str):
    """`sys.modules[...] = None` doesn't work here: once `omega.checkpoint`
    has been imported anywhere in the process, `from ... import checkpoint`
    resolves via the already-set `checkpoint` attribute on the `omega`
    package object and never re-consults `sys.modules` at all. Intercepting
    `__import__` itself is the only way to make the relative import
    (`level=3`, `fromlist=(name,)`) genuinely fail."""
    import builtins
    real_import = builtins.__import__

    def fake_import(modname, globals=None, locals=None, fromlist=(), level=0):
        if level == 3 and fromlist and name in fromlist:
            raise ImportError(f"blocked for this test: {name}")
        return real_import(modname, globals, locals, fromlist, level)
    return fake_import


@pytest.mark.asyncio
async def test_undo_not_available_when_checkpoint_module_is_missing(monkeypatch):
    monkeypatch.setattr("builtins.__import__", _block_relative_import("checkpoint"))
    app = make_app()
    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt", Input)
        app.set_focus(prompt)
        prompt.value = "/undo"
        await pilot.press("enter")
        await pilot.pause()
        blob = "\n".join(_texts(app.query_one(Transcript)))
        assert "not available in this build" in blob


@pytest.mark.asyncio
async def test_verify_not_available_when_verify_module_is_missing(monkeypatch):
    monkeypatch.setattr("builtins.__import__", _block_relative_import("verify"))
    app = make_app()
    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt", Input)
        app.set_focus(prompt)
        prompt.value = "/verify"
        await pilot.press("enter")
        await pilot.pause()
        blob = "\n".join(_texts(app.query_one(Transcript)))
        assert "not available in this build" in blob


@pytest.mark.asyncio
async def test_diff_command_opens_diffscreen_with_checkpoint_diff(monkeypatch):
    from omega import checkpoint

    monkeypatch.setattr(checkpoint, "diff", lambda session_id, cwd=None: "--- a/x\n+++ b/x\n+hi there\n")

    app = make_app()
    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt", Input)
        app.set_focus(prompt)
        prompt.value = "/diff"
        await pilot.press("enter")
        await _wait_for(pilot, lambda: isinstance(app.screen, DiffScreen))
        await _wait_for(pilot, lambda: "hi there" in "\n".join(_texts(app.screen)))


@pytest.mark.asyncio
async def test_verify_command_shows_verified_summary(monkeypatch):
    from omega import verify

    check = verify.Check(name="pytest", command="pytest -q", kind="test")
    result = verify.Result(check=check, ok=True, exit_code=0, tail="")
    monkeypatch.setattr(verify, "detect", lambda cwd: [check])
    monkeypatch.setattr(verify, "run", lambda checks, cwd, timeout=300: [result])

    app = make_app()
    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt", Input)
        app.set_focus(prompt)
        prompt.value = "/verify"
        await pilot.press("enter")
        await pilot.pause()
        blob = "\n".join(_texts(app.query_one(Transcript)))
        assert "✓ verified: pytest ✓" in blob


@pytest.mark.asyncio
async def test_verify_command_no_checks_detected(monkeypatch):
    from omega import verify

    monkeypatch.setattr(verify, "detect", lambda cwd: [])

    app = make_app()
    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt", Input)
        app.set_focus(prompt)
        prompt.value = "/verify"
        await pilot.press("enter")
        await pilot.pause()
        blob = "\n".join(_texts(app.query_one(Transcript)))
        assert "no checks detected" in blob


@pytest.mark.asyncio
async def test_sessions_command_lists_and_resumes(tmp_path):
    app = make_app()
    other = session.Session.new(cwd=app.sess.cwd)
    other.history = [{"role": "user", "content": "earlier task"}]
    other.save()

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt", Input)
        app.set_focus(prompt)
        prompt.value = "/sessions"
        await pilot.press("enter")
        await _wait_for(pilot, lambda: isinstance(app.screen, SessionsScreen))
        await pilot.press("enter")
        await _wait_for(pilot, lambda: app.sess.id == other.id)

        assert app.history[-1] == {"role": "user", "content": "earlier task"}
        blob = "\n".join(_texts(app.query_one(Transcript)))
        assert "resumed" in blob


@pytest.mark.asyncio
async def test_sessions_command_reports_when_none_for_this_directory():
    app = make_app()
    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt", Input)
        app.set_focus(prompt)
        prompt.value = "/sessions"
        await pilot.press("enter")
        await pilot.pause()
        blob = "\n".join(_texts(app.query_one(Transcript)))
        assert "no other sessions" in blob


@pytest.mark.asyncio
async def test_emit_renders_checkpoint_verified_job_events(monkeypatch):
    app = make_app()

    async def fake_run_turn(cfg, history, mode, emit, model=None):
        emit(events.Checkpoint(turn=1, id="cp1"))
        emit(events.Verified(results_summary="pytest ok", ok=True))
        emit(events.Verified(results_summary="pytest failed", ok=False))
        emit(events.JobStarted(id="j1", command="sleep 1"))
        emit(events.JobFinished(id="j1", exit_code=0))
        emit(events.Done("done"))
        history.append({"role": "assistant", "content": "done"})

    monkeypatch.setattr(loop, "run_turn", fake_run_turn)

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt", Input)
        app.set_focus(prompt)
        prompt.value = "go"
        await pilot.press("enter")
        await _wait_for(pilot, lambda: app._turn_worker is None)

        blob = "\n".join(_texts(app.query_one(Transcript)))
        assert "checkpoint" in blob
        assert "verified: pytest ok" in blob
        assert "verification failed: pytest failed" in blob
        assert "job j1 started" in blob
        assert "job j1 finished (exit 0)" in blob


@pytest.mark.asyncio
async def test_emit_survives_a_render_error_and_shows_a_marker(monkeypatch):
    app = make_app()
    monkeypatch.setattr(Transcript, "add_text_delta",
                        lambda self, text: (_ for _ in ()).throw(RuntimeError("boom")))

    async def fake_run_turn(cfg, history, mode, emit, model=None):
        emit(events.TextDelta("hi"))
        emit(events.Done("hi"))
        history.append({"role": "assistant", "content": "hi"})

    monkeypatch.setattr(loop, "run_turn", fake_run_turn)

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt", Input)
        app.set_focus(prompt)
        prompt.value = "go"
        await pilot.press("enter")
        await _wait_for(pilot, lambda: app._turn_worker is None)

        blob = "\n".join(_texts(app.query_one(Transcript)))
        assert "render error" in blob
        assert "RuntimeError" in blob
