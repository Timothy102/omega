"""A Task pairs a session with a repo (optionally its own git worktree) and
the run state (status/phase/tokens/cost) the D1 daemon updates as a worker
process streams events -- see server/manager.py. Task id is always the
underlying session id, so `session.load(task.id)` recovers full history.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from . import gitlog, session

DIR = Path.home() / ".omega" / "tasks"
WORKTREES_DIR = Path.home() / ".omega" / "worktrees"

Status = Literal["idle", "running", "waiting_input", "done", "failed"]

# `gh pr view` is a network round trip; a WS overview push or a sidebar repaint
# must not pay for it every time -- see pr_info().
_PR_CACHE_TTL = 60.0
_pr_cache: dict[str, tuple[float, dict[str, Any] | None]] = {}


@dataclass
class Task:
    id: str
    title: str
    repo: str
    cwd: str
    worktree: bool
    branch: str | None = None
    pr: dict[str, Any] | None = None
    model: str | None = None
    mode: str = "build"
    status: Status = "idle"
    phase: str = ""
    created: float = 0.0
    updated: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float | None = None
    elapsed_s: float = 0.0

    @property
    def path(self) -> Path:
        return DIR / f"{self.id}.json"

    def save(self) -> None:
        DIR.mkdir(parents=True, exist_ok=True)
        self.updated = time.time()
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=1))
        tmp.replace(self.path)


def _slugify(text: str, limit: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:limit].strip("-") or "task"


def title_from_prompt(prompt: str, width: int = 80) -> str:
    text = " ".join(prompt.split())
    return text[: width - 1] + "…" if len(text) > width else text


def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=30)


def _repo_root(repo: str) -> Path | None:
    path = Path(repo).expanduser().resolve()
    result = _run_git(["rev-parse", "--show-toplevel"], path)
    if result.returncode != 0:
        return None
    return Path(result.stdout.strip())


def create(repo: str, prompt: str | None = None, worktree: bool = True,
          model: str | None = None, mode: str = "build") -> Task:
    """Creates the underlying session first (its id becomes the task id), then
    -- when `worktree` -- a git worktree branched from the repo's current HEAD
    at `~/.omega/worktrees/<repo-basename>/<task-id>` and points the session's
    cwd at it, so every tool the agent runs (bash's cwd included) stays inside
    the isolated checkout."""
    root = _repo_root(repo)
    if worktree and root is None:
        raise ValueError(f"{repo!r} is not inside a git repository")
    root = root or Path(repo).expanduser().resolve()

    sess = session.Session.new(cwd=str(root), mode=mode)
    branch: str | None = None
    cwd = str(root)

    if worktree:
        slug = _slugify(prompt) if prompt else sess.id
        branch = f"omega/{slug}"
        wt_path = WORKTREES_DIR / root.name / sess.id
        wt_path.parent.mkdir(parents=True, exist_ok=True)
        added = _run_git(["worktree", "add", str(wt_path), "-b", branch], root)
        if added.returncode != 0:
            raise RuntimeError(f"git worktree add failed: {added.stderr.strip()}")
        cwd = str(wt_path)
        sess.cwd = cwd
    else:
        branch = gitlog._branch(root) if (root / ".git").exists() else None
        if branch == "?":
            branch = None

    sess.save()
    task = Task(
        id=sess.id, title=title_from_prompt(prompt) if prompt else "(no prompt yet)",
        repo=str(root), cwd=cwd, worktree=worktree, branch=branch,
        model=model, mode=mode, created=sess.created, updated=sess.created,
    )
    task.save()
    return task


def _load(path: Path) -> Task | None:
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    known = set(Task.__dataclass_fields__)
    try:
        return Task(**{k: v for k, v in raw.items() if k in known})
    except TypeError:
        return None


def list_tasks() -> list[Task]:
    if not DIR.exists():
        return []
    found = [t for p in DIR.glob("*.json") if (t := _load(p)) is not None]
    return sorted(found, key=lambda t: t.updated, reverse=True)


def get(task_id: str) -> Task | None:
    path = DIR / f"{task_id}.json"
    if path.exists():
        return _load(path)
    matches = sorted(DIR.glob(f"{task_id}*.json")) if DIR.exists() else []
    return _load(matches[-1]) if matches else None


def update(task_id: str, **fields: Any) -> Task | None:
    task = get(task_id)
    if task is None:
        return None
    for key, value in fields.items():
        if key in Task.__dataclass_fields__:
            setattr(task, key, value)
    task.save()
    return task


def remove(task_id: str, delete_worktree: bool = False) -> bool:
    task = get(task_id)
    if task is None:
        return False
    if delete_worktree and task.worktree:
        _run_git(["worktree", "remove", "--force", task.cwd], Path(task.repo))
    task.path.unlink(missing_ok=True)
    return True


def pr_info(task: Task) -> dict[str, Any] | None:
    """`gh pr view` for this task's branch, cached for `_PR_CACHE_TTL` seconds.
    Never raises: no branch, no `gh` on PATH, no PR yet, or a network hiccup
    all just mean "no PR to show" rather than an error the caller must handle."""
    if not task.branch or shutil.which("gh") is None:
        return None
    now = time.time()
    cached = _pr_cache.get(task.id)
    if cached is not None and now - cached[0] < _PR_CACHE_TTL:
        return cached[1]

    info: dict[str, Any] | None
    try:
        result = subprocess.run(
            ["gh", "pr", "view", "--json", "number,url,state,statusCheckRollup,title",
             "--head", task.branch],
            cwd=task.repo, capture_output=True, text=True, timeout=15,
        )
        info = json.loads(result.stdout) if result.returncode == 0 and result.stdout.strip() else None
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        info = None
    _pr_cache[task.id] = (now, info)
    return info
