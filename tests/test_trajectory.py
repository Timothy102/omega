import json

from omega import trajectory


def _history(n: int, result_prefix: str = "result"):
    h = [{"role": "user", "content": "go"}]
    for i in range(n):
        h += [
            {"role": "assistant", "content": None,
             "tool_calls": [{"id": f"c{i}", "type": "function",
                             "function": {"name": "read",
                                          "arguments": json.dumps({"path": f"/f{i}.txt"})}}]},
            {"role": "tool", "tool_call_id": f"c{i}", "content": f"{result_prefix} {i}"},
        ]
    return h


def test_build_returns_one_entry_per_tool_call():
    entries = trajectory.build(_history(3))
    assert [e.call_id for e in entries] == ["c0", "c1", "c2"]
    assert all(e.tool == "read" for e in entries)


def test_build_caps_at_max_entries_oldest_dropped():
    entries = trajectory.build(_history(trajectory.MAX_ENTRIES + 10))
    assert len(entries) == trajectory.MAX_ENTRIES
    assert entries[0].call_id == "c10"
    assert entries[-1].call_id == f"c{trajectory.MAX_ENTRIES + 9}"


def test_args_digest_capped_and_derived_from_real_args():
    h = [{"role": "user", "content": "go"},
         {"role": "assistant", "content": None,
          "tool_calls": [{"id": "c0", "type": "function",
                          "function": {"name": "read",
                                       "arguments": json.dumps({"path": "/some/real/path.txt"})}}]},
         {"role": "tool", "tool_call_id": "c0", "content": "file contents here"}]
    entries = trajectory.build(h)
    assert len(entries) == 1
    assert len(entries[0].args_digest) <= trajectory.ARGS_DIGEST_CHARS
    assert "path.txt" in entries[0].args_digest


def test_summary_capped_at_summary_chars():
    h = [{"role": "user", "content": "go"},
         {"role": "assistant", "content": None,
          "tool_calls": [{"id": "c0", "type": "function",
                          "function": {"name": "bash", "arguments": "{}"}}]},
         {"role": "tool", "tool_call_id": "c0", "content": "x" * 500}]
    entries = trajectory.build(h)
    assert len(entries[0].summary) <= trajectory.SUMMARY_CHARS


def test_artifact_id_extracted_from_offload_footer():
    h = [{"role": "user", "content": "go"},
         {"role": "assistant", "content": None,
          "tool_calls": [{"id": "c0", "type": "function",
                          "function": {"name": "bash", "arguments": "{}"}}]},
         {"role": "tool", "tool_call_id": "c0",
          "content": "preview...\n[full output: 9000 chars, saved as artifact deadbeef "
                     "— fetch_result(deadbeef) to read more]"}]
    entries = trajectory.build(h)
    assert entries[0].artifact_id == "deadbeef"


def test_render_empty_history_returns_empty_string():
    assert trajectory.render([{"role": "user", "content": "hi"}]) == ""


def test_render_produces_header_and_dedupe_footer():
    block = trajectory.render(_history(2))
    assert block.startswith("## Recent actions")
    assert "fetch_result" in block
    assert "c0" not in block  # call ids are internal bookkeeping, not shown


def test_render_caps_at_action_line_cap():
    block = trajectory.render(_history(trajectory.ACTION_LINE_CAP + 10))
    action_lines = [l for l in block.splitlines() if l.startswith("- ")]
    assert len(action_lines) == trajectory.ACTION_LINE_CAP
    # keeps the most recent, not the oldest
    assert "f" + str(trajectory.ACTION_LINE_CAP + 9) in block


def test_render_never_exceeds_block_max_chars():
    block = trajectory.render(_history(trajectory.ACTION_LINE_CAP, result_prefix="y" * 200))
    assert len(block) <= trajectory.BLOCK_MAX_CHARS


def test_render_is_deterministic_across_calls():
    h = _history(5)
    assert trajectory.render(h) == trajectory.render(h)


def test_render_reflects_latest_history_state():
    """The ledger is rebuilt from history, not cached -- new tool calls show
    up immediately without any extra bookkeeping."""
    h = _history(1)
    before = trajectory.render(h)
    h += [{"role": "assistant", "content": None,
          "tool_calls": [{"id": "c1", "type": "function",
                          "function": {"name": "bash", "arguments": "{}"}}]},
         {"role": "tool", "tool_call_id": "c1", "content": "done"}]
    after = trajectory.render(h)
    assert before != after
    assert "bash" in after


def test_compaction_lines_cover_every_call_uncapped_by_action_line_cap():
    h = _history(trajectory.ACTION_LINE_CAP + 5)
    lines = trajectory.compaction_lines(h, 1000)
    assert len(lines) == trajectory.ACTION_LINE_CAP + 5


def test_compaction_lines_respect_entry_char_cap():
    h = [{"role": "user", "content": "go"},
         {"role": "assistant", "content": None,
          "tool_calls": [{"id": "c0", "type": "function",
                          "function": {"name": "bash", "arguments": "{}"}}]},
         {"role": "tool", "tool_call_id": "c0", "content": "z" * 5000}]
    lines = trajectory.compaction_lines(h, 200)
    assert len(lines[0]) <= 200


def test_compaction_lines_are_deterministic():
    h = _history(4)
    assert trajectory.compaction_lines(h, 1000) == trajectory.compaction_lines(h, 1000)
