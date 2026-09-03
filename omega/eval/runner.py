import asyncio
import os
import secrets
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from .. import events, loop, tools
from ..config import Config, Role
from ..session import Message
from .manifest import build_manifest
from .prices import estimate_cost
from .report import RunResult
from .tasks import Task

_CHECK_TIMEOUT_S = 300

# `tools._bash` resolves its working directory via `os.getcwd()`, which is
# process-wide -- two runs executing tools concurrently in different
# worktrees would race on which directory a bash call actually lands in.
# `--jobs` still bounds how many runs are in flight (workspace prep/cleanup
# and the model's own network waits overlap fine); this lock only serializes
# the tool-executing portion of each run, which is the part that touches cwd.
_TOOL_LOCK = asyncio.Lock()


@dataclass(frozen=True)
class Workspace:
    path: Path
    is_git_worktree: bool
    source_repo: Path


def resolve_models(cfg: Config, models_arg: str | None) -> list[tuple[str, Role]]:
    """`--models a,b,c` resolves each alias against the catalog; with no
    `--models`, eval runs against whatever `main` currently points at."""
    if not models_arg:
        role = cfg.role("main")
        return [(role.alias or "main", role)]
    out: list[tuple[str, Role]] = []
    for raw in models_arg.split(","):
        alias = cfg.resolve_alias(raw.strip())
        out.append((alias, cfg.model(alias)))
    return out


