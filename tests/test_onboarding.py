import json

import pytest

from rig import config, loop, onboarding


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    yield


def queue_input(monkeypatch, answers):
    it = iter(answers)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(it))


def queue_getpass(monkeypatch, key):
    monkeypatch.setattr("getpass.getpass", lambda prompt="": key)


async def fake_run_agent(*a, **kw):
    return "probe ok"


@pytest.mark.asyncio
async def test_onboard_anthropic_writes_working_config(monkeypatch):
    queue_input(monkeypatch, ["1", ""])  # provider: Anthropic; model: default (opus)
    queue_getpass(monkeypatch, "sk-ant-test")
    monkeypatch.setattr(loop, "run_agent", fake_run_agent)

    await onboarding.run()

    data = json.loads(config.CONFIG_PATH.read_text())
    assert data["providers"]["anthropic"] == {"type": "anthropic", "apiKey": "sk-ant-test"}
    assert data["models"]["opus"]["model"] == "claude-opus-5"
    assert data["roles"]["main"] == {"alias": "opus"}
    assert data["roles"]["plan"] == {"alias": "opus"}
    assert data["roles"]["subagent_mid"] == {"alias": "opus"}
    assert data["roles"]["subagent_fast"] == {"alias": "haiku"}
    assert data["roles"]["compact"] == {"alias": "haiku"}
    assert data["roles"]["memory"] == {"alias": "haiku"}

    cfg = config.load()
    assert cfg.role("main").model == "claude-opus-5"
    assert cfg.role("main").provider.has_key


@pytest.mark.asyncio
async def test_onboard_openrouter_writes_working_config(monkeypatch):
    queue_input(monkeypatch, ["2", ""])  # provider: OpenRouter; model: default (kimi)
    queue_getpass(monkeypatch, "or-test-key")
    monkeypatch.setattr(loop, "run_agent", fake_run_agent)

    await onboarding.run()

    data = json.loads(config.CONFIG_PATH.read_text())
    assert data["providers"]["openrouter"]["baseUrl"] == "https://openrouter.ai/api/v1"
    assert data["models"]["kimi"]["model"] == "moonshotai/kimi-k3"
    assert data["roles"]["main"] == {"alias": "kimi"}
    assert data["roles"]["subagent_fast"] == {"alias": "glm"}

    cfg = config.load()
    assert cfg.role("main").model == "moonshotai/kimi-k3"


@pytest.mark.asyncio
async def test_onboard_other_openai_compatible_asks_for_url_and_model(monkeypatch):
    queue_input(monkeypatch, ["3", "https://api.example.com/v1", "big-model-v1"])
    queue_getpass(monkeypatch, "example-key")
    monkeypatch.setattr(loop, "run_agent", fake_run_agent)

    await onboarding.run()

    data = json.loads(config.CONFIG_PATH.read_text())
    assert data["providers"]["custom"]["baseUrl"] == "https://api.example.com/v1"
    assert data["models"]["main-model"]["model"] == "big-model-v1"
    assert data["roles"]["main"] == {"alias": "main-model"}
    assert data["roles"]["subagent_fast"] == {"alias": "main-model"}

    cfg = config.load()
    assert cfg.role("main").model == "big-model-v1"


@pytest.mark.asyncio
async def test_onboard_merges_into_existing_config(monkeypatch, tmp_path):
    existing = {"providers": {"inference-net": {"baseUrl": "https://api.inference.net/v1",
                                                 "apiKeyEnv": "INFERENCE_API_KEY"}},
               "models": {"glm": {"model": "z-ai/glm-5.3-flash", "provider": "inference-net",
                                  "context": 128000}},
               "roles": {"main": {"alias": "glm"}},
               "mcp": {"linear": {"command": "mcp-linear"}}}
    config.CONFIG_PATH.write_text(json.dumps(existing))

    queue_input(monkeypatch, ["1", ""])
    queue_getpass(monkeypatch, "sk-ant-test")
    monkeypatch.setattr(loop, "run_agent", fake_run_agent)

    await onboarding.run()

    data = json.loads(config.CONFIG_PATH.read_text())
    assert data["providers"]["inference-net"]["baseUrl"] == "https://api.inference.net/v1"
    assert data["models"]["glm"]["model"] == "z-ai/glm-5.3-flash"
    assert data["mcp"] == {"linear": {"command": "mcp-linear"}}
    # onboarding's own choice overwrites the six roles it manages...
    assert data["roles"]["main"] == {"alias": "opus"}
    # ...but leaves everything else in the file untouched.
    assert data["providers"]["anthropic"]["type"] == "anthropic"


@pytest.mark.asyncio
async def test_onboard_survives_probe_failure(monkeypatch):
    queue_input(monkeypatch, ["1", ""])
    queue_getpass(monkeypatch, "sk-ant-test")

    async def failing_run_agent(*a, **kw):
        raise RuntimeError("network unreachable")

    monkeypatch.setattr(loop, "run_agent", failing_run_agent)

    await onboarding.run()  # must not raise

    assert config.CONFIG_PATH.exists()
