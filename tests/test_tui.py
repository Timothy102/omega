from pathlib import Path

import pytest
from rich.markdown import Markdown
from textual.widgets import Input, Static

from rig import artifacts, events, loop, session
from rig.config import Model
from rig.ui import tui
from rig.ui.tui.activity import ActivityPanel
from rig.ui.tui.modals import AskUserScreen, ConfirmScreen, ModelPickerScreen
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


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "DIR", tmp_path / "sessions")
    monkeypatch.setattr(tui, "HISTORY", tmp_path / "history")
    monkeypatch.setattr(artifacts, "DIR", tmp_path / "sessions")
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
        emit(events.TextDelta("Hello "))
        emit(events.TextDelta("world"))
        emit(events.ToolStart(call_id="c1", name="bash", args_preview="ls"))
        emit(events.ToolEnd(call_id="c1", name="bash", result_preview="ok",
                            duration_s=0.1, offloaded=True, artifact_id="deadbeef"))
        emit(events.Usage(prompt_tokens=1000, completion_tokens=200, used=1200, limit=1_000_000))
        emit(events.Done("Hello world"))
        history.append({"role": "assistant", "content": "Hello world"})

    monkeypatch.setattr(loop, "run_turn", fake_run_turn)

    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt", Input)
        app.set_focus(prompt)
        prompt.value = "hello"
        await pilot.press("enter")
        await _wait_for(pilot, lambda: app._turn_worker is None)

        texts = _texts(app.query_one(Transcript))
        assert any("Hello world" in t for t in texts)
        assert any("offloaded" in t and "deadbeef" in t for t in texts)

        activity = app.query_one(ActivityPanel)
        assert activity._rows == {}

        status_text = str(app.query_one(StatusBar).content)
        assert "1.2k" in status_text

        assert app.history[0] == {"role": "user", "content": "hello"}
        assert app.history[-1] == {"role": "assistant", "content": "Hello world"}
        assert close_calls == [("build", False)]


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
