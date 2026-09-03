from dataclasses import dataclass


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


Event = (TextDelta | ToolStart | ToolEnd | Compacted | MemoryWrite |
         MemoryConsolidated | SubagentSpawned | SubagentDone | Error | Done)
