"""Shared composer helpers for the TUI (and any other Python front-end).

Mirrors the Swift `ComposerPrompt` helpers: `@mention` parsing, workspace
file ranking, and flattening dropped paths into the text-only prompt the
daemon accepts.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

_TOKEN = re.compile(r"[A-Za-z0-9._\-++/\\]")
_SKIP_DIRS = {
    ".git", ".hg", ".svn",
    "node_modules", ".build", "DerivedData", ".swiftpm",
    "dist", "build", "Pods", "Carthage",
    ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache",
    ".next", ".nuxt", "target", ".omega", ".idea", ".vscode",
    "xcuserdata",
}
_MAX_FILES = 8_000
_MAX_DEPTH = 10
_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".tif", ".tiff", ".heic", ".bmp"}


@dataclass(frozen=True)
class MentionQuery:
    query: str
    start: int  # character offset of '@'
    end: int    # character offset of the cursor (exclusive of the token)


def mention_query(text: str, cursor: int) -> MentionQuery | None:
    """If `cursor` sits inside an `@token` at a word boundary, return it."""
    if not text:
        return None
    cursor = max(0, min(cursor, len(text)))
    start = cursor
    while start > 0 and _TOKEN.match(text[start - 1]):
        start -= 1
    if start == 0 or text[start - 1] != "@":
        return None
    at = start - 1
    if at > 0 and not text[at - 1].isspace():
        return None
    return MentionQuery(query=text[start:cursor], start=at, end=cursor)


def rank_files(files: list[str], query: str, limit: int = 12) -> list[str]:
    q = query.strip().lower()
    if not q:
        return sorted(files, key=lambda p: (p.count("/"), p.lower()))[:limit]

    hits: list[tuple[int, int, str, str]] = []
    for path in files:
        filename = path.rsplit("/", 1)[-1]
        path_l, file_l = path.lower(), filename.lower()
        if file_l == q:
            score = 0
        elif file_l.startswith(q):
            score = 1
        elif path_l.startswith(q):
            score = 2
        elif q in file_l:
            score = 3
        elif q in path_l:
            score = 4
        else:
            continue
        hits.append((score, len(filename), file_l, path))
    hits.sort()
    return [h[3] for h in hits[:limit]]


def insert_mention(text: str, cursor: int, path: str) -> tuple[str, int]:
    """Replace the active `@token` (or append) with `` `path` ``. Returns (text, cursor)."""
    q = mention_query(text, cursor)
    replacement = f"`{path}` "
    if q is None:
        new = text + replacement
        return new, len(new)
    new = text[:q.start] + replacement + text[q.end:]
    return new, q.start + len(replacement)


def build_prompt(text: str, attachment_paths: list[str]) -> str:
    trimmed = text.strip()
    paths = [p.strip() for p in attachment_paths if p.strip()]
    if not paths:
        return trimmed
    lines: list[str] = []
    if trimmed:
        lines.append(trimmed)
        lines.append("")
    lines.append("Attached:")
    lines.extend(f"- {p}" for p in paths)
    return "\n".join(lines)


def display_path(path: str, workspace_root: str | None) -> str:
    if not workspace_root:
        return path
    root = os.path.realpath(workspace_root)
    file = os.path.realpath(path)
    if file == root:
        return "."
    prefix = root if root.endswith(os.sep) else root + os.sep
    if file.startswith(prefix):
        return file[len(prefix):]
    return path


def is_image_path(path: str) -> bool:
    return Path(path).suffix.lower() in _IMAGE_EXT


def list_workspace_files(root: str) -> list[str]:
    root_path = Path(root).expanduser().resolve()
    if not root_path.is_dir():
        return []
    files: list[str] = []
    root_s = str(root_path)
    prefix = root_s if root_s.endswith(os.sep) else root_s + os.sep
    for dirpath, dirnames, filenames in os.walk(root_path):
        rel_dir = dirpath[len(prefix):] if dirpath.startswith(prefix) else ""
        depth = 0 if not rel_dir else rel_dir.count(os.sep) + 1
        if depth > _MAX_DEPTH:
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            if name.startswith("."):
                continue
            rel = name if not rel_dir else f"{rel_dir}/{name}".replace("\\", "/")
            files.append(rel)
            if len(files) >= _MAX_FILES:
                return files
    return files
