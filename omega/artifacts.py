import json
import secrets
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

from .tools import truncate

DIR = Path.home() / ".omega" / "sessions"

Meta = dict[str, Any]

# Threshold above which a tool result is offloaded to an artifact instead of
# returned inline.
OFFLOAD_THRESHOLD = 4000

# A single stored artifact is capped here even though the tool result that
# produced it may be larger -- a clipped result on disk still beats no
# artifact at all, and beats forcing the whole thing through the preview.
RESULT_MAX_CHARS = 100_000

# Default page size for fetch_result -- comfortably under tools.MAX_INLINE_CHARS
# so a default-sized page never itself needs the inline re-entry cap.
PAGE_CHARS = 18_000

# Per-process, per-session content cache checked before disk. Bounded by
# entry count rather than bytes: RESULT_MAX_CHARS already caps a single
# entry, so MAX_CACHE_ENTRIES * RESULT_MAX_CHARS is a safe worst case.
MAX_CACHE_ENTRIES = 50
_CACHE: OrderedDict[str, str] = OrderedDict()


def _cache_key(session_id: str, artifact_id: str) -> str:
    return f"{session_id}/{artifact_id}"


def _cache_put(session_id: str, artifact_id: str, content: str) -> None:
    key = _cache_key(session_id, artifact_id)
    _CACHE[key] = content
    _CACHE.move_to_end(key)
    while len(_CACHE) > MAX_CACHE_ENTRIES:
        _CACHE.popitem(last=False)


def _cache_get(session_id: str, artifact_id: str) -> str | None:
    key = _cache_key(session_id, artifact_id)
    if key not in _CACHE:
        return None
    _CACHE.move_to_end(key)
    return _CACHE[key]


def _session_dir(session_id: str) -> Path:
    return DIR / session_id / "artifacts"


def _paths(session_id: str, artifact_id: str) -> tuple[Path, Path]:
    d = _session_dir(session_id)
    return d / f"{artifact_id}.txt", d / f"{artifact_id}.meta.json"


def _write_meta(meta_path: Path, meta: Meta) -> None:
    tmp = meta_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(meta, indent=1))
    tmp.replace(meta_path)


def save(session_id: str, content: str, title: str | None = None, kind: str = "offload") -> str:
    d = _session_dir(session_id)
    d.mkdir(parents=True, exist_ok=True)
    artifact_id = secrets.token_hex(4)
    text_path, meta_path = _paths(session_id, artifact_id)
    stored = content if len(content) <= RESULT_MAX_CHARS else truncate(content, RESULT_MAX_CHARS)
    text_path.write_text(stored)
    now = time.time()
    _write_meta(meta_path, {
        "title": title or "",
        "kind": kind,
        "created": now,
        "updated": now,
        "char_count": len(stored),
    })
    _cache_put(session_id, artifact_id, stored)
    return artifact_id


def fetch(session_id: str, artifact_id: str, offset: int = 0, limit: int = PAGE_CHARS) -> str:
    """Returns `content[offset:offset+limit]` plus a trailer line: either
    `[end]` when the page reaches the artifact's end, or
    `[chars <start>-<end> of <total>; next_offset=<n>]` so the model knows how
    to page for more without re-fetching from the start."""
    content = _cache_get(session_id, artifact_id)
    if content is None:
        text_path, _meta_path = _paths(session_id, artifact_id)
        if not text_path.exists():
            return f"error: no artifact {artifact_id!r} in session {session_id!r}"
        content = text_path.read_text()
        _cache_put(session_id, artifact_id, content)

    total = len(content)
    end = min(offset + limit, total)
    chunk = content[offset:end]
    trailer = "[end]" if end >= total else f"[chars {offset}-{end} of {total}; next_offset={end}]"
    return f"{chunk}\n{trailer}"


def update(session_id: str, artifact_id: str, content: str) -> str:
    text_path, meta_path = _paths(session_id, artifact_id)
    if not meta_path.exists():
        return f"error: no artifact {artifact_id!r} in session {session_id!r}"
    stored = content if len(content) <= RESULT_MAX_CHARS else truncate(content, RESULT_MAX_CHARS)
    text_path.write_text(stored)
    meta = json.loads(meta_path.read_text())
    meta["char_count"] = len(stored)
    meta["updated"] = time.time()
    _write_meta(meta_path, meta)
    _cache_put(session_id, artifact_id, stored)
    return f"updated artifact {artifact_id}"


def list_artifacts(session_id: str) -> list[Meta]:
    d = _session_dir(session_id)
    if not d.exists():
        return []
    out: list[Meta] = []
    for meta_path in sorted(d.glob("*.meta.json")):
        artifact_id = meta_path.name.removesuffix(".meta.json")
        try:
            meta = json.loads(meta_path.read_text())
        except Exception:
            continue
        out.append({
            "id": artifact_id,
            "title": meta.get("title", ""),
            "kind": meta.get("kind", "offload"),
            "size": meta.get("char_count", 0),
            "created": meta.get("created", 0),
        })
    return out


def offload_if_large(text: str, session_id: str, threshold: int = OFFLOAD_THRESHOLD,
                     preview_limit: int = 1200) -> str:
    if len(text) <= threshold:
        return text
    artifact_id = save(session_id, text, kind="offload")
    clipped_note = f" (clipped to {RESULT_MAX_CHARS} chars on save)" if len(text) > RESULT_MAX_CHARS else ""
    return (f"{truncate(text, preview_limit)}\n"
            f"[full output: {len(text)} chars{clipped_note}, saved as artifact {artifact_id} "
            f"— fetch_result({artifact_id}) to read more]")
