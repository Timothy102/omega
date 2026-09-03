import subprocess

import pytest

from omega import checkpoint, config, events, llm, loop, tools
from omega.llm import Turn


def scripted_stream(rounds: list):
    async def stream(role, messages, schemas=None, fallback=None):
        for kind, payload in rounds.pop(0):
            yield kind, payload
    return stream


@pytest.fixture(autouse=True)
def isolate(tmp_path_factory, monkeypatch):
    monkeypatch.setattr(checkpoint, "DIR", tmp_path_factory.mktemp("omega-sessions"))
    monkeypatch.setattr(tools, "SESSION_ID", "sess-checkpoint")
    yield
    monkeypatch.setattr(tools, "SESSION_ID", None)


def _cfg(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "no-such-config.json")
    return config.load()


@pytest.mark.asyncio
async def test_build_turn_in_git_repo_emits_checkpoint(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)

    monkeypatch.setattr(llm, "stream", scripted_stream(
        [[("done", Turn(text="ok", tool_calls=[]))]]))

    cfg = _cfg(tmp_path, monkeypatch)
    received = []
    history = [{"role": "user", "content": "hi"}]
    await loop.run_turn(cfg, history, mode="build", emit=received.append)

    checkpoints = [e for e in received if isinstance(e, events.Checkpoint)]
    assert len(checkpoints) == 1
    assert checkpoints[0].turn == 1


@pytest.mark.asyncio
async def test_build_turn_outside_git_repo_emits_no_checkpoint(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # no `git init` -- not a repo

    monkeypatch.setattr(llm, "stream", scripted_stream(
        [[("done", Turn(text="ok", tool_calls=[]))]]))

    cfg = _cfg(tmp_path, monkeypatch)
    received = []
    history = [{"role": "user", "content": "hi"}]
    await loop.run_turn(cfg, history, mode="build", emit=received.append)

    assert not any(isinstance(e, events.Checkpoint) for e in received)


@pytest.mark.asyncio
async def test_plan_mode_never_checkpoints_even_in_a_git_repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)

    monkeypatch.setattr(llm, "stream", scripted_stream(
        [[("done", Turn(text="ok", tool_calls=[]))]]))

    cfg = _cfg(tmp_path, monkeypatch)
    received = []
    history = [{"role": "user", "content": "hi"}]
    await loop.run_turn(cfg, history, mode="plan", emit=received.append)

    assert not any(isinstance(e, events.Checkpoint) for e in received)


@pytest.mark.asyncio
async def test_second_build_turn_tags_checkpoint_with_turn_two(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)

    cfg = _cfg(tmp_path, monkeypatch)
    history = [{"role": "user", "content": "first"}]
    monkeypatch.setattr(llm, "stream", scripted_stream(
        [[("done", Turn(text="ok", tool_calls=[]))]]))
    await loop.run_turn(cfg, history, mode="build", emit=lambda e: None)

    history.append({"role": "assistant", "content": "ok"})
    history.append({"role": "user", "content": "second"})
    monkeypatch.setattr(llm, "stream", scripted_stream(
        [[("done", Turn(text="ok again", tool_calls=[]))]]))
    received = []
    await loop.run_turn(cfg, history, mode="build", emit=received.append)

    checkpoints = [e for e in received if isinstance(e, events.Checkpoint)]
    assert checkpoints[0].turn == 2
