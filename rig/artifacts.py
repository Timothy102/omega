import json
import secrets
import time
from pathlib import Path
from typing import Any

from .tools import truncate

DIR = Path.home() / ".rig" / "sessions"

Meta = dict[str, Any]


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
    text_path.write_text(content)
    now = time.time()
    _write_meta(meta_path, {
        "title": title or "",
        "kind": kind,
        "created": now,
        "updated": now,
        "char_count": len(content),
    })
    return artifact_id


def fetch(session_id: str, artifact_id: str, offset: int = 0, limit: int = 4000) -> str:
    text_path, meta_path = _paths(session_id, artifact_id)
    if not text_path.exists():
        return f"error: no artifact {artifact_id!r} in session {session_id!r}"
    content = text_path.read_text()
    return content[offset:offset + limit]


def update(session_id: str, artifact_id: str, content: str) -> str:
    text_path, meta_path = _paths(session_id, artifact_id)
    if not meta_path.exists():
        return f"error: no artifact {artifact_id!r} in session {session_id!r}"
    text_path.write_text(content)
    meta = json.loads(meta_path.read_text())
    meta["char_count"] = len(content)
    meta["updated"] = time.time()
    _write_meta(meta_path, meta)
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


def offload_if_large(text: str, session_id: str, threshold: int = 4000, preview_limit: int = 1200) -> str:
    if len(text) <= threshold:
        return text
    artifact_id = save(session_id, text, kind="offload")
    return (f"{truncate(text, preview_limit)}\n"
            f"[full output: {len(text)} chars, saved as artifact {artifact_id} "
            f"— fetch_result({artifact_id}) to read more]")
