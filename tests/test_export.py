import json

import pytest

from omega import export, session


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "DIR", tmp_path / "sessions")
    yield


def test_to_markdown_renders_user_prompt_as_heading():
    history = [{"role": "user", "content": "fix the bug"}]
    md = export.to_markdown(history)
    assert "## › fix the bug" in md


def test_to_markdown_skips_the_resume_marker():
    history = [{"role": "user", "content": f"{session.RESUME_PREFIX} — earlier stuff]"}]
    md = export.to_markdown(history)
    assert session.RESUME_PREFIX not in md
    assert "earlier stuff" not in md


def test_to_markdown_renders_assistant_text_verbatim():
    history = [{"role": "assistant", "content": "Here's **what** I did."}]
    md = export.to_markdown(history)
    assert "Here's **what** I did." in md


def test_to_markdown_renders_tool_calls_as_fenced_list_with_outcomes():
    history = [
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "bash", "arguments": json.dumps({"command": "ls -la"})}},
        ]},
        {"role": "tool", "tool_call_id": "c1", "content": "a.py\nb.py\n[exit 0]"},
    ]
    md = export.to_markdown(history)
    assert "```" in md
    assert "bash" in md and "ls -la" in md
    assert "a.py" in md


def test_to_markdown_references_offloaded_result_by_artifact_id_not_full_content():
    history = [
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "bash", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "c1",
         "content": "head...\n[full output: 50000 chars, saved as artifact deadbeef — fetch_result(deadbeef)]"},
    ]
    md = export.to_markdown(history)
    assert "[artifact deadbeef]" in md
    assert "head..." not in md


def test_write_creates_default_path_under_session_dir(tmp_path):
    out = export.write([{"role": "user", "content": "hi"}], "sess1")
    assert out == session.DIR / "sess1" / "transcript.md"
    assert out.exists()
    assert "hi" in out.read_text()


def test_write_honors_explicit_path(tmp_path):
    target = tmp_path / "custom" / "out.md"
    out = export.write([{"role": "user", "content": "hi"}], "sess1", str(target))
    assert out == target
    assert out.exists()
