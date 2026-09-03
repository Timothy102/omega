"""Pydantic request/response schemas for the D1 daemon's REST API. Field
names here ARE the wire contract the SwiftUI app is built against -- see
Phase 9 of the plan; keep them in lockstep with that spec, not with whatever
is convenient internally (that's what `TaskOut.from_task` adapts)."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

from .. import tasks


class HealthOut(BaseModel):
    status: Literal["ok"] = "ok"
    version: str


class TaskCreateIn(BaseModel):
    repo: str
    prompt: str | None = None
    worktree: bool = True
    model: str | None = None
    mode: str = "build"


class TaskOut(BaseModel):
    id: str
    title: str
    repo: str
    cwd: str
    worktree: bool
    branch: str | None
    pr: dict[str, Any] | None
    model: str | None
    mode: str
    status: str
    phase: str
    created: float
    updated: float
    tokens_in: int
    tokens_out: int
    cost_usd: float | None
    elapsed_s: float

    @classmethod
    def from_task(cls, task: tasks.Task) -> TaskOut:
        return cls(**{f: getattr(task, f) for f in TaskOut.model_fields})


class TaskDetailOut(TaskOut):
    history: list[dict[str, Any]]


class PromptIn(BaseModel):
    text: str


class AnswerIn(BaseModel):
    request_id: str
    answer: str


class ConfirmIn(BaseModel):
    request_id: str
    allow: bool


class ModelIn(BaseModel):
    model: str


class ModeIn(BaseModel):
    mode: str


class PrCreateIn(BaseModel):
    title: str | None = None
    body: str | None = None
    draft: bool = False


class UndoIn(BaseModel):
    steps: int = 1


class TerminalCreateIn(BaseModel):
    task_id: str | None = None
    cwd: str | None = None


class TerminalOut(BaseModel):
    id: str
    task_id: str | None
    pid: int
    cwd: str
    created: float


class ModelCatalogEntry(BaseModel):
    alias: str
    model: str
    provider: str
    context: int


class ConnectionOut(BaseModel):
    name: str
    enabled: bool
    state: str
    tools: int
    error: str | None
    last_used: float | None


class MemoryHitOut(BaseModel):
    id: str
    scope: str
    type: str
    title: str
    body: str
    confidence: float
    importance: float
