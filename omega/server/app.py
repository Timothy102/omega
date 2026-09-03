"""FastAPI app factory and process entrypoint for the D1 daemon: binds
127.0.0.1 only, no CORS, bearer-token auth (see auth.py) -- `omega serve`
(wired up separately in omega/__main__.py) and `python -m omega.server` both
end up calling `main()`."""
from __future__ import annotations

import contextlib
import importlib.metadata
import logging
import os
from collections.abc import AsyncIterator

from fastapi import FastAPI

from . import auth, tasks_api, terminals
from .manager import TaskManager, WorkerArgv, default_worker_argv

LOG_PATH = auth.SERVE_PATH.parent / "serve.log"


def _version() -> str:
    try:
        return importlib.metadata.version("omega-code")
    except importlib.metadata.PackageNotFoundError:
        return "0.0.0"


def create_app(port: int, worker_argv: WorkerArgv | None = None) -> FastAPI:
    """`worker_argv` overrides how a task's child worker process is launched
    -- the real daemon never passes it (defaults to spawning
    `python -m omega.server.worker`); tests point it at a scripted stub."""
    token = auth.generate_token()

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        auth.write_serve_info(auth.ServeInfo(port=port, token=token, pid=os.getpid()))
        try:
            yield
        finally:
            await app.state.tasks_manager.shutdown_all()
            app.state.terminals.kill_all()
            auth.remove_serve_info()

    app = FastAPI(title="omega D1 daemon", lifespan=lifespan)
    app.state.token = token
    app.state.version = _version()
    app.state.tasks_manager = TaskManager(worker_argv or default_worker_argv)
    app.state.terminals = terminals.TerminalManager()
    app.include_router(tasks_api.router)
    app.include_router(terminals.router)
    return app


def _configure_logging() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(LOG_PATH)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    # "uvicorn.error"/"uvicorn.access" propagate up to "uvicorn" by default --
    # attaching the handler to all three would log every line twice.
    for name in ("uvicorn", "omega.server"):
        logger = logging.getLogger(name)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)


def main(port: int = 7777) -> None:
    import uvicorn

    _configure_logging()
    app = create_app(port)
    uvicorn.run(app, host="127.0.0.1", port=port, log_config=None)
