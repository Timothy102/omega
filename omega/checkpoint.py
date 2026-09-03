"""Working-tree checkpoints for BUILD-mode turns.

Each checkpoint snapshots the working tree into a git tree object via a
throwaway index (GIT_INDEX_FILE pointed at a temp file) so the user's real
index/staging area and HEAD are never touched. `.omega/` is excluded from
every snapshot so undo/diff never reads or writes omega's own state.
"""
import json
import os
import secrets
import shutil
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

DIR = Path.home() / ".omega" / "sessions"

NOT_A_REPO = "not a git repository"

# Pathspec passed to every `git add -A` snapshot: everything except omega's
# own project-local state directory.
_PATHSPEC = ("--", ".", ":!.omega")


@dataclass(frozen=True)
class Checkpoint:
    id: str
    turn: int
    created: float
    tree_sha: str


def _checkpoints_path(session_id: str) -> Path:
    return DIR / session_id / "checkpoints.json"


def _load(session_id: str) -> list[Checkpoint]:
    path = _checkpoints_path(session_id)
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError:
        return []
    return [Checkpoint(**c) for c in raw]


def _save(session_id: str, checkpoints: list[Checkpoint]) -> None:
    path = _checkpoints_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps([asdict(c) for c in checkpoints], indent=1))
    tmp.replace(path)


def _find(checkpoints: list[Checkpoint], since_turn: int | None) -> Checkpoint | None:
    if since_turn is None:
        return checkpoints[0] if checkpoints else None
    for cp in checkpoints:
        if cp.turn == since_turn:
            return cp
    return None


def _repo_root(cwd: str) -> Path | None:
    if shutil.which("git") is None:
        return None
    try:
        result = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=cwd,
                                capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return Path(result.stdout.strip())


def _temp_index() -> str:
    """A path git can treat as a fresh index -- created then removed so
    `git add`/`read-tree` populate it from nothing, not a stale prior run."""
    fd, path = tempfile.mkstemp(prefix="omega-checkpoint-idx-")
    os.close(fd)
    os.remove(path)
    return path


def _write_tree(repo_root: Path) -> str | None:
    idx_path = _temp_index()
    env = {**os.environ, "GIT_INDEX_FILE": idx_path}
    try:
        added = subprocess.run(["git", "add", "-A", *_PATHSPEC], cwd=repo_root, env=env,
                               capture_output=True, text=True, timeout=60)
        if added.returncode != 0:
            return None
        written = subprocess.run(["git", "write-tree"], cwd=repo_root, env=env,
                                 capture_output=True, text=True, timeout=30)
        if written.returncode != 0:
            return None
        return written.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        Path(idx_path).unlink(missing_ok=True)


def create(session_id: str, turn: int, cwd: str | None = None) -> Checkpoint | None:
    """Snapshots the working tree without touching the index or HEAD. Returns
    None (never raises) when `cwd` isn't inside a git repo."""
    root = _repo_root(cwd or os.getcwd())
    if root is None:
        return None
    tree_sha = _write_tree(root)
    if tree_sha is None:
        return None
    cp = Checkpoint(id=secrets.token_hex(4), turn=turn, created=time.time(), tree_sha=tree_sha)
    checkpoints = _load(session_id)
    checkpoints.append(cp)
    _save(session_id, checkpoints)
    return cp


