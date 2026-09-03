from rig import events


def test_text_delta_construction_and_equality():
    a = events.TextDelta(text="hi")
    b = events.TextDelta(text="hi")
    assert a == b
    assert a.text == "hi"


def test_tool_start_defaults():
    e = events.ToolStart(call_id="c1", name="read", args_preview="{}")
    assert e.subagent_id is None and e.tier is None


def test_tool_start_with_subagent_fields():
    e = events.ToolStart(call_id="c1", name="read", args_preview="{}",
                          subagent_id="ab12", tier="fast")
    assert e.subagent_id == "ab12" and e.tier == "fast"


def test_tool_end():
    e = events.ToolEnd(call_id="c1", name="bash", result_preview="ok",
                        duration_s=0.5, offloaded=False)
    assert e.artifact_id is None
    assert e == events.ToolEnd(call_id="c1", name="bash", result_preview="ok",
                                duration_s=0.5, offloaded=False)


def test_tool_end_offloaded():
    e = events.ToolEnd(call_id="c1", name="bash", result_preview="ok",
                        duration_s=0.5, offloaded=True, artifact_id="deadbeef")
    assert e.offloaded and e.artifact_id == "deadbeef"


def test_compacted():
    assert events.Compacted(note="dropped 4 turns").note == "dropped 4 turns"


def test_memory_write():
    e = events.MemoryWrite(node_id="n1", type="fact", title="t", scope="project")
    assert (e.node_id, e.type, e.title, e.scope) == ("n1", "fact", "t", "project")


def test_memory_consolidated():
    assert events.MemoryConsolidated(summary="merged 2 nodes").summary == "merged 2 nodes"


def test_subagent_spawned():
    e = events.SubagentSpawned(subagent_id="ab12", tier="fast", task_preview="look up x")
    assert e.tier == "fast"


def test_subagent_done():
    e = events.SubagentDone(subagent_id="ab12", summary_preview="found it")
    assert e.summary_preview == "found it"


def test_error():
    assert events.Error(message="boom").message == "boom"


def test_done():
    assert events.Done(text="finished").text == "finished"


def test_events_are_frozen():
    e = events.TextDelta(text="hi")
    try:
        e.text = "bye"
    except AttributeError:
        return
    raise AssertionError("expected FrozenInstanceError")


def test_event_type_alias_covers_all_variants():
    import typing
    args = set(typing.get_args(events.Event))
    expected = {events.TextDelta, events.ToolStart, events.ToolEnd, events.Compacted,
                events.MemoryWrite, events.MemoryConsolidated, events.SubagentSpawned,
                events.SubagentDone, events.Error, events.Done, events.Usage}
    assert args == expected
