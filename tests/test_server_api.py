import subprocess
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


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=10)


@pytest.fixture(autouse=True)
def isolate(tmp_path_factory, monkeypatch):
    monkeypatch.setattr(session, "DIR", tmp_path_factory.mktemp("omega-sessions"))
    monkeypatch.setattr(artifacts, "DIR", session.DIR)
    monkeypatch.setattr(checkpoint, "DIR", session.DIR)
    monkeypatch.setattr(tasks, "DIR", tmp_path_factory.mktemp("omega-tasks"))
    monkeypatch.setattr(tasks, "WORKTREES_DIR", tmp_path_factory.mktemp("omega-worktrees"))
    monkeypatch.setattr(auth, "SERVE_PATH", tmp_path_factory.mktemp("omega-home") / "serve.json")
    yield


@pytest.fixture
def repo(tmp_path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    assert _git("init", "-q", cwd=root).returncode == 0
    _git("config", "user.email", "a@b.com", cwd=root)
    _git("config", "user.name", "a", cwd=root)
    (root / "a.txt").write_text("hi\n")
    _git("add", ".", cwd=root)
    assert _git("commit", "-q", "-m", "init", cwd=root).returncode == 0
    return root


@pytest.fixture
def client():
    app = create_app(port=7777, worker_argv=_stub_worker_argv)
    with TestClient(app) as c:
        c.headers.update({"Authorization": f"Bearer {app.state.token}"})
        yield c


def _create_task(client, repo, **kwargs):
    body = {"repo": str(repo), "worktree": False, **kwargs}
    r = client.post("/api/tasks", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _drain_until(ws, predicate, limit=50):
    """Reads WS text frames (each one JSON event line) until `predicate`
    matches one, returning every event seen along the way."""
    seen = []
    for _ in range(limit):
        raw = ws.receive_text()
        import json
        obj = json.loads(raw)
        seen.append(obj)
        if predicate(obj):
            return seen
    raise AssertionError(f"predicate never matched; saw {seen}")


# -- health / auth --------------------------------------------------------

def test_health_needs_no_auth():
    app = create_app(port=7777, worker_argv=_stub_worker_argv)
    with TestClient(app) as c:
        r = c.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_missing_token_is_rejected():
    app = create_app(port=7777, worker_argv=_stub_worker_argv)
    with TestClient(app) as c:
        r = c.get("/api/tasks")
    assert r.status_code == 401


def test_wrong_token_is_rejected():
    app = create_app(port=7777, worker_argv=_stub_worker_argv)
    with TestClient(app) as c:
        r = c.get("/api/tasks", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_serve_json_written_and_removed(tmp_path_factory, monkeypatch):
    serve_path = tmp_path_factory.mktemp("home2") / "serve.json"
    monkeypatch.setattr(auth, "SERVE_PATH", serve_path)
    app = create_app(port=7777, worker_argv=_stub_worker_argv)
    with TestClient(app):
        assert serve_path.exists()
        info = auth.read_serve_info()
        assert info is not None
        assert info.port == 7777
    assert not serve_path.exists()


# -- task CRUD --------------------------------------------------------------

def test_create_list_get_task(client, repo):
    created = _create_task(client, repo)
    assert created["title"] == "(no prompt yet)"
    assert created["status"] == "idle"

    listed = client.get("/api/tasks").json()
    assert any(t["id"] == created["id"] for t in listed)

    detail = client.get(f"/api/tasks/{created['id']}").json()
    assert detail["history"] == []
    assert detail["id"] == created["id"]


def test_get_unknown_task_is_404(client):
    assert client.get("/api/tasks/nope").status_code == 404


def test_create_with_prompt_starts_a_turn_immediately(client, repo):
    created = _create_task(client, repo, prompt="fix the thing")
    assert created["title"] == "fix the thing"
    assert created["status"] == "running"


# -- prompt -> events over WS, in order ------------------------------------

def test_prompt_streams_events_in_order(client, repo):
    task = _create_task(client, repo)
    with client.websocket_connect(f"/ws/tasks/{task['id']}?token={client.headers['Authorization'][7:]}") as ws:
        r = client.post(f"/api/tasks/{task['id']}/prompt", json={"text": "hello"})
        assert r.status_code == 200

        events = _drain_until(ws, lambda o: o.get("type") == "Done")
        types = [e["type"] for e in events]
        assert types == ["worker_ready", "Phase", "ModelUsed", "ToolStart",
                         "ToolEnd", "Usage", "Done"]
        assert events[-1]["text"] == "echo: hello"

    final = client.get(f"/api/tasks/{task['id']}").json()
    assert final["status"] == "done"
    assert final["tokens_in"] == 10
    assert final["tokens_out"] == 5


def test_prompt_backfills_title_when_task_had_none(client, repo):
    task = _create_task(client, repo)
    assert task["title"] == "(no prompt yet)"
    with client.websocket_connect(f"/ws/tasks/{task['id']}?token={client.headers['Authorization'][7:]}") as ws:
        client.post(f"/api/tasks/{task['id']}/prompt", json={"text": "a new title"})
        _drain_until(ws, lambda o: o.get("type") == "Done")
    assert client.get(f"/api/tasks/{task['id']}").json()["title"] == "a new title"


# -- ask_user round trip -----------------------------------------------------

def test_ask_user_round_trip(client, repo):
    task = _create_task(client, repo)
    with client.websocket_connect(f"/ws/tasks/{task['id']}?token={client.headers['Authorization'][7:]}") as ws:
        client.post(f"/api/tasks/{task['id']}/prompt", json={"text": "ASK"})
        events = _drain_until(ws, lambda o: o.get("type") == "ask_user_request")
        request = events[-1]
        assert request["question"] == "pick one"
        assert request["options"] == [{"label": "a"}, {"label": "b"}]

        mid = client.get(f"/api/tasks/{task['id']}").json()
        assert mid["status"] == "waiting_input"

        r = client.post(f"/api/tasks/{task['id']}/answer",
                        json={"request_id": request["request_id"], "answer": "b"})
        assert r.json() == {"sent": True}

        done = _drain_until(ws, lambda o: o.get("type") == "Done")[-1]
        assert done["text"] == "answered: b"


# -- confirm round trip -------------------------------------------------

def test_confirm_round_trip(client, repo):
    task = _create_task(client, repo)
    with client.websocket_connect(f"/ws/tasks/{task['id']}?token={client.headers['Authorization'][7:]}") as ws:
        client.post(f"/api/tasks/{task['id']}/prompt", json={"text": "CONFIRM"})
        events = _drain_until(ws, lambda o: o.get("type") == "confirm_request")
        request = events[-1]
        assert request["tool"] == "bash"
        assert request["why"] == "dangerous"

        r = client.post(f"/api/tasks/{task['id']}/confirm",
                        json={"request_id": request["request_id"], "allow": False})
        assert r.json() == {"sent": True}

        done = _drain_until(ws, lambda o: o.get("type") == "Done")[-1]
        assert done["text"] == "denied"


# -- cancel -----------------------------------------------------------------

def test_cancel_stops_a_running_turn(client, repo):
    task = _create_task(client, repo)
    with client.websocket_connect(f"/ws/tasks/{task['id']}?token={client.headers['Authorization'][7:]}") as ws:
        client.post(f"/api/tasks/{task['id']}/prompt", json={"text": "SLEEP"})
        _drain_until(ws, lambda o: o.get("type") == "Phase" and o.get("state") == "tools")

        r = client.post(f"/api/tasks/{task['id']}/cancel")
        assert r.json() == {"cancelled": True}

        events = _drain_until(ws, lambda o: o.get("type") == "Error")
        assert events[-1]["message"] == "cancelled"


# -- model / mode -------------------------------------------------------

def test_set_model_and_mode(client, repo):
    task = _create_task(client, repo)
    r = client.post(f"/api/tasks/{task['id']}/model", json={"model": "sonnet"})
    assert r.json()["model"] == "sonnet"
    r = client.post(f"/api/tasks/{task['id']}/mode", json={"mode": "plan"})
    assert r.json()["mode"] == "plan"

    reloaded = client.get(f"/api/tasks/{task['id']}").json()
    assert reloaded["model"] == "sonnet"
    assert reloaded["mode"] == "plan"


# -- background jobs (forwarded from the worker) -----------------------------

def test_jobs_endpoint_relays_worker_jobs(client, repo):
    task = _create_task(client, repo)
    with client.websocket_connect(f"/ws/tasks/{task['id']}?token={client.headers['Authorization'][7:]}") as ws:
        client.post(f"/api/tasks/{task['id']}/prompt", json={"text": "hello"})
        _drain_until(ws, lambda o: o.get("type") == "Done")

    jobs = client.get(f"/api/tasks/{task['id']}/jobs").json()
    assert jobs == [{"id": "j1", "command": "sleep 1", "finished": False,
                     "exit_code": None, "pid": 999}]


# -- overview WS ----------------------------------------------------------

def test_overview_ws_pushes_on_task_change(client, repo):
    with client.websocket_connect(
            f"/ws/overview?token={client.headers['Authorization'][7:]}") as ws:
        created = _create_task(client, repo, prompt="watch me")
        events = _drain_until(
            ws, lambda o: o.get("type") == "task" and o["task"]["id"] == created["id"])
        assert events[-1]["task"]["title"] == "watch me"


def test_overview_ws_accepts_query_token_without_auth_header(repo):
    """The WS handshake carries no Authorization header from a browser --
    `?token=` must work on its own, not just as a fallback."""
    app = create_app(port=7777, worker_argv=_stub_worker_argv)
    with TestClient(app) as anon:
        with anon.websocket_connect(f"/ws/overview?token={app.state.token}") as ws:
            r = anon.post("/api/tasks", json={"repo": str(repo), "worktree": False},
                          headers={"Authorization": f"Bearer {app.state.token}"})
            created = r.json()
            events = _drain_until(
                ws, lambda o: o.get("type") == "task" and o["task"]["id"] == created["id"])
            assert events[-1]["task"]["id"] == created["id"]


def test_overview_ws_rejects_bad_token(repo):
    app = create_app(port=7777, worker_argv=_stub_worker_argv)
    with TestClient(app) as anon:
        with pytest.raises(Exception):
            with anon.websocket_connect("/ws/overview?token=nope"):
                pass


# -- delete -----------------------------------------------------------------

def test_delete_task_removes_it(client, repo):
    task = _create_task(client, repo)
    r = client.delete(f"/api/tasks/{task['id']}")
    assert r.json() == {"deleted": True}
    assert client.get(f"/api/tasks/{task['id']}").status_code == 404
