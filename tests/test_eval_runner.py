import asyncio
import json
from pathlib import Path

import pytest

from omega import config, events, loop, tools
from omega.eval import runner
from omega.eval.tasks import Task


class FakeProvider:
    name = "fake-provider"


class FakeRole:
    context = 1_000_000
    model = "fake-model"
    alias = "opus"
    effort = None
    provider = FakeProvider()


class FakeCfg:
    def role(self, name: str) -> FakeRole:
        return FakeRole()

    def model(self, alias: str) -> FakeRole:
        return FakeRole()


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "f.txt").write_text("hi")
    return repo


async def _fake_run_agent_ok(cfg, role_name, system, history, tool_names=None,
                             emit=None, max_rounds=60, subagent_id=None, tier=None, role=None):
    emit = emit or (lambda e: None)
    emit(events.ModelUsed(alias="opus", model="fake-model", provider="fake"))
    emit(events.Phase("waiting"))
    emit(events.ToolStart(call_id="c1", name="bash", args_preview="bash: echo hi"))
    emit(events.ToolEnd(call_id="c1", name="bash", result_preview="hi", duration_s=0.01,
                        offloaded=False, result_chars=2))
    emit(events.Usage(prompt_tokens=100, completion_tokens=20, used=120, limit=role.context))
    emit(events.Phase("waiting"))
    emit(events.Done(text="all set"))
    emit(events.Phase("idle"))
    history.append({"role": "assistant", "content": "all set"})
    return "all set"


@pytest.mark.asyncio
async def test_run_one_passing_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(loop, "run_agent", _fake_run_agent_ok)
    repo = _make_repo(tmp_path)
    task = Task(name="t1", prompt="do it", check="test -f f.txt", repo=str(repo),
               timeout_s=30, mode="build")
    ws = await runner.prepare_workspace(task, tmp_path / "work")
    try:
        result = await runner.run_one(FakeCfg(), task, "opus", FakeRole(), ws)
    finally:
        await runner.cleanup_workspace(ws)

    assert result.passed is True
    assert result.error is None
    assert result.tokens_in == 100 and result.tokens_out == 20
    assert result.tool_calls == {"bash": 1}
    assert result.turns == 2
    assert result.cost_usd == pytest.approx(100 / 1_000_000 * 5.0 + 20 / 1_000_000 * 25.0)
    assert result.manifest is not None and len(result.manifest.rounds) == 2


@pytest.mark.asyncio
async def test_run_one_failing_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(loop, "run_agent", _fake_run_agent_ok)
    repo = _make_repo(tmp_path)
    task = Task(name="t2", prompt="do it", check="exit 1", repo=str(repo),
               timeout_s=30, mode="build")
    ws = await runner.prepare_workspace(task, tmp_path / "work")
    try:
        result = await runner.run_one(FakeCfg(), task, "opus", FakeRole(), ws)
    finally:
        await runner.cleanup_workspace(ws)

    assert result.passed is False
    assert result.error is None  # the agent turn itself succeeded; only the check failed


@pytest.mark.asyncio
async def test_run_one_timeout_marks_failed_without_running_check(tmp_path: Path,
                                                                   monkeypatch: pytest.MonkeyPatch):
    async def slow(cfg, role_name, system, history, tool_names=None, emit=None,
                   max_rounds=60, subagent_id=None, tier=None, role=None):
        await asyncio.sleep(5)
        return "never"

    monkeypatch.setattr(loop, "run_agent", slow)
    repo = _make_repo(tmp_path)
    task = Task(name="t3", prompt="do it", check="true", repo=str(repo),
               timeout_s=1, mode="build")
    ws = await runner.prepare_workspace(task, tmp_path / "work")
    try:
        result = await runner.run_one(FakeCfg(), task, "opus", FakeRole(), ws)
    finally:
        await runner.cleanup_workspace(ws)

    assert result.passed is False
    assert result.error is not None and "timed out" in result.error
    assert not (ws.path / "TRANSCRIPT.md").exists()


@pytest.mark.asyncio
async def test_run_suite_repeats_and_cleans_up_workspaces(tmp_path: Path,
                                                           monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(loop, "run_agent", _fake_run_agent_ok)
    repo = _make_repo(tmp_path)
    task = Task(name="t4", prompt="do it", check="true", repo=str(repo), timeout_s=30, mode="build")
    work_root = tmp_path / "work"

    results = await runner.run_suite(FakeCfg(), [task], [("opus", FakeRole())],
                                      repeat=2, jobs=2, work_root=work_root)

    assert len(results) == 2
    assert all(r.passed for r in results)
    assert list(work_root.iterdir()) == []


def test_resolve_models_defaults_to_main_role():
    roles = runner.resolve_models(FakeCfg(), None)
    assert len(roles) == 1
    label, role = roles[0]
    assert label == "opus" and isinstance(role, FakeRole)


def test_resolve_models_splits_comma_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    (tmp_path / "config.json").write_text(json.dumps({
        "providers": {"anthropic": {"type": "anthropic", "apiKeyEnv": "ANTHROPIC_API_KEY"}},
        "models": {
            "opus": {"model": "claude-opus-5", "provider": "anthropic", "context": 1000000},
            "sonnet": {"model": "claude-sonnet-5", "provider": "anthropic", "context": 1000000},
        },
        "roles": {"main": {"alias": "opus"}},
    }))
    cfg = config.load()
    roles = runner.resolve_models(cfg, "opus, sonnet")
    assert [label for label, _ in roles] == ["opus", "sonnet"]
    assert [role.model for _, role in roles] == ["claude-opus-5", "claude-sonnet-5"]


@pytest.fixture(autouse=True)
def _isolate_tool_globals(monkeypatch: pytest.MonkeyPatch):
    # monkeypatch.setattr (not a raw assignment) so pytest restores whatever
    # value another test/fixture left here, instead of stomping it to a fixed
    # value that leaks across test files that don't expect it.
    monkeypatch.setattr(tools, "SESSION_ID", None)
    monkeypatch.setattr(tools, "CONFIRM", None)
    monkeypatch.setattr(tools, "ASK_USER", None)
