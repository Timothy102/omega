import json

import pytest

from omega import compact, llm, session
from omega.llm import Turn


def agentic_history(rounds=8):
    """The real shape of an agentic turn: exactly ONE user message, at index 0."""
    h = [{"role": "user", "content": "do the thing"}]
    for i in range(rounds):
        h += [{"role": "assistant", "content": None,
               "tool_calls": [{"id": f"c{i}", "type": "function",
                               "function": {"name": "read", "arguments": "{}"}}]},
              {"role": "tool", "tool_call_id": f"c{i}", "content": "x" * 400},
              {"role": "assistant", "content": f"step {i} done"}]
    return h


def orphans(history):
    ids = {c["id"] for m in history for c in (m.get("tool_calls") or [])}
    return [m["tool_call_id"] for m in history
            if m.get("role") == "tool" and m.get("tool_call_id") not in ids]


def test_split_never_severs_a_tool_call_from_its_result():
    h = agentic_history()
    for keep in range(1, 15):
        i = compact.safe_split(h, keep)
        if i:
            assert not (h[i].get("role") == "assistant" and h[i].get("tool_calls"))
            assert not orphans(h[i:])


def test_compaction_fires_inside_an_agentic_turn():
    """Regression: only user-message boundaries were legal, and there is
    exactly one such message -- at index 0, which was excluded."""
    assert compact.safe_split(agentic_history(), 6) != 0


def test_estimate_tokens_is_monotonic():
    small = [{"role": "user", "content": "hi"}]
    assert compact.estimate_tokens(small) < compact.estimate_tokens(small * 20)


def test_repair_removes_unanswered_tool_calls():
    h = [{"role": "user", "content": "x"},
         {"role": "assistant", "tool_calls": [{"id": "a"}]}]
    assert len(session.repair(h)) == 1


def test_repair_keeps_answered_tool_calls():
    h = [{"role": "user", "content": "x"},
         {"role": "assistant", "tool_calls": [{"id": "a"}]},
         {"role": "tool", "tool_call_id": "a", "content": "ok"}]
    assert len(session.repair(list(h))) == 3


def test_session_round_trips_through_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "DIR", tmp_path)
    s = session.Session.new(cwd=str(tmp_path))
    s.history = agentic_history(2)
    s.save()
    back = session.load(s.id)
    assert back.history == s.history and back.cwd == str(tmp_path)


def test_load_tolerates_unknown_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "DIR", tmp_path)
    s = session.Session.new(cwd=str(tmp_path))
    s.save()
    raw = json.loads((tmp_path / f"{s.id}.json").read_text())
    raw["a_field_from_the_future"] = 1
    (tmp_path / f"{s.id}.json").write_text(json.dumps(raw))
    assert session.load(s.id).id == s.id


def test_latest_never_crosses_projects(tmp_path, monkeypatch):
    """Regression: fell back to any project's newest session, so the model
    would reason about project A while bash ran in project B."""
    monkeypatch.setattr(session, "DIR", tmp_path)
    other = session.Session.new(cwd="/somewhere/else")
    other.history = [{"role": "user", "content": "other project"}]
    other.save()
    assert session.latest(cwd=str(tmp_path / "elsewhere")) is None


