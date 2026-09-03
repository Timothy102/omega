"""Project and global instruction files for the STABLE half of the system
prompt -- the omega analogue of CLAUDE.md, read once at startup rather than
injected per-turn like `memory`.

Load order (least to most specific, so a subdirectory's file adds to the
root's rather than replacing it):

1. ``~/.omega/OMEGA.md`` -- global, applies to every project
2. ``OMEGA.md`` at each directory from the git root down to ``cwd``
3. ``.omega/instructions.md`` in ``cwd``, if present

At each of the locations in (2) and (3), ``CLAUDE.md`` is read instead when
no ``OMEGA.md`` exists there -- many projects already have one.
"""
import os
from pathlib import Path

MAX_CHARS = 12_000
GLOBAL_PATH = Path.home() / ".omega" / "OMEGA.md"

_PRIMARY = "OMEGA.md"
_FALLBACK = "CLAUDE.md"


def _git_root(start: Path) -> Path | None:
    for candidate in (start, *start.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def _project_dirs(cwd: Path, root: Path) -> list[Path]:
    """root, then each path component down to cwd -- root first, so its file
    is read before (and therefore superseded in emphasis by) a subdir's."""
    if cwd == root:
        return [root]
    dirs = [root]
    current = root
    for part in cwd.relative_to(root).parts:
        current = current / part
        dirs.append(current)
    return dirs


def _find(d: Path) -> tuple[Path, str] | None:
    for fname in (_PRIMARY, _FALLBACK):
        p = d / fname
        if p.is_file():
            return p, fname
    return None


def _block(path: Path, fname: str, relpath: str) -> str | None:
    try:
        text = path.read_text(errors="replace").strip()
    except OSError:
        return None
    if not text:
        return None
    return f"# Instructions ({fname} at {relpath})\n{text}"


def _cap(text: str, limit: int | None = None) -> str:
    limit = MAX_CHARS if limit is None else limit
    if len(text) <= limit:
        return text
    dropped = len(text) - limit
    return (f"{text[:limit]}\n\n...[truncated {dropped} chars; "
            f"read the source file directly with `read` for the rest]")


def load(cwd: str | Path | None = None) -> str:
    cwd_path = Path(cwd).resolve() if cwd else Path(os.getcwd())
    blocks: list[str] = []

    if GLOBAL_PATH.is_file():
        block = _block(GLOBAL_PATH, _PRIMARY, "~/.omega")
        if block:
            blocks.append(block)

    root = _git_root(cwd_path) or cwd_path
    for d in _project_dirs(cwd_path, root):
        found = _find(d)
        if found is None:
            continue
        path, fname = found
        relpath = "." if d == root else str(d.relative_to(root))
        block = _block(path, fname, relpath)
        if block:
            blocks.append(block)

    local = cwd_path / ".omega" / "instructions.md"
    block = _block(local, "instructions.md", ".omega/instructions.md")
    if block:
        blocks.append(block)

    return _cap("\n\n".join(blocks))


def system_block(cwd: str | Path | None = None) -> str:
    """Empty string when nothing is found, so a caller can splice it in
    unconditionally without an extra blank section."""
    return load(cwd)
