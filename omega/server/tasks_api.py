"""REST + WebSocket routes for tasks, plus the daemon-wide `/api/models`,
`/api/connections` and `/api/memory` lookups -- see Phase 9 of the plan for
the exact contract the SwiftUI app is built against."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect

from .. import artifacts, checkpoint, config, gitlog, mcp, session, tasks, trace
from ..memory import store
from . import auth
from .manager import TaskManager
from .models import (
    AnswerIn,
    ConfirmIn,
    ConnectionOut,
    HealthOut,
    MemoryHitOut,
    ModeIn,
    ModelCatalogEntry,
    ModelIn,
    PrCreateIn,
    PromptIn,
    TaskCreateIn,
    TaskDetailOut,
    TaskOut,
    UndoIn,
)

router = APIRouter(tags=["tasks"])


def _manager(request: Request) -> TaskManager:
    mgr: TaskManager = request.app.state.tasks_manager
    return mgr


def _get_task_or_404(task_id: str) -> tasks.Task:
    task = tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="no such task")
    return task


@router.get("/api/health")
async def health(request: Request) -> HealthOut:
    return HealthOut(version=request.app.state.version)


# -- tasks CRUD -----------------------------------------------------------

@router.get("/api/tasks", dependencies=[Depends(auth.require_token)])
async def list_tasks_route() -> list[TaskOut]:
    return [TaskOut.from_task(t) for t in tasks.list_tasks()]


@router.post("/api/tasks", dependencies=[Depends(auth.require_token)])
async def create_task(body: TaskCreateIn, request: Request) -> TaskOut:
    try:
        task = await asyncio.to_thread(
            tasks.create, body.repo, body.prompt, body.worktree, body.model, body.mode)
    except (ValueError, RuntimeError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    _manager(request).publish_overview_task(task)
    if body.prompt:
        await _manager(request).send_prompt(task, body.prompt)
        task = _get_task_or_404(task.id)
    return TaskOut.from_task(task)


@router.get("/api/tasks/{task_id}", dependencies=[Depends(auth.require_token)])
async def get_task(task_id: str) -> TaskDetailOut:
    task = _get_task_or_404(task_id)
    sess = session.load(task.id)
    return TaskDetailOut(**TaskOut.from_task(task).model_dump(), history=sess.history)


@router.delete("/api/tasks/{task_id}", dependencies=[Depends(auth.require_token)])
async def delete_task(task_id: str, request: Request,
                      delete_worktree: bool = Query(False)) -> dict[str, bool]:
    _get_task_or_404(task_id)
    await _manager(request).shutdown_task(task_id)
    ok = tasks.remove(task_id, delete_worktree=delete_worktree)
    return {"deleted": ok}


# -- turn control -----------------------------------------------------------

@router.post("/api/tasks/{task_id}/prompt", dependencies=[Depends(auth.require_token)])
async def send_prompt(task_id: str, body: PromptIn, request: Request) -> TaskOut:
    task = _get_task_or_404(task_id)
    if task.title == "(no prompt yet)":
        task = tasks.update(task_id, title=tasks.title_from_prompt(body.text)) or task
    await _manager(request).send_prompt(task, body.text)
    return TaskOut.from_task(_get_task_or_404(task_id))


@router.post("/api/tasks/{task_id}/cancel", dependencies=[Depends(auth.require_token)])
async def cancel_task(task_id: str, request: Request) -> dict[str, bool]:
    _get_task_or_404(task_id)
    ok = await _manager(request).cancel(task_id)
    return {"cancelled": ok}


@router.post("/api/tasks/{task_id}/answer", dependencies=[Depends(auth.require_token)])
async def answer_task(task_id: str, body: AnswerIn, request: Request) -> dict[str, bool]:
    _get_task_or_404(task_id)
    ok = await _manager(request).send_answer(task_id, body.request_id, body.answer)
    return {"sent": ok}


@router.post("/api/tasks/{task_id}/confirm", dependencies=[Depends(auth.require_token)])
async def confirm_task(task_id: str, body: ConfirmIn, request: Request) -> dict[str, bool]:
    _get_task_or_404(task_id)
    ok = await _manager(request).send_confirm(task_id, body.request_id, body.allow)
    return {"sent": ok}


@router.post("/api/tasks/{task_id}/model", dependencies=[Depends(auth.require_token)])
async def set_model(task_id: str, body: ModelIn, request: Request) -> TaskOut:
    task = _get_task_or_404(task_id)
    task = tasks.update(task_id, model=body.model) or task
    await _manager(request).set_model(task_id, body.model)
    return TaskOut.from_task(task)


@router.post("/api/tasks/{task_id}/mode", dependencies=[Depends(auth.require_token)])
async def set_mode(task_id: str, body: ModeIn, request: Request) -> TaskOut:
    task = _get_task_or_404(task_id)
    task = tasks.update(task_id, mode=body.mode) or task
    await _manager(request).set_mode(task_id, body.mode)
    return TaskOut.from_task(task)


# -- git / checkpoints --------------------------------------------------

@router.get("/api/tasks/{task_id}/git", dependencies=[Depends(auth.require_token)])
async def task_git(task_id: str) -> dict[str, Any]:
    task = _get_task_or_404(task_id)
    repos = await gitlog.discover_repos_async(Path(task.cwd), max_depth=0)
    if not repos:
        return {"branch": None, "dirty": False, "changes": []}
    repo = repos[0]
    changes = await gitlog.working_tree_async(repo)
    return {
        "branch": repo.branch, "dirty": repo.dirty,
        "changes": [{"path": c.path, "status": c.status, "added": c.added, "removed": c.removed}
                   for c in changes],
    }


@router.get("/api/tasks/{task_id}/diff", dependencies=[Depends(auth.require_token)])
async def task_diff(task_id: str, path: str | None = None) -> dict[str, str]:
    task = _get_task_or_404(task_id)
    if path:
        repos = await gitlog.discover_repos_async(Path(task.cwd), max_depth=0)
        text = await gitlog.diff_async(repos[0], path) if repos else ""
    else:
        text = await asyncio.to_thread(checkpoint.diff, task.id, None, task.cwd)
    return {"diff": text}


@router.post("/api/tasks/{task_id}/undo", dependencies=[Depends(auth.require_token)])
async def task_undo(task_id: str, body: UndoIn) -> dict[str, str]:
    task = _get_task_or_404(task_id)
    result = await asyncio.to_thread(checkpoint.undo, task.id, body.steps, task.cwd)
    return {"result": result}


# -- artifacts / trace / jobs --------------------------------------------

@router.get("/api/tasks/{task_id}/artifacts", dependencies=[Depends(auth.require_token)])
async def task_artifacts(task_id: str) -> list[dict[str, Any]]:
    task = _get_task_or_404(task_id)
    return artifacts.list_artifacts(task.id)


@router.get("/api/tasks/{task_id}/artifacts/{artifact_id}", dependencies=[Depends(auth.require_token)])
async def task_artifact(task_id: str, artifact_id: str,
                        offset: int = 0, limit: int = 0) -> dict[str, str]:
    task = _get_task_or_404(task_id)
    text = artifacts.fetch(task.id, artifact_id, offset, limit or artifacts.PAGE_CHARS)
    return {"content": text}


@router.get("/api/tasks/{task_id}/trace", dependencies=[Depends(auth.require_token)])
async def task_trace(task_id: str) -> dict[str, str]:
    task = _get_task_or_404(task_id)
    return {"trace": trace.render_timeline(task.id, raw_json=True)}


@router.get("/api/tasks/{task_id}/jobs", dependencies=[Depends(auth.require_token)])
async def task_jobs(task_id: str, request: Request) -> list[dict[str, Any]]:
    _get_task_or_404(task_id)
    return await _manager(request).get_jobs(task_id)


# -- pull requests --------------------------------------------------------

@router.post("/api/tasks/{task_id}/pr", dependencies=[Depends(auth.require_token)])
async def create_pr(task_id: str, body: PrCreateIn) -> dict[str, Any]:
    task = _get_task_or_404(task_id)
    if not task.branch:
        raise HTTPException(status_code=400, detail="task has no branch (not a worktree task)")
    args = ["gh", "pr", "create", "--head", task.branch,
           "--title", body.title or task.title, "--body", body.body or ""]
    if body.draft:
        args.append("--draft")
    proc = await asyncio.create_subprocess_exec(
        *args, cwd=task.repo, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise HTTPException(status_code=502, detail=stderr.decode(errors="replace")[:500])
    info = await asyncio.to_thread(tasks.pr_info, task)
    if info is not None:
        tasks.update(task_id, pr=info)
    return {"url": stdout.decode().strip(), "pr": info}


# -- daemon-wide lookups --------------------------------------------------

@router.get("/api/models", dependencies=[Depends(auth.require_token)])
async def list_models() -> list[ModelCatalogEntry]:
    cfg = config.load()
    return [ModelCatalogEntry(alias=alias, model=m.model, provider=m.provider, context=m.context)
           for alias, m in sorted(cfg.models.items())]


@router.get("/api/connections", dependencies=[Depends(auth.require_token)])
async def list_connections() -> list[ConnectionOut]:
    return [ConnectionOut(name=name, enabled=st.enabled, state=st.state, tools=st.tools,
                          error=st.error, last_used=st.last_used)
           for name, st in sorted(mcp.status().items())]


@router.post("/api/connections/{name}/connect", dependencies=[Depends(auth.require_token)])
async def connect_connection(name: str) -> ConnectionOut:
    st = await mcp.connect(name)
    return ConnectionOut(name=st.name, enabled=st.enabled, state=st.state, tools=st.tools,
                         error=st.error, last_used=st.last_used)


@router.get("/api/memory", dependencies=[Depends(auth.require_token)])
async def query_memory(q: str, scope: str = "both", type: str | None = None,
                       limit: int = 8, task_id: str | None = None) -> list[MemoryHitOut]:
    cwd = _get_task_or_404(task_id).cwd if task_id else None
    scopes = ["project", "global"] if scope == "both" else [scope]
    hits: list[MemoryHitOut] = []
    for sc in scopes:
        if sc == "project" and not store.db_exists("project", cwd):
            continue
        try:
            rows = await asyncio.to_thread(store.search, sc, q, type, limit, False, cwd)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        hits.extend(MemoryHitOut(id=r["id"], scope=sc, type=r["type"], title=r["title"],
                                 body=r["body"], confidence=r["confidence"],
                                 importance=r["importance"]) for r in rows)
    return hits[:limit]


# -- websockets -----------------------------------------------------------

async def _relay_events(websocket: WebSocket, queue: asyncio.Queue[str]) -> None:
    """Both `/ws/tasks/{id}` and `/ws/overview` are server-push-only from the
    app's side. A send on a connection the client has closed raises
    `WebSocketDisconnect` (uvicorn tears the ASGI channel down on the
    client's close frame), which the caller catches -- so the loop ends
    without a separate `receive()`-based watchdog."""
    while True:
        await websocket.send_text(await queue.get())


@router.websocket("/ws/tasks/{task_id}")
async def task_ws(websocket: WebSocket, task_id: str) -> None:
    if not auth.check_ws_token(websocket):
        await websocket.close(code=1008)
        return
    if tasks.get(task_id) is None:
        await websocket.close(code=1008, reason="no such task")
        return

    mgr: TaskManager = websocket.app.state.tasks_manager
    await websocket.accept()
    queue = mgr.subscribe_task(task_id)
    try:
        await _relay_events(websocket, queue)
    except WebSocketDisconnect:
        pass
    finally:
        mgr.unsubscribe_task(task_id, queue)


@router.websocket("/ws/overview")
async def overview_ws(websocket: WebSocket) -> None:
    if not auth.check_ws_token(websocket):
        await websocket.close(code=1008)
        return

    mgr: TaskManager = websocket.app.state.tasks_manager
    await websocket.accept()
    queue = mgr.subscribe_overview()
    try:
        await _relay_events(websocket, queue)
    except WebSocketDisconnect:
        pass
    finally:
        mgr.unsubscribe_overview(queue)
