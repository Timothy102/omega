import json

import pytest

from omega import config, keys, secrets


@pytest.fixture(autouse=True)
def _clear_cache():
    secrets.clear_cache()
    yield
    secrets.clear_cache()


@pytest.fixture
def cfg_file(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setattr(config, "CONFIG_PATH", path)
    return path


def write(path, providers):
    path.write_text(json.dumps({"providers": providers,
                                "roles": {"main": {"model": "m", "provider": "p",
                                                   "context": 1000}}}))


# ---- resolution order ---------------------------------------------------------

def test_a_literal_key_still_works(monkeypatch):
    monkeypatch.setattr(secrets, "keychain_get", lambda name: None)
    p = config.Provider(name="p", api_key_literal="sk-literal")
    assert p.api_key == "sk-literal"
    assert p.key_source == "config.json (plaintext)"


def test_env_is_used_when_there_is_no_literal(monkeypatch):
    monkeypatch.setattr(secrets, "keychain_get", lambda name: None)
    monkeypatch.setenv("MY_KEY", "sk-from-env")
    p = config.Provider(name="p", api_key_env="MY_KEY")
    assert p.api_key == "sk-from-env"
    assert p.key_source == "env: MY_KEY"


def test_a_command_supplies_the_key(monkeypatch):
    monkeypatch.setattr(secrets, "keychain_get", lambda name: None)
    p = config.Provider(name="p", api_key_cmd="printf sk-from-cmd")
    assert p.api_key == "sk-from-cmd"


def test_the_keychain_is_the_last_resort(monkeypatch):
    monkeypatch.setattr(secrets, "keychain_get", lambda name: "sk-from-keychain")
    p = config.Provider(name="p")
    assert p.api_key == "sk-from-keychain"
    assert p.key_source == "keychain"


def test_an_explicit_source_beats_the_keychain(monkeypatch):
    # `migrate` removes the literal precisely so the keychain can take over;
    # while both exist the written-down one must win, or migrating would
    # silently change which key is in use.
    monkeypatch.setattr(secrets, "keychain_get", lambda name: "sk-from-keychain")
    assert config.Provider(name="p", api_key_literal="sk-literal").api_key == "sk-literal"


def test_a_missing_key_names_the_command_that_sets_one(monkeypatch):
    monkeypatch.setattr(secrets, "keychain_get", lambda name: None)
    with pytest.raises(SystemExit) as exc:
        _ = config.Provider(name="openrouter").api_key
    assert "omega keys set openrouter" in str(exc.value)


def test_a_broken_command_fails_loudly_but_does_not_break_probing(monkeypatch):
    monkeypatch.setattr(secrets, "keychain_get", lambda name: None)
    p = config.Provider(name="p", api_key_cmd="exit 3")
    assert p.has_key is False
    with pytest.raises(SystemExit) as exc:
        _ = p.api_key
    assert "exit 3" in str(exc.value)


def test_a_command_that_prints_nothing_is_an_error(monkeypatch):
    monkeypatch.setattr(secrets, "keychain_get", lambda name: None)
    with pytest.raises(SystemExit):
        _ = config.Provider(name="p", api_key_cmd="true").api_key


# ---- redaction ----------------------------------------------------------------

def test_redact_masks_provider_keys_but_keeps_them_distinguishable():
    a = secrets.redact("sk-ant-api03-" + "a" * 40)
    b = secrets.redact("sk-ant-api03-" + "b" * 40)
    assert "a" * 40 not in a and "b" * 40 not in b
    assert a != b


def test_redact_leaves_ordinary_text_alone():
    assert secrets.redact("no secrets here, just prose") == "no secrets here, just prose"


def test_looks_like_key_recognises_the_common_shapes():
    assert secrets.looks_like_key("sk-or-v1-" + "0" * 40)
    assert not secrets.looks_like_key("hunter2")


# ---- migration -----------------------------------------------------------------

def test_migrate_moves_the_key_and_leaves_the_rest_of_the_config(cfg_file, monkeypatch, capsys):
    store: dict[str, str] = {}
    monkeypatch.setattr(secrets, "keychain_available", lambda: True)
    monkeypatch.setattr(secrets, "keychain_set", lambda n, s: store.__setitem__(n, s))
    monkeypatch.setattr(secrets, "keychain_get", store.get)
    write(cfg_file, {"p": {"baseUrl": "https://x.test/v1", "apiKey": "sk-secret-value"}})

    assert keys.main(["migrate"]) == 0

    saved = json.loads(cfg_file.read_text())
    assert "apiKey" not in saved["providers"]["p"]
    assert saved["providers"]["p"]["baseUrl"] == "https://x.test/v1"
    assert saved["roles"]["main"]["model"] == "m"
    assert store == {"p": "sk-secret-value"}
    assert "sk-secret-value" not in capsys.readouterr().out


def test_migrate_dry_run_writes_nothing(cfg_file, monkeypatch):
    monkeypatch.setattr(secrets, "keychain_available", lambda: True)
    monkeypatch.setattr(secrets, "keychain_set", lambda n, s: pytest.fail("wrote"))
    write(cfg_file, {"p": {"baseUrl": "b", "apiKey": "sk-x"}})
    before = cfg_file.read_text()
    assert keys.main(["migrate", "--dry-run"]) == 0
    assert cfg_file.read_text() == before


def test_migrate_keeps_the_config_when_the_keychain_does_not_read_back(cfg_file, monkeypatch):
    # The failure that would otherwise destroy the only copy of a key.
    monkeypatch.setattr(secrets, "keychain_available", lambda: True)
    monkeypatch.setattr(secrets, "keychain_set", lambda n, s: None)
    monkeypatch.setattr(secrets, "keychain_get", lambda n: None)
    write(cfg_file, {"p": {"baseUrl": "b", "apiKey": "sk-x"}})

    assert keys.main(["migrate"]) == 1
    assert json.loads(cfg_file.read_text())["providers"]["p"]["apiKey"] == "sk-x"


def test_migrate_backs_the_old_config_up_at_0600(cfg_file, monkeypatch):
    store: dict[str, str] = {}
    monkeypatch.setattr(secrets, "keychain_available", lambda: True)
    monkeypatch.setattr(secrets, "keychain_set", lambda n, s: store.__setitem__(n, s))
    monkeypatch.setattr(secrets, "keychain_get", store.get)
    write(cfg_file, {"p": {"baseUrl": "b", "apiKey": "sk-x"}})

    keys.main(["migrate"])
    backup = cfg_file.with_suffix(cfg_file.suffix + ".pre-migrate")
    assert "sk-x" in backup.read_text()
    assert backup.stat().st_mode & 0o777 == 0o600
    assert cfg_file.stat().st_mode & 0o777 == 0o600


def test_migrate_is_a_no_op_when_nothing_is_in_plaintext(cfg_file, monkeypatch):
    monkeypatch.setattr(secrets, "keychain_available", lambda: True)
    write(cfg_file, {"p": {"baseUrl": "b", "apiKeyEnv": "SOME_KEY"}})
    before = cfg_file.read_text()
    assert keys.main(["migrate"]) == 0
    assert cfg_file.read_text() == before


def test_listing_never_prints_a_key(cfg_file, monkeypatch, capsys):
    monkeypatch.setattr(secrets, "keychain_get", lambda n: None)
    write(cfg_file, {"p": {"baseUrl": "b", "apiKey": "sk-super-secret-value"}})
    keys.main([])
    out = capsys.readouterr().out
    assert "sk-super-secret-value" not in out
    assert "plaintext" in out
