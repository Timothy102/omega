import json

import pytest

from rig import config


def write_config(tmp_path, data):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data))
    return path


BASE = {
    "providers": {
        "openai-like": {"baseUrl": "https://api.example.com/v1", "apiKeyEnv": "EXAMPLE_KEY"},
        "anthropic": {"type": "anthropic", "apiKeyEnv": "ANTHROPIC_KEY_UNSET"},
    },
    "models": {
        "big": {"model": "claude-opus-5", "provider": "anthropic", "context": 1000000, "effort": "high"},
        "small": {"model": "small-model", "provider": "openai-like", "context": 128000},
    },
    "roles": {
        "main": {"alias": "big"},
        "plan": {"model": "small-model", "provider": "openai-like", "context": 128000},
    },
}


@pytest.fixture(autouse=True)
def isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.delenv("ANTHROPIC_KEY_UNSET", raising=False)
    monkeypatch.setenv("EXAMPLE_KEY", "example-key-value")
    yield


def test_alias_role_resolves_to_catalog_entry(tmp_path, monkeypatch):
    write_config(tmp_path, BASE)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    cfg = config.load()

    role = cfg.role("main")
    assert role.model == "claude-opus-5"
    assert role.alias == "big"
    assert role.effort == "high"
    assert role.provider.type == "anthropic"


def test_inline_role_has_no_alias(tmp_path, monkeypatch):
    write_config(tmp_path, BASE)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    cfg = config.load()

    role = cfg.role("plan")
    assert role.model == "small-model"
    assert role.alias is None


def test_config_model_builds_role_from_catalog(tmp_path, monkeypatch):
    write_config(tmp_path, BASE)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    cfg = config.load()

    role = cfg.model("small")
    assert role.model == "small-model" and role.alias == "small"
    assert role.provider.name == "openai-like"


def test_unknown_alias_raises_keyerror(tmp_path, monkeypatch):
    write_config(tmp_path, BASE)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    cfg = config.load()
    with pytest.raises(KeyError):
        cfg.model("nonexistent")


def test_provider_without_key_loads_fine(tmp_path, monkeypatch):
    """A provider missing its key must not block config.load() -- only using
    it (accessing .api_key) should fail, and with the existing helpful message."""
    write_config(tmp_path, BASE)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    cfg = config.load()  # must not raise

    role = cfg.role("main")  # anthropic provider, no key set
    with pytest.raises(SystemExit, match="no API key for provider 'anthropic'"):
        _ = role.provider.api_key


def test_provider_with_key_resolves(tmp_path, monkeypatch):
    write_config(tmp_path, BASE)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    cfg = config.load()

    role = cfg.role("plan")
    assert role.provider.api_key == "example-key-value"


def test_resolve_alias_matches_catalog_alias(tmp_path, monkeypatch):
    write_config(tmp_path, BASE)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    cfg = config.load()
    assert cfg.resolve_alias("big") == "big"


def test_resolve_alias_matches_bare_model_id(tmp_path, monkeypatch):
    write_config(tmp_path, BASE)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    cfg = config.load()
    assert cfg.resolve_alias("claude-opus-5") == "big"


def test_resolve_alias_unknown_raises_systemexit_listing_catalog(tmp_path, monkeypatch):
    write_config(tmp_path, BASE)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    cfg = config.load()
    with pytest.raises(SystemExit, match="unknown model"):
        cfg.resolve_alias("does-not-exist")


def test_anthropic_provider_without_base_url_loads_and_uses_sdk_default(tmp_path, monkeypatch):
    data = {**BASE, "providers": {**BASE["providers"],
                                  "anthropic": {"type": "anthropic", "apiKeyEnv": "ANTHROPIC_KEY_UNSET"}}}
    write_config(tmp_path, data)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    cfg = config.load()  # must not raise KeyError
    assert cfg.providers["anthropic"].base_url == ""


def test_anthropic_provider_with_base_url_strips_only_trailing_slash(tmp_path, monkeypatch):
    data = {**BASE, "providers": {**BASE["providers"], "anthropic": {
        "type": "anthropic", "apiKeyEnv": "ANTHROPIC_KEY_UNSET",
        "baseUrl": "https://api.anthropic.com"}}}
    write_config(tmp_path, data)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    cfg = config.load()
    assert cfg.providers["anthropic"].base_url == "https://api.anthropic.com"

    data["providers"]["anthropic"]["baseUrl"] = "https://api.anthropic.com/"
    write_config(tmp_path, data)
    cfg = config.load()
    assert cfg.providers["anthropic"].base_url == "https://api.anthropic.com"


def test_openai_provider_missing_base_url_raises_clear_systemexit(tmp_path, monkeypatch):
    data = {**BASE, "providers": {**BASE["providers"], "broken": {"apiKeyEnv": "BROKEN_KEY"}}}
    write_config(tmp_path, data)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    with pytest.raises(SystemExit, match="'broken'.*missing \"baseUrl\""):
        config.load()


def test_defaults_load_without_a_config_file(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "no-such-config.json")
    monkeypatch.setenv("INFERENCE_API_KEY", "inference-key")
    cfg = config.load()

    assert cfg.role("main").model == "claude-opus-5"
    assert cfg.role("main").alias == "opus"
    assert "fable" in cfg.models and "opus" in cfg.models
    assert cfg.providers["anthropic"].type == "anthropic"
