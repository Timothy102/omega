import json
import sys
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from omega import artifacts, checkpoint, session, tasks
from omega.server import auth
from omega.server.app import create_app

STUB_PATH = Path(__file__).parent / "_stub_worker.py"


def _stub_worker_argv(task):
    return [sys.executable, str(STUB_PATH), task.id]


@pytest.fixture(autouse=True)
def isolate(tmp_path_factory, monkeypatch):
    monkeypatch.setattr(session, "DIR", tmp_path_factory.mktemp("omega-sessions"))
    monkeypatch.setattr(artifacts, "DIR", session.DIR)
    monkeypatch.setattr(checkpoint, "DIR", session.DIR)
    monkeypatch.setattr(tasks, "DIR", tmp_path_factory.mktemp("omega-tasks"))
    monkeypatch.setattr(tasks, "WORKTREES_DIR", tmp_path_factory.mktemp("omega-worktrees"))
    monkeypatch.setattr(auth, "SERVE_PATH", tmp_path_factory.mktemp("omega-home") / "serve.json")
    # A fast, predictable shell -- the user's real login shell may print a
    # slow-loading MOTD/prompt theme that would make output assertions flaky.
    monkeypatch.setenv("SHELL", "/bin/sh")
    yield


@pytest.fixture
def client():
    app = create_app(port=7777, worker_argv=_stub_worker_argv)
    with TestClient(app) as c:
        c.headers.update({"Authorization": f"Bearer {app.state.token}"})
        yield c


def _read_until(ws, marker: str, limit: int = 200) -> bytes:
    buf = b""
    for _ in range(limit):
        buf += ws.receive_bytes()
        if marker.encode() in buf:
            return buf
    raise AssertionError(f"marker {marker!r} never seen; got {buf!r}")


def test_create_list_delete_terminal(client, tmp_path):
    r = client.post("/api/terminals", json={"cwd": str(tmp_path)})
    assert r.status_code == 200
    term = r.json()
    assert term["cwd"] == str(tmp_path)
    assert term["pid"] > 0

    listed = client.get("/api/terminals").json()
    assert any(t["id"] == term["id"] for t in listed)

    assert client.delete(f"/api/terminals/{term['id']}").json() == {"deleted": True}
    assert client.delete(f"/api/terminals/{term['id']}").status_code == 404
    assert term["id"] not in {t["id"] for t in client.get("/api/terminals").json()}


def test_create_rejects_missing_directory(client, tmp_path):
    r = client.post("/api/terminals", json={"cwd": str(tmp_path / "nope")})
    assert r.status_code == 400


def test_terminal_ws_echoes_shell_output(client, tmp_path):
    term = client.post("/api/terminals", json={"cwd": str(tmp_path)}).json()
    token = client.headers["Authorization"][7:]
    with client.websocket_connect(f"/ws/terminals/{term['id']}?token={token}") as ws:
        ws.send_bytes(b"echo hello_omega_marker\n")
        output = _read_until(ws, "hello_omega_marker")
    assert b"hello_omega_marker" in output


def test_terminal_ws_resize(client, tmp_path):
    term = client.post("/api/terminals", json={"cwd": str(tmp_path)}).json()
    token = client.headers["Authorization"][7:]
    with client.websocket_connect(f"/ws/terminals/{term['id']}?token={token}") as ws:
        ws.send_text(json.dumps({"resize": [120, 40]}))
        # Proves the socket and PTY are still alive after a resize frame --
        # a bad ioctl would kill the pump task and this would time out.
        ws.send_bytes(b"echo still_alive_marker\n")
        output = _read_until(ws, "still_alive_marker")
    assert b"still_alive_marker" in output


def test_terminal_ws_rejects_bad_token(tmp_path):
    app = create_app(port=7777, worker_argv=_stub_worker_argv)
    with TestClient(app) as anon:
        term = anon.post("/api/terminals", json={"cwd": str(tmp_path)},
                         headers={"Authorization": f"Bearer {app.state.token}"}).json()
        with pytest.raises(Exception):
            with anon.websocket_connect(f"/ws/terminals/{term['id']}?token=nope"):
                pass


def test_unknown_terminal_ws_closes(client):
    with pytest.raises(Exception):
        with client.websocket_connect(
                f"/ws/terminals/no-such-id?token={client.headers['Authorization'][7:]}"):
            pass


def test_overview_ws_pushes_terminal_list(client, tmp_path):
    token = client.headers["Authorization"][7:]
    with client.websocket_connect(f"/ws/overview?token={token}") as ws:
        term = client.post("/api/terminals", json={"cwd": str(tmp_path)}).json()
        for _ in range(20):
            raw = ws.receive_text()
            obj = json.loads(raw)
            if obj.get("type") == "terminals":
                assert any(t["id"] == term["id"] for t in obj["terminals"])
                break
        else:
            raise AssertionError("no terminals overview push seen")
