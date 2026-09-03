from omega import events
from omega.ui import format


def test_tool_start_without_subagent_has_no_leading_indent_marker():
    ev = events.ToolStart(call_id="c1", name="read", args_preview="path.py")
    text = format.tool_start(ev)
    assert "read" in text and "path.py" in text
    assert "·" not in text


def test_tool_start_with_subagent_includes_tier_and_id():
    ev = events.ToolStart(call_id="c1", name="grep", args_preview="foo",
                          subagent_id="a1b2c3", tier="fast")
    text = format.tool_start(ev)
    assert "grep" in text and "foo" in text and "fast" in text and "a1b2c3" in text


def test_tool_end_returns_none_when_not_offloaded():
    ev = events.ToolEnd(call_id="c1", name="bash", result_preview="ok",
                        duration_s=0.1, offloaded=False)
    assert format.tool_end(ev) is None


def test_tool_end_mentions_artifact_id_when_offloaded():
    ev = events.ToolEnd(call_id="c1", name="bash", result_preview="ok",
                        duration_s=0.1, offloaded=True, artifact_id="deadbeef")
    text = format.tool_end(ev)
    assert text is not None
    assert "offloaded" in text and "deadbeef" in text


def test_subagent_spawned_includes_tier_task_and_id():
    ev = events.SubagentSpawned(subagent_id="ab12", tier="mid", task_preview="find the bug")
    text = format.subagent_spawned(ev)
    assert "mid" in text and "find the bug" in text and "ab12" in text


def test_subagent_done_includes_id():
    ev = events.SubagentDone(subagent_id="ab12", summary_preview="fixed it")
    assert "ab12" in format.subagent_done(ev)


def test_compacted_includes_note():
    ev = events.Compacted(note="compacted 4 messages")
    assert "compacted 4 messages" in format.compacted(ev)


def test_memory_write_includes_type_title_and_scope():
    ev = events.MemoryWrite(node_id="n1", type="fact", title="uses postgres", scope="project")
    text = format.memory_write(ev)
    assert "fact" in text and "uses postgres" in text and "project" in text


def test_memory_consolidated_includes_summary():
    ev = events.MemoryConsolidated(summary="merged 2 nodes")
    assert "merged 2 nodes" in format.memory_consolidated(ev)


def test_error_includes_message():
    ev = events.Error(message="boom")
    assert "boom" in format.error(ev)
