"""The trajectory ledger: a deterministic, non-LLM record of every tool call
made this session, derived by walking `history` rather than kept as separate
state -- that way it survives session resume for free and can never drift
from what actually happened.

Two consumers:
  - `render()` -- the live "## Recent actions" block `loop.run_turn` injects
    into the volatile half of the system prompt every round.
  - `compaction_lines()` -- the full-fidelity, uncapped-per-field lines
    `compact.py` folds into its deterministic ledger for a dropped range.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from . import tools
from .session import Message
from .ui import format

MAX_ENTRIES = 50
ACTION_LINE_CAP = 25
BLOCK_MAX_CHARS = 2000
ARGS_DIGEST_CHARS = 64
SUMMARY_CHARS = 120

_DEDUPE_NOTE = ("If an identical call already appears above, do not re-run it "
                "-- call fetch_result on its recorded artifact instead.")


@dataclass(frozen=True)
class Entry:
    tool: str
    args_digest: str
    summary: str
    artifact_id: str | None
    call_id: str


@dataclass(frozen=True)
class _Raw:
    tool: str
    args: dict[str, Any]
    result: str
    artifact_id: str | None
    call_id: str


def _flatten(text: str) -> str:
    return " ".join(text.split())


def _cap(text: str, limit: int) -> str:
    text = _flatten(text)
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _describe_args(name: str, args: dict[str, Any]) -> str:
    try:
        described = format.describe_call(name, args)
    except Exception:
        described = ""
    if described.startswith(name):
        described = described[len(name):].strip()
    if not described:
        described = json.dumps(args, default=str)
    return _flatten(described)


def _tool_calls_by_id(history: list[Message]) -> dict[str, tuple[str, str]]:
    out: dict[str, tuple[str, str]] = {}
    for m in history:
        if m.get("role") != "assistant":
            continue
        for tc in m.get("tool_calls") or []:
            fn = tc.get("function") or {}
            out[tc["id"]] = (fn.get("name", ""), fn.get("arguments", "{}"))
    return out


def _raw_entries(history: list[Message]) -> list[_Raw]:
    calls = _tool_calls_by_id(history)
    out: list[_Raw] = []
    for m in history:
        if m.get("role") != "tool":
            continue
        call_id = str(m.get("tool_call_id") or "")
        name, args_json = calls.get(call_id, ("", "{}"))
        if not name:
            continue
        try:
            args = dict(json.loads(args_json or "{}"))
        except json.JSONDecodeError:
            args = {}
        text = str(m.get("content") or "")
        _offloaded, artifact_id = tools.offload_info(text)
        out.append(_Raw(tool=name, args=args, result=text,
                        artifact_id=artifact_id, call_id=call_id))
    return out


def build(history: list[Message]) -> list[Entry]:
    """The last `MAX_ENTRIES` tool calls this session, oldest dropped."""
    raws = _raw_entries(history)[-MAX_ENTRIES:]
    return [Entry(tool=r.tool,
                  args_digest=_cap(_describe_args(r.tool, r.args), ARGS_DIGEST_CHARS),
                  summary=_cap(r.result, SUMMARY_CHARS),
                  artifact_id=r.artifact_id, call_id=r.call_id)
            for r in raws]


def _line(tool: str, args_text: str, summary_text: str, artifact_id: str | None) -> str:
    suffix = f" [artifact {artifact_id}]" if artifact_id else ""
    return f"{tool}({args_text}) → {summary_text}{suffix}"


def render(history: list[Message]) -> str:
    """`## Recent actions` block for the volatile half of the system prompt.
    Empty string when there is nothing to show yet."""
    entries = build(history)[-ACTION_LINE_CAP:]
    if not entries:
        return ""
    header = "## Recent actions\n"
    footer = "\n\n" + _DEDUPE_NOTE
    lines = [f"- {_line(e.tool, e.args_digest, e.summary, e.artifact_id)}" for e in entries]
    # Oldest-first drop keeps the most recent (most relevant) actions when the
    # block would otherwise blow the char budget.
    while lines and len(header) + len("\n".join(lines)) + len(footer) > BLOCK_MAX_CHARS:
        lines.pop(0)
    if not lines:
        return ""
    return header + "\n".join(lines) + footer


def compaction_lines(history: list[Message], entry_char_cap: int) -> list[str]:
    """Every tool call in `history` (typically the dropped range of a
    compaction), one deterministic line each -- no LLM involved, so it cannot
    hallucinate. Unlike `render()`, nothing here is capped to the live
    ledger's tight per-field limits; only the whole line is capped."""
    lines = []
    for r in _raw_entries(history):
        args_text = _describe_args(r.tool, r.args)
        line = _line(r.tool, args_text, _flatten(r.result), r.artifact_id)
        lines.append(_cap(line, entry_char_cap) if len(line) > entry_char_cap else line)
    return lines
