import os
import shutil
import subprocess
from pathlib import Path

import pytest

from rig import gitlog

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")

_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "Test Author",
    "GIT_AUTHOR_EMAIL": "author@example.com",
    "GIT_COMMITTER_NAME": "Test Author",
    "GIT_COMMITTER_EMAIL": "author@example.com",
}


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", "user.name=Test Author", "-c", "user.email=author@example.com",
         "-c", "commit.gpgsign=false", *args],
        cwd=cwd, capture_output=True, text=True, env=_ENV, check=True,
    )


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")


def _commit(path: Path, message: str, filename: str = "file.txt", content: str = "x") -> str:
    (path / filename).write_text(content)
    _git(path, "add", filename)
    _git(path, "commit", "-q", "-m", message)
    return _git(path, "rev-parse", "HEAD").stdout.strip()


def test_discover_root_is_repo(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _commit(tmp_path, "initial")
    repos = gitlog.discover_repos(tmp_path)
    assert len(repos) == 1
    assert repos[0].name == tmp_path.name
    assert repos[0].dirty is False


def test_discover_children_plus_non_repo(tmp_path: Path) -> None:
    _init_repo(tmp_path / "beta")
    _commit(tmp_path / "beta", "c1")
    _init_repo(tmp_path / "alpha")
    _commit(tmp_path / "alpha", "c1")
    (tmp_path / "plain_dir").mkdir()
    (tmp_path / "plain_dir" / "readme.txt").write_text("hi")

    repos = gitlog.discover_repos(tmp_path)
    names = [r.name for r in repos]
    assert names == ["alpha", "beta"]


def test_discover_max_depth_respected(tmp_path: Path) -> None:
    deep = tmp_path / "a" / "b" / "c"
    _init_repo(deep)
    _commit(deep, "c1")

    assert gitlog.discover_repos(tmp_path, max_depth=2) == []
    found = gitlog.discover_repos(tmp_path, max_depth=3)
    assert [r.name for r in found] == ["c"]


def test_discover_does_not_descend_into_found_repo(tmp_path: Path) -> None:
    outer = tmp_path / "outer"
    _init_repo(outer)
    _commit(outer, "c1")
    inner = outer / "vendor" / "inner"
    _init_repo(inner)
    _commit(inner, "c1")

    repos = gitlog.discover_repos(tmp_path, max_depth=5)
    assert [r.name for r in repos] == ["outer"]


def test_dirty_detection_untracked_and_modified(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _commit(tmp_path, "initial")
    repos = gitlog.discover_repos(tmp_path)
    assert repos[0].dirty is False

    (tmp_path / "file.txt").write_text("changed")
    assert gitlog.discover_repos(tmp_path)[0].dirty is True

    _git(tmp_path, "checkout", "--", "file.txt")
    (tmp_path / "new_untracked.txt").write_text("new")
    assert gitlog.discover_repos(tmp_path)[0].dirty is True


def test_detached_head_branch_falls_back_to_short_sha(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    sha = _commit(tmp_path, "initial")
    _commit(tmp_path, "second", filename="other.txt")
    _git(tmp_path, "checkout", "-q", sha)

    repo = gitlog.discover_repos(tmp_path)[0]
    short_sha = _git(tmp_path, "rev-parse", "--short", sha).stdout.strip()
    assert repo.branch == short_sha


def test_recent_commits_parses_quotes_and_unicode(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    subject = 'fix: handle "quotes" and emoji \U0001f389 café'
    _commit(tmp_path, subject)

    repo = gitlog.discover_repos(tmp_path)[0]
    commits = gitlog.recent_commits(repo)
    assert len(commits) == 1
    commit = commits[0]
    assert commit.subject == subject
    assert commit.author == "Test Author"
    assert len(commit.short_sha) == 7
    assert commit.sha.startswith(commit.short_sha)
    assert commit.age.endswith("s")


def test_recent_commits_newest_first_and_limit(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _commit(tmp_path, "first", filename="a.txt")
    _commit(tmp_path, "second", filename="b.txt")
    _commit(tmp_path, "third", filename="c.txt")

    repo = gitlog.discover_repos(tmp_path)[0]
    commits = gitlog.recent_commits(repo, limit=2)
    assert [c.subject for c in commits] == ["third", "second"]


def test_recent_commits_empty_repo_returns_empty_list(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    repo = gitlog.discover_repos(tmp_path)[0]
    assert gitlog.recent_commits(repo) == []


def test_missing_git_returns_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gitlog.shutil, "which", lambda _name: None)
    assert gitlog.discover_repos(tmp_path) == []
    repo = gitlog.Repo(path=tmp_path, name=tmp_path.name, branch="?", dirty=False)
    assert gitlog.recent_commits(repo) == []


async def test_async_wrappers_match_sync(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _commit(tmp_path, "initial")

    repos = await gitlog.discover_repos_async(tmp_path)
    assert len(repos) == 1
    commits = await gitlog.recent_commits_async(repos[0])
    assert len(commits) == 1
    assert commits[0].subject == "initial"
