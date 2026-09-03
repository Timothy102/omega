import subprocess

import pytest

from omega import checkpoint


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], capture_output=True, text=True, timeout=10)


@pytest.fixture(autouse=True)
def isolate(tmp_path_factory, monkeypatch):
    # A session store separate from any per-test repo dir -- checkpoint.DIR
    # living inside the git working tree would pollute the very status/diff
    # output these tests assert on.
    monkeypatch.setattr(checkpoint, "DIR", tmp_path_factory.mktemp("omega-sessions"))
    yield


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert _git("init", "-q").returncode == 0
    (tmp_path / "a.txt").write_text("a\n")
    return tmp_path


def test_create_returns_none_for_non_git_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert checkpoint.create("sess1", 1) is None


def test_create_returns_a_checkpoint_for_a_git_repo(repo):
    cp = checkpoint.create("sess1", 1)
    assert cp is not None
    assert cp.turn == 1
    assert len(cp.tree_sha) == 40


def test_create_does_not_touch_the_real_staging_area(repo):
    (repo / "untracked.txt").write_text("u\n")
    before = _git("status", "--porcelain").stdout
    checkpoint.create("sess1", 1)
    after = _git("status", "--porcelain").stdout
    # both files stayed untracked -- create() never touched the real index
    assert before == after
    assert "?? untracked.txt" in after
    assert "?? a.txt" in after


def test_create_excludes_omega_directory(repo):
    (repo / ".omega").mkdir()
    (repo / ".omega" / "config.json").write_text("{}")
    cp = checkpoint.create("sess1", 1)
    assert cp is not None
    files = checkpoint.changed_files("sess1")
    assert ".omega/config.json" not in files


def test_undo_restores_modified_tracked_file(repo):
    cp1 = checkpoint.create("sess1", 1)
    assert cp1 is not None
    (repo / "a.txt").write_text("a\nmodified\n")

    summary = checkpoint.undo("sess1", steps=1)
    assert "reverted" in summary
    assert (repo / "a.txt").read_text() == "a\n"


def test_undo_removes_untracked_file_added_after_checkpoint(repo):
    checkpoint.create("sess1", 1)
    (repo / "new.txt").write_text("new\n")

    checkpoint.undo("sess1", steps=1)
    assert not (repo / "new.txt").exists()


def test_undo_restores_deleted_tracked_file(repo):
    checkpoint.create("sess1", 1)
    (repo / "a.txt").unlink()

    checkpoint.undo("sess1", steps=1)
    assert (repo / "a.txt").read_text() == "a\n"


def test_undo_never_touches_omega_directory(repo):
    (repo / ".omega").mkdir()
    (repo / ".omega" / "state.json").write_text('{"x": 1}')
    checkpoint.create("sess1", 1)

    (repo / "a.txt").write_text("a\nmodified\n")
    (repo / ".omega" / "state.json").write_text('{"x": 2}')
    checkpoint.undo("sess1", steps=1)

    assert (repo / "a.txt").read_text() == "a\n"
    assert (repo / ".omega" / "state.json").read_text() == '{"x": 2}'


def test_undo_never_touches_git_directory(repo):
    checkpoint.create("sess1", 1)
    head_before = (repo / ".git" / "HEAD").read_text()
    checkpoint.undo("sess1", steps=1)
    assert (repo / ".git" / "HEAD").read_text() == head_before


def test_undo_two_steps_reverts_two_turns(repo):
    checkpoint.create("sess1", 1)
    (repo / "a.txt").write_text("a\nturn2\n")
    checkpoint.create("sess1", 2)
    (repo / "a.txt").write_text("a\nturn2\nturn3\n")

    summary = checkpoint.undo("sess1", steps=2)
    assert "turn 1" in summary
    assert (repo / "a.txt").read_text() == "a\n"


def test_undo_out_of_range_reports_clearly(repo):
    checkpoint.create("sess1", 1)
    result = checkpoint.undo("sess1", steps=5)
    assert "no checkpoint" in result


def test_undo_non_git_cwd_message(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert checkpoint.undo("sess1", steps=1) == checkpoint.NOT_A_REPO


def test_diff_non_git_cwd_message(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert checkpoint.diff("sess1") == checkpoint.NOT_A_REPO


def test_changed_files_non_git_cwd_returns_empty_list(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert checkpoint.changed_files("sess1") == []


def test_diff_defaults_to_first_checkpoint(repo):
    checkpoint.create("sess1", 1)
    (repo / "a.txt").write_text("a\nturn2\n")
    checkpoint.create("sess1", 2)
    (repo / "a.txt").write_text("a\nturn2\nturn3\n")

    diff_text = checkpoint.diff("sess1")
    assert "+turn2" in diff_text
    assert "+turn3" in diff_text


def test_diff_since_turn_selects_that_checkpoint(repo):
    checkpoint.create("sess1", 1)
    (repo / "a.txt").write_text("a\nturn2\n")
    checkpoint.create("sess1", 2)
    (repo / "a.txt").write_text("a\nturn2\nturn3\n")

    diff_text = checkpoint.diff("sess1", since_turn=2)
    assert "+turn2" not in diff_text
    assert "+turn3" in diff_text


def test_diff_includes_untracked_files(repo):
    checkpoint.create("sess1", 1)
    (repo / "new.txt").write_text("brand new\n")

    diff_text = checkpoint.diff("sess1")
    assert "new.txt" in diff_text
    assert "brand new" in diff_text


def test_diff_unknown_turn_reports_clearly(repo):
    checkpoint.create("sess1", 1)
    result = checkpoint.diff("sess1", since_turn=99)
    assert "no checkpoint" in result


def test_changed_files_lists_touched_paths(repo):
    checkpoint.create("sess1", 1)
    (repo / "a.txt").write_text("a\nmodified\n")
    (repo / "new.txt").write_text("new\n")

    files = checkpoint.changed_files("sess1")
    assert set(files) == {"a.txt", "new.txt"}