def undo(session_id: str, steps: int = 1, cwd: str | None = None) -> str:
    """Restores the working tree to the checkpoint `steps` turns back. Never
    touches .git, untracked ignored files, or .omega/."""
    root = _repo_root(cwd or os.getcwd())
    if root is None:
        return NOT_A_REPO
    checkpoints = _load(session_id)
    if steps < 1 or steps > len(checkpoints):
        return f"no checkpoint {steps} turn(s) back (this session has {len(checkpoints)})"
    target = checkpoints[-steps]

    current_tree = _write_tree(root)
    if current_tree is None:
        return "error: could not snapshot the current working tree"

    diffed = subprocess.run(
        ["git", "diff-tree", "-r", "--no-renames", "--name-status", current_tree, target.tree_sha],
        cwd=root, capture_output=True, text=True, timeout=30)
    to_delete = [line[2:] for line in diffed.stdout.splitlines() if line.startswith("D\t")]
    touched = len(diffed.stdout.splitlines())

    idx_path = _temp_index()
    env = {**os.environ, "GIT_INDEX_FILE": idx_path}
    try:
        read = subprocess.run(["git", "read-tree", target.tree_sha], cwd=root, env=env,
                              capture_output=True, text=True, timeout=30)
        if read.returncode != 0:
            return f"error: git read-tree failed: {read.stderr.strip()}"
        checked_out = subprocess.run(["git", "checkout-index", "-a", "-f"], cwd=root, env=env,
                                     capture_output=True, text=True, timeout=60)
        if checked_out.returncode != 0:
            return f"error: git checkout-index failed: {checked_out.stderr.strip()}"
    except (OSError, subprocess.SubprocessError) as e:
        return f"error: {type(e).__name__}: {e}"
    finally:
        Path(idx_path).unlink(missing_ok=True)

    removed = 0
    for rel in to_delete:
        if rel.startswith(".omega/") or rel.startswith(".git/"):
            continue
        path = root / rel
        try:
            if path.exists() or path.is_symlink():
                path.unlink()
                removed += 1
        except OSError:
            continue
        _prune_empty_parents(path.parent, root)

    return (f"reverted {steps} turn(s) to the checkpoint from turn {target.turn} "
           f"({touched} file(s) touched, {removed} removed)")


def _prune_empty_parents(start: Path, root: Path) -> None:
    d = start
    while d != root and root in d.parents and d.exists():
        try:
            next(d.iterdir())
            return  # not empty
        except StopIteration:
            pass
        try:
            d.rmdir()
        except OSError:
            return
        d = d.parent


def diff(session_id: str, since_turn: int | None = None, cwd: str | None = None) -> str:
    """Unified diff between the checkpoint before `since_turn` (default: the
    session's first checkpoint) and the current working tree, untracked files
    included."""
    root = _repo_root(cwd or os.getcwd())
    if root is None:
        return NOT_A_REPO
    checkpoints = _load(session_id)
    target = _find(checkpoints, since_turn)
    if target is None:
        return "no checkpoint recorded yet this session" if since_turn is None \
            else f"no checkpoint recorded for turn {since_turn}"

    idx_path = _temp_index()
    env = {**os.environ, "GIT_INDEX_FILE": idx_path}
    try:
        added = subprocess.run(["git", "add", "-A", *_PATHSPEC], cwd=root, env=env,
                               capture_output=True, text=True, timeout=60)
        if added.returncode != 0:
            return f"error: git add failed: {added.stderr.strip()}"
        diffed = subprocess.run(["git", "diff", "--cached", target.tree_sha], cwd=root, env=env,
                                capture_output=True, text=True, timeout=60)
        return diffed.stdout or "(no changes since that checkpoint)"
    except (OSError, subprocess.SubprocessError) as e:
        return f"error: {type(e).__name__}: {e}"
    finally:
        Path(idx_path).unlink(missing_ok=True)


def changed_files(session_id: str, since_turn: int | None = None, cwd: str | None = None) -> list[str]:
    """Empty list for a non-git cwd or a session with no checkpoints -- unlike
    undo()/diff(), the return type has no room for a sentinel message."""
    root = _repo_root(cwd or os.getcwd())
    if root is None:
        return []
    checkpoints = _load(session_id)
    target = _find(checkpoints, since_turn)
    if target is None:
        return []

    idx_path = _temp_index()
    env = {**os.environ, "GIT_INDEX_FILE": idx_path}
    try:
        added = subprocess.run(["git", "add", "-A", *_PATHSPEC], cwd=root, env=env,
                               capture_output=True, text=True, timeout=60)
        if added.returncode != 0:
            return []
        listed = subprocess.run(["git", "diff", "--cached", "--name-only", target.tree_sha],
                                cwd=root, env=env, capture_output=True, text=True, timeout=60)
        return [line for line in listed.stdout.splitlines() if line]
    except (OSError, subprocess.SubprocessError):
        return []
    finally:
        Path(idx_path).unlink(missing_ok=True)
