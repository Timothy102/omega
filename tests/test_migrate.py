import stat

from omega import migrate


def _write(path, data="{}", mode=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data)
    if mode is not None:
        path.chmod(mode)
    return path


def test_migrate_home_copies_rig_to_omega(tmp_path):
    home = tmp_path
    _write(home / ".rig" / "config.json", '{"a": 1}', mode=0o600)
    _write(home / ".rig" / "sessions" / "s1.json", "{}")

    migrated = migrate.migrate_home(home)

    assert migrated is True
    assert (home / ".omega" / "config.json").read_text() == '{"a": 1}'
    assert (home / ".omega" / "sessions" / "s1.json").exists()
    # The old directory is left in place, untouched.
    assert (home / ".rig" / "config.json").exists()


def test_migrate_home_preserves_0600_on_config_and_permissions(tmp_path):
    home = tmp_path
    _write(home / ".rig" / "config.json", "{}", mode=0o600)
    _write(home / ".rig" / "permissions.json", "{}", mode=0o600)

    migrate.migrate_home(home)

    for name in ("config.json", "permissions.json"):
        mode = stat.S_IMODE((home / ".omega" / name).stat().st_mode)
        assert mode == 0o600


def test_migrate_home_does_nothing_when_omega_already_exists(tmp_path):
    home = tmp_path
    _write(home / ".rig" / "config.json", '{"old": true}')
    _write(home / ".omega" / "config.json", '{"new": true}')

    migrated = migrate.migrate_home(home)

    assert migrated is False
    assert (home / ".omega" / "config.json").read_text() == '{"new": true}'


def test_migrate_home_does_nothing_when_no_rig_dir(tmp_path):
    home = tmp_path
    assert migrate.migrate_home(home) is False
    assert not (home / ".omega").exists()


def test_migrate_home_is_idempotent(tmp_path):
    home = tmp_path
    _write(home / ".rig" / "config.json", "{}")

    assert migrate.migrate_home(home) is True
    assert migrate.migrate_home(home) is False  # second call is a no-op
    assert (home / ".omega" / "config.json").exists()


def test_migrate_project_copies_rig_to_omega(tmp_path):
    cwd = tmp_path
    _write(cwd / ".rig" / "memory.db", "binary-ish content")

    migrated = migrate.migrate_project(str(cwd))

    assert migrated is True
    assert (cwd / ".omega" / "memory.db").read_text() == "binary-ish content"
    assert (cwd / ".rig" / "memory.db").exists()


def test_migrate_project_does_nothing_when_omega_already_exists(tmp_path):
    cwd = tmp_path
    _write(cwd / ".rig" / "memory.db", "old")
    _write(cwd / ".omega" / "memory.db", "new")

    assert migrate.migrate_project(str(cwd)) is False
    assert (cwd / ".omega" / "memory.db").read_text() == "new"


def test_run_prints_dim_line_only_when_home_migrates(tmp_path, capsys):
    home = tmp_path / "home"
    cwd = tmp_path / "proj"
    _write(home / ".rig" / "config.json", "{}")

    migrate.run(cwd=str(cwd), home=home)

    out = capsys.readouterr().out
    assert "migrated ~/.rig → ~/.omega (the old directory was left in place)" in out
    assert (home / ".omega" / "config.json").exists()


def test_run_is_silent_when_only_project_migrates(tmp_path, capsys):
    home = tmp_path / "home"
    cwd = tmp_path / "proj"
    _write(home / ".omega" / "config.json", "{}")  # home already migrated
    _write(cwd / ".rig" / "memory.db", "x")

    migrate.run(cwd=str(cwd), home=home)

    out = capsys.readouterr().out
    assert "migrated" not in out
    assert (cwd / ".omega" / "memory.db").exists()


def test_run_never_deletes_the_old_directories(tmp_path):
    home = tmp_path / "home"
    cwd = tmp_path / "proj"
    _write(home / ".rig" / "config.json", "{}")
    _write(cwd / ".rig" / "memory.db", "x")

    migrate.run(cwd=str(cwd), home=home)
    migrate.run(cwd=str(cwd), home=home)  # idempotent second run

    assert (home / ".rig" / "config.json").exists()
    assert (cwd / ".rig" / "memory.db").exists()