def test_append_writes_history_and_jsonl_line(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "DIR", tmp_path)
    s = session.Session.new(cwd=str(tmp_path))
    s.append({"role": "user", "content": "hi"})
    s.append({"role": "assistant", "content": "hello"})

    assert s.history == [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
    lines = (tmp_path / f"{s.id}.jsonl").read_text().splitlines()
    assert [json.loads(l) for l in lines] == s.history


def test_log_message_appends_without_a_session_object(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "DIR", tmp_path)
    session.log_message("sess-1", {"role": "user", "content": "a"})
    session.log_message("sess-1", {"role": "assistant", "content": "b"})
    lines = (tmp_path / "sess-1.jsonl").read_text().splitlines()
    assert [json.loads(l) for l in lines] == [
        {"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]


def test_close_turn_writes_jsonl_when_shorter_than_history(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "DIR", tmp_path)
    s = session.Session.new(cwd=str(tmp_path))
    history = agentic_history(1)
    s.close_turn(history, "build", interrupted=False)

    logged = [json.loads(l) for l in s.jsonl_path.read_text().splitlines()]
    assert logged == history


def test_close_turn_is_a_noop_on_jsonl_already_caught_up(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "DIR", tmp_path)
    s = session.Session.new(cwd=str(tmp_path))
    history = [{"role": "user", "content": "x"}]
    s.append(history[0])
    before = s.jsonl_path.read_text()
    s.close_turn(history, "build", interrupted=False)
    assert s.jsonl_path.read_text() == before


def test_load_recovers_from_jsonl_after_a_mid_turn_crash(tmp_path, monkeypatch):
    """The .json snapshot only reflects the last completed turn; a hand-built
    .jsonl with more messages simulates a crash mid-turn -- load() must
    rebuild history from it, repair() any dangling tool call, and report the
    recovery via Session.recovered."""
    monkeypatch.setattr(session, "DIR", tmp_path)
    s = session.Session.new(cwd=str(tmp_path))
    s.history = [{"role": "user", "content": "do the thing"}]
    s.save()

    crashed_history = [
        {"role": "user", "content": "do the thing"},
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "c0", "type": "function",
                         "function": {"name": "read", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c0", "content": "file contents"},
        {"role": "assistant", "content": "step 0 done"},
        {"role": "user", "content": "now do another thing"},
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "c1", "type": "function",
                         "function": {"name": "bash", "arguments": "{}"}}]},
    ]
    with s.jsonl_path.open("w") as f:
        for msg in crashed_history:
            f.write(json.dumps(msg) + "\n")

    back = session.load(s.id)
    # The trailing assistant turn's tool call was never answered when the
    # process died -- repair() must drop it, same as any other resume.
    assert back.history == crashed_history[:-1]
    assert back.recovered == len(crashed_history) - 1


def test_load_ignores_a_truncated_trailing_jsonl_line(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "DIR", tmp_path)
    s = session.Session.new(cwd=str(tmp_path))
    s.save()
    with s.jsonl_path.open("w") as f:
        f.write(json.dumps({"role": "user", "content": "a"}) + "\n")
        f.write(json.dumps({"role": "assistant", "content": "b"}) + "\n")
        f.write('{"role": "user", "content": "trunc')  # crash mid-write, no fsync

    back = session.load(s.id)
    assert back.history == [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]
    assert back.recovered == 2


def test_load_does_not_recover_when_jsonl_is_not_ahead(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "DIR", tmp_path)
    s = session.Session.new(cwd=str(tmp_path))
    s.history = agentic_history(1)
    s.save()
    with s.jsonl_path.open("w") as f:
        f.write(json.dumps(s.history[0]) + "\n")

    back = session.load(s.id)
    assert back.recovered == 0
    assert back.history == s.history


def test_recovered_field_is_not_persisted_to_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "DIR", tmp_path)
    s = session.Session.new(cwd=str(tmp_path))
    s.save()
    raw = json.loads((tmp_path / f"{s.id}.json").read_text())
    assert "recovered" not in raw


def test_session_list_rows_never_wrap(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "DIR", tmp_path)
    s = session.Session.new(cwd=str(tmp_path))
    s.history = [{"role": "user", "content": "x" * 400}]
    s.save()
    import shutil
    width = shutil.get_terminal_size((80, 24)).columns
    assert all(len(line) < width for line in session.render_list().splitlines())


# ---- ledger-first compaction -------------------------------------------------


class FakeProvider:
    name = "fake-provider"


class FakeRole:
    context = 1_000_000
    model = "fake-model"
    alias = None
    provider = FakeProvider()


class FakeCfg:
    roles: dict = {"main": None}

    def role(self, name):
        return FakeRole()


def _scripted_summary(text, sink=None):
    async def stream(role, messages, tools=None):
        if sink is not None:
            sink.append(messages)
        yield "done", Turn(text=text)
    return stream


