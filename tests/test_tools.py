import asyncio

import pytest

from omega import tools
from omega.llm import ToolCall


def call(name, **args):
    import json
    return ToolCall("id", name, json.dumps(args))


@pytest.mark.asyncio
async def test_plan_mode_actually_blocks_bash(tmp_path):
    """Regression: filtering schemas only HID the tool; run() executed it anyway."""
    sentinel = tmp_path / "breach.txt"
    result = await tools.run(call("bash", command=f"touch {sentinel}"),
                             allowed=tools.READ_ONLY)
    assert "not permitted" in result
    assert not sentinel.exists()


@pytest.mark.asyncio
async def test_subagent_cannot_reach_mutating_tools():
    allowed = tools.READ_ONLY - {"subagent"}
    for name in ("write", "edit", "bash"):
        assert "not permitted" in await tools.run(call(name, path="x", command="x",
                                                       old="a", new="b", content="c"),
                                                  allowed=allowed)


@pytest.mark.asyncio
async def test_bash_timeout_kills_the_process_group(approve_all, tmp_path):
    """Regression: subprocess.run killed only the shell; children survived."""
    sentinel = tmp_path / "orphan.txt"
    result = await tools.run(call(
        "bash", command=f"(sleep 5; touch {sentinel}) & echo started", timeout=1))
    assert "timed out" in result
    await asyncio.sleep(6)
    assert not sentinel.exists(), "backgrounded child survived the timeout"


@pytest.mark.asyncio
async def test_bash_surfaces_exit_code_alongside_output(approve_all):
    """Regression: exit code only appeared when output was empty."""
    result = await tools.run(call("bash", command="echo compiled; exit 7"))
    assert "compiled" in result and "[exit 7]" in result


@pytest.mark.asyncio
async def test_stderr_survives_a_flood_of_stdout(approve_all):
    """Regression: head-only truncation dropped exactly the error message."""
    result = await tools.run(call(
        "bash", command="for i in $(seq 1 6000); do echo padpadpadpad; done; "
                        "echo FATAL_MARKER >&2; exit 1"))
    assert "FATAL_MARKER" in result and "[exit 1]" in result


@pytest.mark.asyncio
async def test_bash_timeout_is_clamped(approve_all):
    assert "timed out after 1s" in await tools.run(
        call("bash", command="sleep 4", timeout=0))


def test_truncate_keeps_head_and_tail():
    text = "S" * 100 + "M" * 5000 + "E" * 100
    out = tools.truncate(text, 400)
    assert out.startswith("S") and out.endswith("E") and "truncated" in out


@pytest.mark.asyncio
async def test_edit_requires_a_unique_match(tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("alpha\nalpha\n")
    assert "3 times" in await tools.run(call("edit", path=str(f), old="a", new="b")) \
        or "appears" in await tools.run(call("edit", path=str(f), old="alpha", new="b"))
    f.write_text("solo\n")
    assert "edited" in await tools.run(call("edit", path=str(f), old="solo", new="duo"))
    assert f.read_text() == "duo\n"
    assert "not found" in await tools.run(call("edit", path=str(f), old="zzz", new="y"))


@pytest.mark.asyncio
async def test_grep_distinguishes_no_matches_from_bad_regex(tmp_path):
    (tmp_path / "a.txt").write_text("hello\n")
    assert "no matches" in await tools.run(call("grep", pattern="zzz", path=str(tmp_path)))
    assert "error" in await tools.run(call("grep", pattern="[unclosed", path=str(tmp_path)))


@pytest.mark.asyncio
async def test_reading_outside_the_repo_taints_the_turn(tmp_path):
    tools.set_tainted(False)
    await tools.run(call("read", path="/etc/hosts", limit=1))
    assert tools.TAINTED
    assert "confirmation" in await tools.run(call("bash", command="echo hi"))
