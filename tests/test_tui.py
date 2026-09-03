import asyncio
import json
from pathlib import Path

import pytest
from rich.markdown import Markdown
from textual.widgets import Input, Static

from rig import artifacts, events, gitlog, loop, session
from rig.config import Model
from rig.ui import tui
from rig.ui.tui import prefs
from rig.ui.tui.modals import AskUserScreen, ConfirmScreen, ModelPickerScreen
from rig.ui.tui.sidebar import GitTab, Sidebar
from rig.ui.tui.status import StatusBar
from rig.ui.tui.transcript import Transcript


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
        raise SystemExit(f"rig: unknown model {text!r}; have {sorted(self.models)}")


async def _fake_discover_repos(root, max_depth=2):
    return []


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "DIR", tmp_path / "sessions")
    monkeypatch.setattr(tui, "HISTORY", tmp_path / "history")
    monkeypatch.setattr(artifacts, "DIR", tmp_path / "sessions")
    monkeypatch.setattr(prefs, "PATH", tmp_path / "ui.json")
    # The Git tab discovers real repos on mount in a worker; keep that fast
    # and hermetic by default, individual tests override it as needed.
    monkeypatch.setattr(gitlog, "discover_repos_async", _fake_discover_repos)
    yield


def make_app() -> tui.RigApp:
    sess = session.Session.new(cwd=str(Path.cwd()))
    return tui.RigApp(FakeCfg(), sess, "build", sess.history)


def _texts(widget) -> list[str]:
    out = []
    for s in widget.query(Static):
        content = s.content
        out.append(content.markup if isinstance(content, Markdown) else str(content))
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
        # The offload is folded onto the tool's own line, not a second line.
        assert any("4.2k chars" in t and "deadbeef" in t for t in texts)
        assert not any(t.strip().startswith("↳") for t in texts)

        sidebar = app.query_one(Sidebar)
        assert sidebar.session_tab._tool_session["bash"] == 1

        status_text = str(app.query_one(StatusBar).content)
        assert "1.2k" in status_text
        assert "●" in status_text
        assert app._phase == "idle"

        assert app.history[0] == {"role": "user", "content": "hello"}
        assert app.history[-1] == {"role": "assistant", "content": "Hello world"}
        assert close_calls == [("build", False)]


@pytest.mark.asyncio
async def test_phase_indicator_shows_thinking_then_settles_idle(monkeypatch):
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

        status_text = str(app.query_one(StatusBar).content)
        assert "thinking" in status_text
        thinking_texts = _texts(app.query_one(Transcript))
        assert any("thinking" in t for t in thinking_texts)

        resume.set()
        await _wait_for(pilot, lambda: app._turn_worker is None)

        status_text = str(app.query_one(StatusBar).content)
        assert app._phase == "idle"
        assert "●" in status_text
        final_texts = _texts(app.query_one(Transcript))
        assert not any("thinking…" in t for t in final_texts)


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
        assert "→ 5 lines" in blob
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
        assert prompt.placeholder == "plan› "


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
