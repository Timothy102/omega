import json

import pytest

from omega import events, session, trace


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "DIR", tmp_path / "sessions")
    yield


def test_append_writes_one_json_line_with_epoch_turn_and_type():
    trace.append("sess1", events.ToolStart(call_id="c1", name="bash", args_preview="bash  $ ls"), turn=1)
    lines = trace.trace_path("sess1").read_text().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["type"] == "ToolStart"
    assert row["turn"] == 1
    assert row["name"] == "bash"
    assert row["args_preview"] == "bash  $ ls"
    assert row["call_id"] == "c1"
    assert isinstance(row["t"], float)


def test_append_never_raises_on_a_non_dataclass_event():
    class NotADataclass:
        pass

    trace.append("sess1", NotADataclass(), turn=1)  # type: ignore[arg-type]
    assert not trace.trace_path("sess1").exists()


def test_append_multiple_events_are_newline_delimited():
    trace.append("sess1", events.Phase(state="waiting"), turn=1)
    trace.append("sess1", events.Done(text="hi"), turn=1)
    lines = trace.trace_path("sess1").read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["type"] == "Phase"
    assert json.loads(lines[1])["type"] == "Done"


def test_render_timeline_no_trace_file_says_so():
    assert trace.render_timeline("nope") == "no trace for session nope"


def test_render_timeline_json_returns_one_raw_line_per_event():
    trace.append("sess1", events.Done(text="done"), turn=1)
    out = trace.render_timeline("sess1", raw_json=True)
    row = json.loads(out)
    assert row["type"] == "Done" and row["text"] == "done"


def test_render_timeline_shows_tool_start_and_end_with_duration():
    trace.append("sess1", events.ToolStart(call_id="c1", name="bash", args_preview="bash  $ ls"), turn=1)
    trace.append("sess1", events.ToolEnd(call_id="c1", name="bash", result_preview="ok",
                                         duration_s=1.5, offloaded=False, outcome="→ exit 0"), turn=1)
    out = trace.render_timeline("sess1")
    assert "ToolStart" in out and "ToolEnd" in out
    assert "1.50s" in out
    assert "turn 1" in out


def test_render_timeline_tools_only_filters_non_tool_events():
    trace.append("sess1", events.Phase(state="waiting"), turn=1)
    trace.append("sess1", events.ToolStart(call_id="c1", name="read", args_preview="read  a.py"), turn=1)
    out = trace.render_timeline("sess1", tools_only=True)
    assert "ToolStart" in out
    assert "Phase" not in out


def test_render_timeline_per_turn_totals_priced_from_model_used():
    trace.append("sess1", events.ModelUsed(alias="haiku", model="claude-haiku-4-5", provider="anthropic"), turn=1)
    trace.append("sess1", events.Usage(prompt_tokens=1000, completion_tokens=500, used=1500, limit=200000), turn=1)
    out = trace.render_timeline("sess1")
    assert "turn 1: 1000 in / 500 out tokens" in out
    assert "$0.0035" in out  # 1000/1e6*1.0 + 500/1e6*5.0


def test_render_timeline_sums_usage_across_rounds_within_a_turn():
    trace.append("sess1", events.ModelUsed(alias="haiku", model="claude-haiku-4-5", provider="anthropic"), turn=1)
    trace.append("sess1", events.Usage(prompt_tokens=100, completion_tokens=50, used=150, limit=200000), turn=1)
    trace.append("sess1", events.Usage(prompt_tokens=100, completion_tokens=50, used=300, limit=200000), turn=1)
    out = trace.render_timeline("sess1")
    assert "turn 1: 200 in / 100 out tokens" in out


def test_render_timeline_unpriced_model_shows_unknown_cost():
    trace.append("sess1", events.ModelUsed(alias="totally-unknown", model="x", provider="y"), turn=1)
    trace.append("sess1", events.Usage(prompt_tokens=10, completion_tokens=5, used=15, limit=1000), turn=1)
    out = trace.render_timeline("sess1")
    assert "turn 1: 10 in / 5 out tokens · unknown" in out


def test_render_timeline_tolerates_a_truncated_trailing_line(tmp_path):
    path = trace.trace_path("sess1")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"t": 1.0, "turn": 1, "type": "Done", "text": "ok"}) + "\n"
                    + '{"t": 2.0, "turn": 1, "type": "Er')
    out = trace.render_timeline("sess1")
    assert "Done" in out
