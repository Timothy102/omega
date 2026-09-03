import json

from omega import compact, session


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


def test_session_list_rows_never_wrap(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "DIR", tmp_path)
    s = session.Session.new(cwd=str(tmp_path))
    s.history = [{"role": "user", "content": "x" * 400}]
    s.save()
    import shutil
    width = shutil.get_terminal_size((80, 24)).columns
    assert all(len(line) < width for line in session.render_list().splitlines())
