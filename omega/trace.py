"""Per-session JSONL event trace: every `events.Event` a session emits is
appended as one line to `~/.omega/sessions/<id>/trace.jsonl`, independent of
what either UI chooses to render -- so `omega trace <id>` can replay a
session's full timeline (including events a UI drops on the floor) after the
fact, and price it against `omega.eval.prices`."""
from __future__ import annotations

import json
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from . import events, session

_TOOL_TYPES = {"ToolStart", "ToolEnd"}

_GLYPH = {
    "ToolStart": "●", "ToolEnd": "└", "SubagentSpawned": "●", "SubagentDone": "✓",
    "Compacted": "⏺", "MemoryWrite": "◆", "MemoryConsolidated": "◆", "Error": "✗",
    "Done": "●", "Fallback": "⇄", "ModelUsed": "◇", "Usage": "◇", "Phase": "·",
    "Checkpoint": "⎘", "Verified": "✓", "JobStarted": "⟳", "JobFinished": "✓",
}


def trace_path(session_id: str) -> Path:
    return session.DIR / session_id / "trace.jsonl"


def append(session_id: str, ev: events.Event, turn: int) -> None:
    """Fire-and-forget: observability must never be able to break a turn, so
    any failure here (disk full, permissions, an event with no dataclass
    fields) is swallowed rather than raised."""
    try:
        if not is_dataclass(ev):
            return
        path = trace_path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        line = {"t": time.time(), "turn": turn, "type": type(ev).__name__, **asdict(ev)}
        with path.open("a") as f:
            f.write(json.dumps(line) + "\n")
    except Exception:
        pass


def _read(session_id: str) -> list[dict[str, Any]]:
    """Tolerates a truncated trailing line the same way `session._read_jsonl`
    does -- appends are not fsynced, so a crash mid-write leaves one."""
    path = trace_path(session_id)
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            break
    return rows


def _summary(row: dict[str, Any]) -> str:
    t = row.get("type", "")
    if t == "ToolStart":
        return f"{row.get('name', '')}  {row.get('args_preview', '')}"
    if t == "ToolEnd":
        dur = float(row.get("duration_s") or 0.0)
        return f"{row.get('name', '')}  {row.get('outcome', '')}  ({dur:.2f}s)".strip()
    if t == "Done":
        return " ".join(str(row.get("text", "")).split())[:80]
    if t == "Error":
        message = str(row.get("message", ""))
        return message.splitlines()[0] if message else ""
    if t == "SubagentSpawned":
        return f"subagent({row.get('tier', '')})  {row.get('task_preview', '')}"
    if t == "SubagentDone":
        return f"subagent {row.get('subagent_id', '')} done"
    if t == "Compacted":
        return str(row.get("note", ""))
    if t == "MemoryWrite":
        return f"{row.get('type', '')} '{row.get('title', '')}' ({row.get('scope', '')})"
    if t == "MemoryConsolidated":
        return str(row.get("summary", ""))
    if t == "ModelUsed":
        alias = row.get("alias") or ""
        return f"{alias} {row.get('model', '')}".strip()
    if t == "Usage":
        return f"{row.get('prompt_tokens', 0)} in / {row.get('completion_tokens', 0)} out"
    if t == "Fallback":
        return f"{row.get('from_model', '')} -> {row.get('to_model', '')}: {row.get('reason', '')}"
    if t == "Phase":
        return str(row.get("state", ""))
    if t == "Checkpoint":
        return f"checkpoint {row.get('id', '')}"
    if t == "Verified":
        return str(row.get("results_summary", ""))
    if t == "JobStarted":
        return f"{row.get('id', '')}  {row.get('command', '')}"
    if t == "JobFinished":
        return f"{row.get('id', '')}  exit {row.get('exit_code', '')}"
    return ""


def _turn_totals(rows: list[dict[str, Any]]) -> dict[int, tuple[int, int, float | None]]:
    """`turn -> (tokens_in, tokens_out, cost_usd)`, summed from `Usage` rows
    and priced against whichever alias the last `ModelUsed` row announced --
    `cost_usd` is None once that model is absent from `eval.prices.PRICES`."""
    from .eval import prices

    totals: dict[int, tuple[int, int, float | None]] = {}
    alias: str | None = None
    for row in rows:
        if row.get("type") == "ModelUsed":
            alias = row.get("alias")
        elif row.get("type") == "Usage":
            turn = int(row.get("turn", 0))
            prev_in, prev_out, _ = totals.get(turn, (0, 0, None))
            tin = prev_in + int(row.get("prompt_tokens", 0))
            tout = prev_out + int(row.get("completion_tokens", 0))
            cost = prices.estimate_cost(alias, tin, tout) if alias else None
            totals[turn] = (tin, tout, cost)
    return totals


def render_timeline(session_id: str, *, tools_only: bool = False, raw_json: bool = False) -> str:
    rows = _read(session_id)
    if raw_json:
        return "\n".join(json.dumps(r) for r in rows)
    if not rows:
        return f"no trace for session {session_id}"

    totals = _turn_totals(rows)
    view = [r for r in rows if r.get("type") in _TOOL_TYPES] if tools_only else rows
    t0 = float(rows[0].get("t") or 0.0)

    lines = []
    for row in view:
        t = str(row.get("type", ""))
        if t == "TextDelta":
            continue
        offset = float(row.get("t") or t0) - t0
        turn = row.get("turn", 0)
        glyph = _GLYPH.get(t, "·")
        lines.append(f"+{offset:7.2f}s  turn {turn:<3} {glyph} {t:<18}{_summary(row)}")

    if totals:
        lines.append("")
        lines.append("per-turn totals:")
        for turn in sorted(totals):
            tin, tout, cost = totals[turn]
            cost_text = f"${cost:.4f}" if cost is not None else "unknown"
            lines.append(f"  turn {turn}: {tin} in / {tout} out tokens · {cost_text}")

    return "\n".join(lines)
