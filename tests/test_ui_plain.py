import io

import pytest
from rich.console import Console

from omega import events
from omega.ui import plain


@pytest.fixture(autouse=True)
def isolate_console(monkeypatch):
    buf = io.StringIO()
    monkeypatch.setattr(plain, "console", Console(file=buf, force_terminal=False,
                                                   width=200))
    yield buf


@pytest.mark.asyncio
async def test_ask_user_digit_picks_the_label(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *a, **k: "2")
    options = [{"label": "alpha", "description": "first"},
               {"label": "beta", "description": "second"}]
    result = await plain.ask_user("pick one", options)
    assert result == "beta"


@pytest.mark.asyncio
async def test_ask_user_multi_select_comma_digits_pick_two(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *a, **k: "1,3")
    options = [{"label": "a"}, {"label": "b"}, {"label": "c"}]
    result = await plain.ask_user("pick some", options, multi_select=True)
    assert result == "a, c"


@pytest.mark.asyncio
async def test_ask_user_free_text_passes_through(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *a, **k: "neither, do something else")
    options = [{"label": "a"}, {"label": "b"}]
    result = await plain.ask_user("pick one", options)
    assert result == "neither, do something else"


@pytest.mark.asyncio
async def test_ask_user_eof_returns_no_answer(monkeypatch):
    def raise_eof(*a, **k):
        raise EOFError
    monkeypatch.setattr("builtins.input", raise_eof)
    result = await plain.ask_user("pick one", [{"label": "a"}])
    assert result == "(no answer)"


def test_render_offloaded_tool_end_prints_artifact_line(isolate_console):
    ev = events.ToolEnd(call_id="c1", name="bash", result_preview="ok",
                        duration_s=0.1, offloaded=True, artifact_id="deadbeef")
    plain.render(ev)
    out = isolate_console.getvalue()
    assert "deadbeef" in out
    assert "artifact" in out


def test_render_non_offloaded_tool_end_prints_nothing(isolate_console):
    ev = events.ToolEnd(call_id="c1", name="bash", result_preview="ok",
                        duration_s=0.1, offloaded=False)
    plain.render(ev)
    assert isolate_console.getvalue() == ""


def test_render_tool_start_indents_when_subagent_id_set(isolate_console):
    ev = events.ToolStart(call_id="c1", name="grep", args_preview="foo",
                          subagent_id="a1b2c3", tier="fast")
    plain.render(ev)
    out = isolate_console.getvalue()
    assert "grep" in out and "foo" in out and "fast" in out and "a1b2c3" in out


def test_render_tool_start_does_not_repeat_the_tool_name(isolate_console):
    ev = events.ToolStart(call_id="c1", name="bash", args_preview="bash  $ ls -la")
    plain.render(ev)
    out = isolate_console.getvalue()
    assert out.count("bash") == 1


def test_render_recall_outcome_prints_memory_count(isolate_console):
    ev = events.ToolEnd(call_id="c1", name="recall", result_preview="ok",
                        duration_s=0.1, offloaded=False, outcome="→ 2 memories")
    plain.render(ev)
    out = isolate_console.getvalue()
    assert "2 memories" in out


def test_render_error_shows_only_first_line_with_glyph(isolate_console):
    ev = events.Error(message="FileNotFoundError: no such file\nfull traceback here")
    plain.render(ev)
    out = isolate_console.getvalue()
    assert "FileNotFoundError: no such file" in out
    assert "traceback" not in out
    assert "✗" in out


def test_render_checkpoint_verified_job_events(isolate_console):
    plain.render(events.Checkpoint(turn=1, id="cp1"))
    plain.render(events.Verified(results_summary="pytest ok", ok=True))
    plain.render(events.Verified(results_summary="pytest failed", ok=False))
    plain.render(events.JobStarted(id="j1", command="sleep 1"))
    plain.render(events.JobFinished(id="j1", exit_code=0))
    plain.render(events.JobFinished(id="j1", exit_code=1))
    out = isolate_console.getvalue()
    assert "checkpoint" in out
    assert "verified: pytest ok" in out
    assert "verification failed: pytest failed" in out
    assert "job j1 started" in out
    assert "job j1 finished (exit 0)" in out
    assert "job j1 finished (exit 1)" in out


@pytest.mark.asyncio
async def test_run_prompt_survives_a_render_error_and_still_closes_the_turn(
        monkeypatch, isolate_console, tmp_path):
    from omega import loop, session

    monkeypatch.setattr(session, "DIR", tmp_path)
    sess = session.Session.new(cwd=str(tmp_path))

    def broken_render(ev):
        raise RuntimeError("boom")

    monkeypatch.setattr(plain, "render", broken_render)

    async def fake_run_turn(cfg, history, mode, emit, model=None):
        emit(events.Done("hi"))
        history.append({"role": "assistant", "content": "hi"})

    monkeypatch.setattr(loop, "run_turn", fake_run_turn)

    await plain.run_prompt(cfg=None, history=sess.history, prompt="hello", mode="build", sess=sess)

    out = isolate_console.getvalue()
    assert "render error" in out
    assert sess.history[-1] == {"role": "assistant", "content": "hi"}
