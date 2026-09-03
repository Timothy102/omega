"""Session transcript -> Markdown, for `/export`. A pure function of
`history` (the same message list `session.Session` carries): user prompts as
`## › …`, assistant text verbatim, tool calls as a compact fenced list with
outcomes, and an offloaded result referenced by its artifact id rather than
re-embedded in full."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from . import session
from .session import Message
from .ui import format

_ARTIFACT_RE = re.compile(r"saved as artifact ([0-9a-f]+)")
_OUTCOME_CHARS = 120


def _tool_call_args(call: dict[str, Any]) -> dict[str, Any]:
    fn = call.get("function") or {}
    try:
        parsed = json.loads(fn.get("arguments") or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _outcome(result: str) -> str:
    m = _ARTIFACT_RE.search(result)
    if m:
        return f"[artifact {m.group(1)}]"
    flat = " ".join(result.split())
    return flat if len(flat) <= _OUTCOME_CHARS else flat[:_OUTCOME_CHARS - 1] + "…"


def _tool_calls_block(tool_calls: list[dict[str, Any]], results: dict[str, str]) -> str:
    lines = []
    for call in tool_calls:
        name = str((call.get("function") or {}).get("name", ""))
        args = _tool_call_args(call)
        result = results.get(str(call.get("id", "")), "")
        lines.append(f"{format.describe_call(name, args)}  →  {_outcome(result)}")
    return "```\n" + "\n".join(lines) + "\n```"


def to_markdown(history: list[Message], session_id: str = "") -> str:
    results: dict[str, str] = {
        str(m.get("tool_call_id", "")): str(m.get("content", ""))
        for m in history if m.get("role") == "tool"
    }

    parts: list[str] = [f"# omega session {session_id}"] if session_id else []
    for msg in history:
        role = msg.get("role")
        if role == "user":
            content = str(msg.get("content", ""))
            if content.startswith(session.RESUME_PREFIX):
                continue
            parts.append(f"## › {content}")
        elif role == "assistant":
            assistant_text = msg.get("content")
            if assistant_text:
                parts.append(str(assistant_text))
            tool_calls = msg.get("tool_calls")
            if tool_calls:
                parts.append(_tool_calls_block(tool_calls, results))
    return "\n\n".join(parts) + "\n"


def default_path(session_id: str) -> Path:
    return session.DIR / session_id / "transcript.md"


def write(history: list[Message], session_id: str, path: str | None = None) -> Path:
    out = Path(path).expanduser() if path else default_path(session_id)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(to_markdown(history, session_id))
    return out
