import io

from rich.console import Console

from omega import events
from omega.ui import format


def test_tool_start_without_subagent_has_no_leading_indent_marker():
    ev = events.ToolStart(call_id="c1", name="read", args_preview="path.py")
    text = format.tool_start(ev)
    assert "read" in text and "path.py" in text
    assert "·" not in text


def test_tool_start_does_not_repeat_the_tool_name():
    ev = events.ToolStart(call_id="c1", name="bash", args_preview="bash  $ ls -la")
    text = format.tool_start(ev)
    assert text.count("bash") == 1
    assert "$ ls -la" in text


def test_tool_start_pads_name_column():
    ev = events.ToolStart(call_id="c1", name="read", args_preview="read  a.py")
    text = format.tool_start(ev)
    assert format.pad_name("read") in text


def test_tool_start_with_subagent_includes_tier_and_id():
    ev = events.ToolStart(call_id="c1", name="grep", args_preview="foo",
                          subagent_id="a1b2c3", tier="fast")
    text = format.tool_start(ev)
    assert "grep" in text and "foo" in text and "fast" in text and "a1b2c3" in text
    assert "└" in text


def test_tool_start_truncates_detail_to_width():
    ev = events.ToolStart(call_id="c1", name="bash", args_preview="bash  $ " + "x" * 200)
    text = format.tool_start(ev, width=40)
    assert "…" in text
    assert len(text) < 200


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


def test_tool_end_shows_find_tools_outcome_in_plain_mode():
    ev = events.ToolEnd(call_id="c1", name="find_tools", result_preview="ok",
                        duration_s=0.1, offloaded=False, outcome="→ 3 tools")
    text = format.tool_end(ev)
    assert text is not None and "3 tools" in text


def _render_ok(markup: str) -> str:
    """A `[/x]`-style stray tag raises `rich.errors.MarkupError` when printed
    with markup parsing on -- this is the exact crash a live session hit."""
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=200)
    console.print(markup, highlight=False)
    return buf.getvalue()


def test_tool_start_escapes_stray_markup_in_args_preview():
    ev = events.ToolStart(call_id="c1", name="bash",
                          args_preview="bash  $ echo [/Short] and [bold] and [[literal")
    text = format.tool_start(ev)
    out = _render_ok(text)
    assert "[/Short]" in out and "[bold]" in out and "[[literal" in out


def test_tool_end_escapes_stray_markup_in_outcome():
    ev = events.ToolEnd(call_id="c1", name="bash", result_preview="ok", duration_s=0.1,
                        offloaded=False, outcome="→ error: unexpected [/token] here")
    text = format.tool_end(ev)
    assert text is not None
    out = _render_ok(text)
    assert "[/token]" in out


def test_error_escapes_stray_markup_in_message():
    ev = events.Error(message="boom: got [/x] from provider")
    out = _render_ok(format.error(ev))
    assert "[/x]" in out


def test_compacted_escapes_stray_markup_in_note():
    ev = events.Compacted(note="compaction skipped: KeyError('[missing]')")
    out = _render_ok(format.compacted(ev))
    assert "[missing]" in out


def test_describe_outcome_error_strips_untrusted_wrapper_and_keeps_full_text():
    text = ('error: <untrusted source="mcp:linear/save_project">\n'
           '{"error":"invalid_request","message":"' + "x" * 150 + '"}\n</untrusted>')
    outcome = format.describe_outcome("call_tool", text, 0.1, False, None, len(text))
    assert "<untrusted" not in outcome and "</untrusted>" not in outcome
    assert "invalid_request" in outcome
    assert "x" * 150 in outcome


def test_subagent_spawned_hides_id_by_default():
    ev = events.SubagentSpawned(subagent_id="ab12", tier="mid", task_preview="find the bug")
    text = format.subagent_spawned(ev)
    assert "mid" in text and "find the bug" in text
    assert "ab12" not in text


def test_subagent_spawned_shows_id_when_requested():
    ev = events.SubagentSpawned(subagent_id="ab12", tier="mid", task_preview="find the bug")
    text = format.subagent_spawned(ev, show_id=True)
    assert "ab12" in text


def test_subagent_done_reports_task_and_elapsed():
    ev = events.SubagentDone(subagent_id="ab12", summary_preview="fixed it")
    text = format.subagent_done(ev, task_preview="find the bug", elapsed_s=62)
    assert "find the bug" in text and "62s" in text and "finished" in text


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


def test_error_shows_only_first_line_with_red_glyph():
    ev = events.Error(message="FileNotFoundError: no such file\nfull traceback\nmore")
    text = format.error(ev)
    assert "FileNotFoundError: no such file" in text
    assert "traceback" not in text
    assert "[red]✗[/red]" in text


def test_describe_outcome_find_tools_counts_blocks():
    text = "tool_one\n  desc\n\ntool_two\n  desc"
    outcome = format.describe_outcome("find_tools", text, 0.1, False, None, len(text))
    assert outcome == "→ 2 tools"


def test_describe_outcome_find_tools_no_match():
    text = "no tools matched 'xyz'. 40 tools available across 5 servers."
    outcome = format.describe_outcome("find_tools", text, 0.1, False, None, len(text))
    assert outcome == "→ no match"


def test_describe_outcome_recall_counts_memories():
    text = "[n1] fact · project\ntitle\nbody\n\n[n2] fact · project\ntitle2\nbody2"
    outcome = format.describe_outcome("recall", text, 0.1, False, None, len(text))
    assert outcome == "→ 2 memories"


def test_describe_outcome_recall_none():
    outcome = format.describe_outcome("recall", "(no matching memories)", 0.1, False, None, 21)
    assert outcome == "→ none"


def test_describe_outcome_call_tool_shows_chars():
    text = "x" * 1200
    outcome = format.describe_outcome("call_tool", text, 0.1, False, None, len(text))
    assert outcome == "→ 1.2k chars"


def test_describe_outcome_call_tool_error_still_wins():
    outcome = format.describe_outcome("call_tool", "error: boom", 0.1, False, None, 11)
    assert outcome == "→ error: boom"


def test_fmt_num_uses_k_and_m_suffixes():
    assert format.fmt_num(42) == "42"
    assert format.fmt_num(13600) == "13.6k"
    assert format.fmt_num(1_000_000) == "1.0M"


def test_truncate_middle_keeps_start_and_end():
    text = "a" * 10 + "MIDDLE" + "b" * 10
    out = format.truncate_middle(text, 12)
    assert len(out) == 12
    assert out.startswith("a")
    assert out.endswith("b")
    assert "…" in out


def test_truncate_middle_noop_when_it_fits():
    assert format.truncate_middle("short", 40) == "short"


def test_right_align_pads_to_width():
    out = format.right_align("[bold]left[/bold]", "right", 20)
    assert out.endswith("right")
    assert format.visible_len(out) == 20


def test_right_align_falls_back_when_too_narrow():
    out = format.right_align("[bold]a very long left side[/bold]", "right", 5)
    assert out.endswith(" right")


def test_abbrev_cwd_shortens_home_path():
    import os
    home = os.path.expanduser("~")
    assert format.abbrev_cwd(f"{home}/Documents/code/omega") == "~/Documents/code/omega"


def test_abbrev_cwd_trims_to_last_segments():
    import os
    home = os.path.expanduser("~")
    out = format.abbrev_cwd(f"{home}/a/b/c/d/e")
    assert out == "~/…/c/d/e"


def test_relative_age_buckets():
    assert format.relative_age(30) == "30s"
    assert format.relative_age(90) == "1m"
    assert format.relative_age(3700) == "1h"
    assert format.relative_age(90000) == "1d"
