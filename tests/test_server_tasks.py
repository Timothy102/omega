import subprocess
from pathlib import Path

import pytest

from omega import tasks


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=10)


@pytest.fixture(autouse=True)
def isolate(tmp_path_factory, monkeypatch):
    monkeypatch.setattr(tasks, "DIR", tmp_path_factory.mktemp("omega-tasks"))
    monkeypatch.setattr(tasks, "WORKTREES_DIR", tmp_path_factory.mktemp("omega-worktrees"))
    monkeypatch.setattr(tasks, "_pr_cache", {})
    from omega import session
    monkeypatch.setattr(session, "DIR", tmp_path_factory.mktemp("omega-sessions"))
    yield


@pytest.fixture
def repo(tmp_path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    assert _git("init", "-q", cwd=root).returncode == 0
    _git("config", "user.email", "a@b.com", cwd=root)
    _git("config", "user.name", "a", cwd=root)
    (root / "a.txt").write_text("hi\n")
    _git("add", ".", cwd=root)
    assert _git("commit", "-q", "-m", "init", cwd=root).returncode == 0
    return root


def test_create_without_worktree_uses_repo_cwd(repo):
    task = tasks.create(str(repo), prompt="do a thing", worktree=False)
    assert task.cwd == str(repo)
    assert task.worktree is False
    assert task.title == "do a thing"


def test_create_with_worktree_branches_from_head(repo):
    task = tasks.create(str(repo), prompt="Fix The Bug!!", worktree=True)
    wt = Path(task.cwd)
    assert wt.exists()
    assert wt != repo
    assert task.branch == "omega/fix-the-bug"
    assert (wt / "a.txt").read_text() == "hi\n"

    branches = _git("branch", "--list", task.branch, cwd=repo).stdout
    assert task.branch in branches


def test_create_worktree_path_is_scoped_by_repo_and_task_id(repo):
    task = tasks.create(str(repo), prompt="hello world", worktree=True)
    assert Path(task.cwd).parent.name == repo.name
    assert Path(task.cwd).name == task.id


def test_create_without_prompt_uses_task_id_as_slug(repo):
    task = tasks.create(str(repo), prompt=None, worktree=True)
    assert task.branch == f"omega/{task.id}"
    assert task.title == "(no prompt yet)"


def test_create_worktree_requires_a_git_repo(tmp_path):
    with pytest.raises(ValueError):
        tasks.create(str(tmp_path), worktree=True)


def test_create_without_worktree_allows_non_git_dir(tmp_path):
    task = tasks.create(str(tmp_path), worktree=False)
    assert task.cwd == str(tmp_path.resolve())
    assert task.branch is None


def test_title_truncated_to_80_chars(repo):
    long_prompt = "x" * 200
    task = tasks.create(str(repo), prompt=long_prompt, worktree=False)
    assert len(task.title) <= 80


def test_list_and_get_roundtrip(repo):
    t1 = tasks.create(str(repo), prompt="first", worktree=False)
    t2 = tasks.create(str(repo), prompt="second", worktree=False)

    listed = tasks.list_tasks()
    assert {t.id for t in listed} == {t1.id, t2.id}

    fetched = tasks.get(t1.id)
    assert fetched is not None
    assert fetched.title == "first"


def test_get_unknown_task_returns_none():
    assert tasks.get("no-such-task") is None


def test_update_persists_fields(repo):
    task = tasks.create(str(repo), prompt="p", worktree=False)
    updated = tasks.update(task.id, status="running", tokens_in=42)
    assert updated is not None
    assert updated.status == "running"
    assert updated.tokens_in == 42

    reloaded = tasks.get(task.id)
    assert reloaded is not None
    assert reloaded.status == "running"
    assert reloaded.tokens_in == 42


def test_remove_deletes_task_record(repo):
    task = tasks.create(str(repo), prompt="p", worktree=False)
    assert tasks.remove(task.id) is True
    assert tasks.get(task.id) is None


def test_remove_can_delete_worktree(repo):
    task = tasks.create(str(repo), prompt="p", worktree=True)
    wt = Path(task.cwd)
    assert wt.exists()
    assert tasks.remove(task.id, delete_worktree=True) is True
    assert not wt.exists()


def test_remove_unknown_task_returns_false():
    assert tasks.remove("no-such-task") is False


def test_pr_info_none_without_gh(repo, monkeypatch):
    monkeypatch.setattr(tasks.shutil, "which", lambda _name: None)
    task = tasks.create(str(repo), prompt="p", worktree=True)
    assert tasks.pr_info(task) is None


def test_pr_info_none_without_branch(repo):
    task = tasks.create(str(repo), prompt="p", worktree=False)
    task.branch = None
    assert tasks.pr_info(task) is None
