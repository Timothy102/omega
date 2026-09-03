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
