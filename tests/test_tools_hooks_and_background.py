import json

import pytest

from omega import artifacts, tools
from omega.config import HookRule
from omega.llm import ToolCall


def call(name, **args):
    return ToolCall("id", name, json.dumps(args))


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(artifacts, "DIR", tmp_path)
    monkeypatch.setattr(tools, "JOBS_DIR", tmp_path / "sessions")
    monkeypatch.setattr(tools, "HOOK_RULES", {})
    monkeypatch.setattr(tools, "EMIT", None)
    yield
    monkeypatch.setattr(tools, "HOOK_RULES", {})
    monkeypatch.setattr(tools, "EMIT", None)


# ---- pre/post hook wiring through tools.run --------------------------------


@pytest.mark.asyncio
async def test_pre_hook_blocks_the_tool_call(tmp_path):
    tools.HOOK_RULES = {"pre_tool": [HookRule(tools=["write"], command="echo nope && exit 1")]}
    target = tmp_path / "out.txt"
    result = await tools.run(call("write", path=str(target), content="hi"))
    assert result.startswith("error: blocked by hook: nope")
    assert not target.exists()


@pytest.mark.asyncio
async def test_pre_hook_allows_call_through_when_it_exits_zero(tmp_path):
    tools.HOOK_RULES = {"pre_tool": [HookRule(tools=["write"], command="true")]}
    target = tmp_path / "out.txt"
    result = await tools.run(call("write", path=str(target), content="hi"))
    assert "wrote" in result
    assert target.read_text() == "hi"


@pytest.mark.asyncio
async def test_post_hook_output_is_appended_to_result(tmp_path):
    tools.HOOK_RULES = {"post_tool": [HookRule(tools=["write"], command="echo formatted")]}
    target = tmp_path / "out.txt"
    result = await tools.run(call("write", path=str(target), content="hi"))
    assert "[hook echo] formatted" in result


@pytest.mark.asyncio
async def test_hooks_do_not_apply_to_other_tools(tmp_path):
    tools.HOOK_RULES = {"pre_tool": [HookRule(tools=["edit"], command="exit 1")]}
    target = tmp_path / "out.txt"
    result = await tools.run(call("write", path=str(target), content="hi"))
    assert "blocked by hook" not in result


# ---- background bash --------------------------------------------------------


@pytest.mark.asyncio
async def test_background_bash_starts_and_returns_immediately(approve_all):
    result = await tools.run(call("bash", command="sleep 0.2; echo done", background=True))
    assert "started background job" in result


@pytest.mark.asyncio
async def test_bash_status_reports_running_then_finished(approve_all):
    started = await tools.run(call("bash", command="sleep 0.2; echo done", background=True))
    job_id = started.split("started background job ")[1].split(" ")[0]

    status = await tools.run(call("bash_status", id=job_id))
    assert "running" in status or "finished" in status

    import asyncio
    for _ in range(30):
        status = await tools.run(call("bash_status", id=job_id))
        if "finished" in status:
            break
        await asyncio.sleep(0.05)

    assert "finished, exit 0" in status
    assert "done" in status


@pytest.mark.asyncio
async def test_bash_status_unknown_id_reports_error():
    result = await tools.run(call("bash_status", id="doesnotexist"))
    assert "error" in result


@pytest.mark.asyncio
async def test_background_bash_job_appears_in_list_jobs(approve_all):
    started = await tools.run(call("bash", command="sleep 0.2; echo done", background=True))
    job_id = started.split("started background job ")[1].split(" ")[0]
    jobs = tools.list_jobs()
    assert any(j["id"] == job_id for j in jobs)


@pytest.mark.asyncio
async def test_background_bash_emits_job_started_and_finished_events(approve_all):
    events_seen = []
    tools.EMIT = events_seen.append
    try:
        started = await tools.run(call("bash", command="sleep 0.1; echo done", background=True))
        job_id = started.split("started background job ")[1].split(" ")[0]

        import asyncio
        for _ in range(30):
            status = await tools.run(call("bash_status", id=job_id))
            if "finished" in status:
                break
            await asyncio.sleep(0.05)
    finally:
        tools.EMIT = None

    from omega import events
    names = [type(e).__name__ for e in events_seen]
    assert "JobStarted" in names
    assert "JobFinished" in names
    finished = next(e for e in events_seen if isinstance(e, events.JobFinished))
    assert finished.exit_code == 0
