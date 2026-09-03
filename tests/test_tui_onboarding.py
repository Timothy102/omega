import json

import pytest
from textual.widgets import Input

from omega import config, events, loop, onboarding
from omega.ui.tui.onboarding import (
    DoneScreen,
    KeyScreen,
    ModelScreen,
    OnboardingApp,
    ProveScreen,
    ProviderScreen,
    WelcomeScreen,
)


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    yield


async def _wait_for(pilot, predicate, attempts=40):
    for _ in range(attempts):
        if predicate():
            return
        await pilot.pause()
    raise AssertionError("condition never became true")


def _fake_run_agent_factory():
    async def fake_run_agent(cfg, role_name, system, history, *args, **kwargs):
        emit = kwargs.get("emit")
        if emit:
            emit(events.ToolStart(call_id="c1", name="bash", args_preview="bash  $ pwd"))
            emit(events.ToolEnd(call_id="c1", name="bash", result_preview="ok",
                                duration_s=0.1, offloaded=False, outcome="→ 0.1s"))
            emit(events.TextDelta("here we are"))
            emit(events.Done("here we are"))
        return "here we are"
    return fake_run_agent


@pytest.mark.asyncio
async def test_full_wizard_flow_writes_config_and_returns_true(monkeypatch):
    async def fake_validate_key(choice, key):
        return True, "connected"

    monkeypatch.setattr(onboarding, "validate_key", fake_validate_key)
    monkeypatch.setattr(loop, "run_agent", _fake_run_agent_factory())

    app = OnboardingApp()
    async with app.run_test() as pilot:
        await _wait_for(pilot, lambda: isinstance(app.screen, WelcomeScreen))
        await pilot.press("enter")
        await _wait_for(pilot, lambda: isinstance(app.screen, ProviderScreen))

        # No env keys are set, so Anthropic (row 0) is preselected; arrow down
        # to OpenRouter (row 1) to exercise real navigation.
        await pilot.press("down")
        await pilot.press("enter")
        await _wait_for(pilot, lambda: isinstance(app.screen, KeyScreen))

        key_input = app.screen.query_one("#key-input", Input)
        key_input.value = "or-test-key"
        await pilot.press("enter")
        await _wait_for(pilot, lambda: isinstance(app.screen, ModelScreen))

        # spark is the recommended OpenRouter default -- accept it directly.
        await pilot.press("enter")
        await _wait_for(pilot, lambda: isinstance(app.screen, ProveScreen))

        # ProveScreen holds the success line on screen briefly before
        # auto-advancing -- give that real delay time to elapse.
        await pilot.pause(0.8)
        await _wait_for(pilot, lambda: isinstance(app.screen, DoneScreen))
        await pilot.press("enter")
        await pilot.pause()

    assert app.return_value is True

    data = json.loads(config.CONFIG_PATH.read_text())
    assert data["providers"]["openrouter"]["baseUrl"] == "https://openrouter.ai/api/v1"
    assert data["providers"]["openrouter"]["apiKey"] == "or-test-key"
    assert data["models"]["spark"]["model"] == "meta/muse-spark-1.3"
    assert data["roles"]["main"] == {"alias": "spark"}
    assert data["roles"]["plan"] == {"alias": "spark"}
    assert data["roles"]["subagent_mid"] == {"alias": "spark"}
    assert data["roles"]["subagent_fast"] == {"alias": "glm"}
    assert data["roles"]["compact"] == {"alias": "glm"}
    assert data["roles"]["memory"] == {"alias": "glm"}


@pytest.mark.asyncio
async def test_key_validation_failure_then_retry_succeeds(monkeypatch):
    outcomes = iter([(False, "unauthorized: bad key"), (True, "connected")])

    async def fake_validate_key(choice, key):
        return next(outcomes)

    monkeypatch.setattr(onboarding, "validate_key", fake_validate_key)

    app = OnboardingApp()
    async with app.run_test() as pilot:
        await _wait_for(pilot, lambda: isinstance(app.screen, WelcomeScreen))
        await pilot.press("enter")
        await _wait_for(pilot, lambda: isinstance(app.screen, ProviderScreen))
        await pilot.press("enter")  # Anthropic, preselected
        await _wait_for(pilot, lambda: isinstance(app.screen, KeyScreen))

        key_input = app.screen.query_one("#key-input", Input)
        key_input.value = "bad-key"
        await pilot.press("enter")

        def _shows_error() -> bool:
            statics = app.screen.query("Static")
            return any("unauthorized" in str(s.content) for s in statics)

        await _wait_for(pilot, _shows_error)
        assert isinstance(app.screen, KeyScreen)

        key_input = app.screen.query_one("#key-input", Input)
        key_input.value = "good-key"
        await pilot.press("enter")
        await _wait_for(pilot, lambda: isinstance(app.screen, ModelScreen))


@pytest.mark.asyncio
async def test_welcome_quit_returns_false(monkeypatch):
    app = OnboardingApp()
    async with app.run_test() as pilot:
        await _wait_for(pilot, lambda: isinstance(app.screen, WelcomeScreen))
        await pilot.press("q")
        await pilot.pause()

    assert app.return_value is False
    assert not config.CONFIG_PATH.exists()


@pytest.mark.asyncio
async def test_prove_step_failure_offers_retry_and_continue(monkeypatch):
    async def fake_validate_key(choice, key):
        return True, "connected"

    async def failing_run_agent(*args, **kwargs):
        raise RuntimeError("network unreachable")

    monkeypatch.setattr(onboarding, "validate_key", fake_validate_key)
    monkeypatch.setattr(loop, "run_agent", failing_run_agent)

    app = OnboardingApp()
    async with app.run_test() as pilot:
        await _wait_for(pilot, lambda: isinstance(app.screen, WelcomeScreen))
        await pilot.press("enter")
        await _wait_for(pilot, lambda: isinstance(app.screen, ProviderScreen))
        await pilot.press("enter")
        await _wait_for(pilot, lambda: isinstance(app.screen, KeyScreen))
        app.screen.query_one("#key-input", Input).value = "sk-ant-test"
        await pilot.press("enter")
        await _wait_for(pilot, lambda: isinstance(app.screen, ModelScreen))
        await pilot.press("enter")
        await _wait_for(pilot, lambda: isinstance(app.screen, ProveScreen))

        def _shows_retry_hint() -> bool:
            statics = app.screen.query("Static")
            return any("press r to retry" in str(s.content) for s in statics)

        await _wait_for(pilot, _shows_retry_hint)
        # config is saved even though the probe turn failed
        assert config.CONFIG_PATH.exists()

        await pilot.press("enter")  # continue anyway
        await _wait_for(pilot, lambda: isinstance(app.screen, DoneScreen))