def compaction_history(rounds=8, result="x" * 400):
    h = [{"role": "user", "content": "do the thing"}]
    for i in range(rounds):
        h += [{"role": "assistant", "content": None,
               "tool_calls": [{"id": f"c{i}", "type": "function",
                               "function": {"name": "read",
                                            "arguments": json.dumps({"path": f"/f{i}.txt"})}}]},
              {"role": "tool", "tool_call_id": f"c{i}", "content": result},
              {"role": "assistant", "content": f"step {i} done"}]
    return h


@pytest.mark.asyncio
async def test_compaction_appends_ledger_as_its_own_message_after_the_summary(monkeypatch):
    monkeypatch.setattr(llm, "stream", _scripted_summary("dense summary of what happened"))

    h = compaction_history()
    note = await compact.maybe_compact(FakeCfg(), h, used=900, limit=1000, fraction=0.5, keep_last=2)

    assert note is not None
    assert h[0]["role"] == "user" and "dense summary of what happened" in h[0]["content"]
    assert h[1]["role"] == "user"
    assert h[1]["content"].startswith("[Action ledger for dropped range]")
    assert "dense summary" not in h[1]["content"]
    assert "read(" in h[1]["content"]


@pytest.mark.asyncio
async def test_compaction_ledger_is_deterministic_across_runs(monkeypatch):
    monkeypatch.setattr(llm, "stream", _scripted_summary("summary"))

    h1 = compaction_history()
    h2 = compaction_history()
    await compact.maybe_compact(FakeCfg(), h1, used=900, limit=1000, fraction=0.5, keep_last=2)
    await compact.maybe_compact(FakeCfg(), h2, used=900, limit=1000, fraction=0.5, keep_last=2)

    assert h1[1]["content"] == h2[1]["content"]


@pytest.mark.asyncio
async def test_compaction_ledger_caps_each_entry(monkeypatch):
    monkeypatch.setattr(llm, "stream", _scripted_summary("summary"))

    h = compaction_history(rounds=2, result="z" * 5000)
    await compact.maybe_compact(FakeCfg(), h, used=900, limit=1000, fraction=0.5, keep_last=1)

    ledger_line = h[1]["content"].splitlines()[1]
    assert len(ledger_line) <= compact._COMPACT_ENTRY_CHARS


@pytest.mark.asyncio
async def test_compaction_ledger_covers_every_dropped_call_not_just_recent_ones(monkeypatch):
    """Distinct from the live trajectory block's ACTION_LINE_CAP=25 -- the
    compaction ledger must not silently drop calls from a big dropped range."""
    monkeypatch.setattr(llm, "stream", _scripted_summary("summary"))

    h = compaction_history(rounds=30)
    await compact.maybe_compact(FakeCfg(), h, used=900, limit=1000, fraction=0.5, keep_last=2)

    ledger_lines = [l for l in h[1]["content"].splitlines() if l.startswith("read(")]
    assert len(ledger_lines) > 25


@pytest.mark.asyncio
async def test_compaction_caps_llm_input_at_summary_input_chars(monkeypatch):
    seen: list = []
    monkeypatch.setattr(llm, "stream", _scripted_summary("summary", sink=seen))

    h = compaction_history(rounds=400, result="y" * 400)
    await compact.maybe_compact(FakeCfg(), h, used=900, limit=1000, fraction=0.5, keep_last=2)

    transcript = seen[0][1]["content"]
    assert len(transcript) <= compact._COMPACT_SUMMARY_INPUT_CHARS


@pytest.mark.asyncio
async def test_compaction_ledger_says_so_when_dropped_range_has_no_tool_calls(monkeypatch):
    monkeypatch.setattr(llm, "stream", _scripted_summary("summary"))

    h = [{"role": "user", "content": "hi"},
         {"role": "assistant", "content": "just talk, no tools"},
         {"role": "user", "content": "more talk"},
         {"role": "assistant", "content": "still no tools"},
         {"role": "user", "content": "final"}]
    note = await compact.maybe_compact(FakeCfg(), h, used=900, limit=1000, fraction=0.5, keep_last=1)

    assert note is not None
    assert "(no tool calls in this range)" in h[1]["content"]