async def _is_git_repo(repo: Path) -> bool:
    proc = await asyncio.create_subprocess_exec(
        "git", "-C", str(repo), "rev-parse", "--is-inside-work-tree",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    await proc.communicate()
    return proc.returncode == 0


async def prepare_workspace(task: Task, work_root: Path) -> Workspace:
    repo = Path(task.repo).expanduser().resolve()
    work_root.mkdir(parents=True, exist_ok=True)
    dest = work_root / f"{task.name.replace(' ', '_')}-{secrets.token_hex(3)}"
    is_git = await _is_git_repo(repo)
    if is_git:
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", str(repo), "worktree", "add", "--detach", str(dest), "HEAD",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        _out, err = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"git worktree add failed: {err.decode(errors='replace')[:500]}")
    else:
        shutil.copytree(repo, dest)

    if task.setup:
        proc = await asyncio.create_subprocess_shell(
            task.setup, cwd=str(dest),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        _out, err = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"setup command failed ({proc.returncode}): "
                               f"{err.decode(errors='replace')[:2000]}")
    return Workspace(path=dest, is_git_worktree=is_git, source_repo=repo)


async def cleanup_workspace(ws: Workspace) -> None:
    if ws.is_git_worktree:
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", str(ws.source_repo), "worktree", "remove", "--force", str(ws.path),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await proc.communicate()
    else:
        shutil.rmtree(ws.path, ignore_errors=True)


async def _run_check(check: str, cwd: Path) -> tuple[bool, str]:
    proc = await asyncio.create_subprocess_shell(
        check, cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=_CHECK_TIMEOUT_S)
    except TimeoutError:
        proc.kill()
        await proc.communicate()
        return False, f"check timed out after {_CHECK_TIMEOUT_S}s"
    return proc.returncode == 0, out.decode(errors="replace")[-4000:]


async def run_one(cfg: Config, task: Task, label: str, role: Role, ws: Workspace,
                  repeat: int = 0) -> RunResult:
    """Runs one task against one model in an already-prepared workspace.
    Consumes `loop.run_agent` exactly as the CLI does -- `--yolo` semantics
    (no CONFIRM/ASK_USER), events collected via `emit` instead of rendered."""
    system, tool_names = loop.MODES[task.mode]
    system = f"{system}\n{loop.UNTRUSTED_NOTE}"
    schemas = tools.schemas(tool_names)
    history: list[Message] = [{"role": "user", "content": task.prompt}]
    initial_history = list(history)
    collected: list[events.Event] = []
    error: str | None = None
    result_text = ""

    started = time.monotonic()
    prev_cwd = os.getcwd()
    # tools.SESSION_ID/CONFIRM/ASK_USER/TAINTED are process globals `tools.run`
    # reads directly -- this run isn't the only thing that may touch them
    # (a caller embedding the eval harness in a live session, or -- in
    # tests -- an unrelated fixture), so snapshot and restore them rather
    # than assuming None/False is always the right thing to leave behind.
    prev_session_id, prev_confirm, prev_ask_user, prev_tainted = (
        tools.SESSION_ID, tools.CONFIRM, tools.ASK_USER, tools.TAINTED)
    async with _TOOL_LOCK:
        tools.SESSION_ID = f"eval-{task.name}-{label}-{repeat}-{secrets.token_hex(3)}"
        tools.CONFIRM = None
        tools.ASK_USER = None
        tools.set_tainted(False)
        os.chdir(ws.path)
        try:
            result_text = await asyncio.wait_for(
                loop.run_agent(cfg, "main" if task.mode == "build" else "plan",
                               system, history, tool_names, emit=collected.append, role=role),
                timeout=task.timeout_s)
        except TimeoutError:
            error = f"timed out after {task.timeout_s}s"
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
        finally:
            os.chdir(prev_cwd)
            tools.SESSION_ID, tools.CONFIRM, tools.ASK_USER = (
                prev_session_id, prev_confirm, prev_ask_user)
            tools.set_tainted(prev_tainted)
    wall_time_s = time.monotonic() - started

    passed, check_output = False, ""
    if error is None:
        (ws.path / "TRANSCRIPT.md").write_text(result_text)
        passed, check_output = await _run_check(task.check, ws.path)
    else:
        check_output = error

    turns = sum(1 for ev in collected if isinstance(ev, events.Phase) and ev.state == "waiting")
    tool_calls: dict[str, int] = {}
    for ev in collected:
        if isinstance(ev, events.ToolStart):
            tool_calls[ev.name] = tool_calls.get(ev.name, 0) + 1
    usage_events = [ev for ev in collected if isinstance(ev, events.Usage)]
    tokens_in = sum(ev.prompt_tokens for ev in usage_events)
    tokens_out = sum(ev.completion_tokens for ev in usage_events)
    cache_total = sum(getattr(ev, "cached_tokens", None) or getattr(ev, "cache_read", None) or 0
                      for ev in usage_events)
    cache_hit_ratio = (cache_total / tokens_in) if tokens_in else None
    manifest = build_manifest(collected, system, schemas, initial_history, history)

    return RunResult(task=task.name, model=label, repeat=repeat, passed=passed, turns=turns,
                     tool_calls=tool_calls, tokens_in=tokens_in, tokens_out=tokens_out,
                     cost_usd=estimate_cost(label, tokens_in, tokens_out),
                     wall_time_s=wall_time_s, cache_hit_ratio=cache_hit_ratio,
                     error=error, check_output=check_output, manifest=manifest)


async def run_suite(cfg: Config, tasks: list[Task], roles: list[tuple[str, Role]],
                    repeat: int = 1, jobs: int = 1,
                    work_root: Path | None = None) -> list[RunResult]:
    owns_root = work_root is None
    work_root = work_root or Path(tempfile.mkdtemp(prefix="omega-eval-"))
    sem = asyncio.Semaphore(max(1, jobs))

    async def _one(task: Task, label: str, role: Role, i: int) -> RunResult:
        async with sem:
            ws = await prepare_workspace(task, work_root)
            try:
                return await run_one(cfg, task, label, role, ws, repeat=i)
            finally:
                await cleanup_workspace(ws)

    coros = [_one(task, label, role, i)
            for task in tasks for label, role in roles for i in range(repeat)]
    try:
        return list(await asyncio.gather(*coros))
    finally:
        if owns_root:
            shutil.rmtree(work_root, ignore_errors=True)
