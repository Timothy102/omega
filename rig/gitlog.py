import asyncio
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

_SKIP_DIRS = {"node_modules", ".venv"}
_UNIT_SEP = "\x1f"


@dataclass(frozen=True)
class Repo:
    path: Path
    name: str
    branch: str
    dirty: bool


@dataclass(frozen=True)
class Commit:
    sha: str
    short_sha: str
    author: str
    age: str
    subject: str


def _run(repo_path: Path, args: list[str]) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-c", "core.quotepath=off", *args],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def _is_repo(d: Path) -> bool:
    return (d / ".git").exists()


def _branch(repo_path: Path) -> str:
    out = _run(repo_path, ["symbolic-ref", "--short", "-q", "HEAD"])
    if out is not None and out.strip():
        return out.strip()
    out = _run(repo_path, ["rev-parse", "--short", "HEAD"])
    if out is not None and out.strip():
        return out.strip()
    return "?"


def _dirty(repo_path: Path) -> bool:
    out = _run(repo_path, ["status", "--porcelain", "--untracked-files=normal"])
    return bool(out and out.strip())


def _make_repo(d: Path) -> Repo:
    if shutil.which("git") is None:
        return Repo(path=d, name=d.name, branch="?", dirty=False)
    return Repo(path=d, name=d.name, branch=_branch(d), dirty=_dirty(d))


def discover_repos(root: Path, max_depth: int = 2) -> list[Repo]:
    if shutil.which("git") is None:
        return []
    root = root.resolve()
    if _is_repo(root):
        return [_make_repo(root)]

    found: list[Repo] = []

    def walk(d: Path, depth: int) -> None:
        if depth > max_depth:
            return
        try:
            children = sorted(p for p in d.iterdir() if p.is_dir())
        except OSError:
            return
        for child in children:
            if child.name.startswith(".") or child.name in _SKIP_DIRS:
                continue
            if _is_repo(child):
                found.append(_make_repo(child))
                # Never descend into a discovered repo: nested .git dirs
                # (submodules, vendored checkouts) aren't separate projects.
                continue
            walk(child, depth + 1)

    walk(root, 1)
    return sorted(found, key=lambda r: r.name)


def _relative_age(seconds: float) -> str:
    seconds = max(0.0, seconds)
    if seconds < 60:
        return f"{int(seconds)}s"
    minutes = seconds / 60
    if minutes < 60:
        return f"{int(minutes)}m"
    hours = minutes / 60
    if hours < 24:
        return f"{int(hours)}h"
    days = hours / 24
    if days < 7:
        return f"{int(days)}d"
    weeks = days / 7
    if weeks < 52:
        return f"{int(weeks)}w"
    years = days / 365
    return f"{int(years)}y"


def recent_commits(repo: Repo, limit: int = 20) -> list[Commit]:
    if shutil.which("git") is None:
        return []
    out = _run(repo.path, ["log", f"--format=%H{_UNIT_SEP}%an{_UNIT_SEP}%ct{_UNIT_SEP}%s", "-n", str(limit)])
    if not out:
        return []
    now = time.time()
    commits: list[Commit] = []
    for line in out.splitlines():
        parts = line.split(_UNIT_SEP)
        if len(parts) != 4:
            continue
        sha, author, ct, subject = parts
        commits.append(Commit(
            sha=sha,
            short_sha=sha[:7],
            author=author,
            age=_relative_age(now - float(ct)),
            subject=subject,
        ))
    return commits


async def discover_repos_async(root: Path, max_depth: int = 2) -> list[Repo]:
    return await asyncio.to_thread(discover_repos, root, max_depth)


async def recent_commits_async(repo: Repo, limit: int = 20) -> list[Commit]:
    return await asyncio.to_thread(recent_commits, repo, limit)
