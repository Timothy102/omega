from dataclasses import dataclass
from typing import Literal, TypedDict


class Option(TypedDict, total=False):
    """An `ask_user` choice, as passed to `tools.ASK_USER` and rendered by both UIs."""
    label: str
    description: str


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class ToolStart:
    call_id: str
    name: str
    args_preview: str
    subagent_id: str | None = None
    tier: str | None = None


@dataclass(frozen=True)
class ToolEnd:
    call_id: str
    name: str
    result_preview: str
    duration_s: float
    offloaded: bool
    artifact_id: str | None = None
    result_chars: int = 0
    outcome: str = ""


@dataclass(frozen=True)
class Compacted:
    note: str


@dataclass(frozen=True)
class MemoryWrite:
    node_id: str
    type: str
    title: str
    scope: str


@dataclass(frozen=True)
class MemoryConsolidated:
    summary: str


@dataclass(frozen=True)
class SubagentSpawned:
    subagent_id: str
    tier: str
    task_preview: str


@dataclass(frozen=True)
class SubagentDone:
    subagent_id: str
    summary_preview: str


@dataclass(frozen=True)
class Error:
    message: str


@dataclass(frozen=True)
class Done:
    text: str


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int
    completion_tokens: int
    used: int
    limit: int
    cache_read: int = 0
    cache_write: int = 0


@dataclass(frozen=True)
class Fallback:
    from_model: str
    to_model: str
    reason: str


@dataclass(frozen=True)
class ModelUsed:
    alias: str | None
    model: str
    provider: str


@dataclass(frozen=True)
class Phase:
    state: Literal["waiting", "thinking", "streaming", "tools", "idle"]


@dataclass(frozen=True)
class Checkpoint:
    """A working-tree snapshot taken before a BUILD-mode turn -- see checkpoint.py."""
    turn: int
    id: str


@dataclass(frozen=True)
class Verified:
    """One end-of-turn verification pass (possibly a retry) -- see verify.py."""
    results_summary: str
    ok: bool


@dataclass(frozen=True)
class JobStarted:
    """A `bash(..., background=True)` job began running -- see tools.py."""
    id: str
    command: str


@dataclass(frozen=True)
class JobFinished:
    id: str
    exit_code: int


Event = (TextDelta | ToolStart | ToolEnd | Compacted | MemoryWrite |
         MemoryConsolidated | SubagentSpawned | SubagentDone | Error | Done | Usage | ModelUsed | Phase |
         Fallback | Checkpoint | Verified | JobStarted | JobFinished)
