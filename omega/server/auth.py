"""Bearer-token auth for the D1 daemon. The token is minted once and written
to `~/.omega/serve.json` (0600) by `app.main()` on startup, so the Mac app can
discover a running daemon (port, token, pid) and authenticate against it --
see Phase 9 of the plan. Bound to 127.0.0.1 only; this token is the only
access control, not a substitute for network isolation."""
from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException, Request, WebSocket

SERVE_PATH = Path.home() / ".omega" / "serve.json"


@dataclass(frozen=True)
class ServeInfo:
    port: int
    token: str
    pid: int


def generate_token() -> str:
    return secrets.token_hex(32)


def write_serve_info(info: ServeInfo) -> None:
    SERVE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = SERVE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps({"port": info.port, "token": info.token, "pid": info.pid}))
    tmp.replace(SERVE_PATH)
    os.chmod(SERVE_PATH, 0o600)


def remove_serve_info() -> None:
    SERVE_PATH.unlink(missing_ok=True)


def read_serve_info() -> ServeInfo | None:
    if not SERVE_PATH.exists():
        return None
    try:
        raw = json.loads(SERVE_PATH.read_text())
        return ServeInfo(port=int(raw["port"]), token=str(raw["token"]), pid=int(raw["pid"]))
    except (OSError, json.JSONDecodeError, KeyError, ValueError):
        return None


def _bearer(header: str) -> str | None:
    return header[7:] if header.lower().startswith("bearer ") else None


def require_token(request: Request) -> None:
    """FastAPI dependency for every REST route -- raises 401 on a missing or
    wrong token instead of letting the route run."""
    expected: str = request.app.state.token
    provided = _bearer(request.headers.get("authorization", ""))
    if provided is None or not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="unauthorized")


def check_ws_token(websocket: WebSocket) -> bool:
    """WebSocket routes call this themselves (before `accept()`) rather than
    using it as a Depends(): raising out of a dependency during the WS
    handshake closes the socket with an opaque 500, whereas the caller can
    close(code=1008) with an explanation."""
    expected: str = websocket.app.state.token
    provided = (_bearer(websocket.headers.get("authorization", ""))
               or websocket.query_params.get("token"))
    return provided is not None and secrets.compare_digest(provided, expected)
