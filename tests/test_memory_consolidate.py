import json

import pytest

from omega import config, llm
from omega.memory import consolidate, store


@pytest.fixture(autouse=True)
def isolate_global_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "GLOBAL_DIR", tmp_path / "global")
    yield


@pytest.fixture
def git_cwd(tmp_path):
    (tmp_path / ".git").mkdir()
    return tmp_path


@pytest.fixture
def cfg():
    provider = config.Provider("p", "http://x", "key")
    return config.Config(roles={"memory": config.Role("m", provider, 128000)})


def _write(scope, **kw):
    defaults = dict(type="fact", title="t", body="b", confidence=0.8,
                    volatility="stable", sensitivity="normal", importance=0.5)
    defaults.update(kw)
    return store.write_node(scope, **defaults)


def _stub_llm(monkeypatch, obj):
    class FakeTurn:
        def __init__(self, text):
            self.text = text

    async def fake_stream(role, messages, tools=None):
        yield "done", FakeTurn(json.dumps(obj))

    monkeypatch.setattr(llm, "stream", fake_stream)


@pytest.mark.asyncio
async def test_returns_empty_string_under_min_new(cfg, git_cwd):
    _write("project", title="one node", body="x")
    assert store.since_consolidation("project") == 0  # write_node alone doesn't bump it
    result = await consolidate.run(cfg, "project", min_new=5, force=False)
    assert result == ""


@pytest.mark.asyncio
async def test_force_runs_even_under_threshold_and_reports_nothing_to_consolidate(cfg, git_cwd, monkeypatch):
    _write("project", title="one node", body="x")
    _stub_llm(monkeypatch, {"merge": [], "contradict": [], "retag": []})
    result = await consolidate.run(cfg, "project", force=True)
    assert result == "memory: nothing to consolidate"
    assert store.since_consolidation("project") == 0


@pytest.mark.asyncio
async def test_applies_merge_contradict_and_retag(cfg, git_cwd, monkeypatch):
    keep = _write("project", title="keep me", body="old body")
    drop = _write("project", title="drop me", body="duplicate")
    a = _write("project", title="claim A", body="x is true")
    b = _write("project", title="claim B", body="x is false")
    retag_target = _write("project", title="retag me", body="stale", volatility="stable",
                          importance=0.2)

    _stub_llm(monkeypatch, {
        "merge": [{"keep": keep, "drop": drop, "merged_body": "merged body"}],
        "contradict": [[a, b]],
        "retag": [{"id": retag_target, "volatility": "volatile", "importance": 0.9}],
    })

    result = await consolidate.run(cfg, "project", force=True)
    assert result == "memory: merged 1, flagged 1 contradiction, retagged 1"

    remaining_ids = {n["id"] for n in store.all_nodes("project")}
    assert keep not in remaining_ids and drop not in remaining_ids
    merged_nodes = [n for n in store.all_nodes("project") if n["body"] == "merged body"]
    assert len(merged_nodes) == 1
    old_keep = store.get("project", keep)
    old_drop = store.get("project", drop)
    assert old_keep["superseded_by"] == merged_nodes[0]["id"]
    assert old_drop["superseded_by"] == merged_nodes[0]["id"]

    neighbors_of_b = {n["id"] for n in store.neighbors("project", b, depth=1)}
    assert a in neighbors_of_b

    retagged = store.get("project", retag_target)
    assert retagged["volatility"] == "volatile"
    assert retagged["importance"] == 0.9

    assert store.since_consolidation("project") == 0


@pytest.mark.asyncio
async def test_unparseable_model_output_returns_skip_message(cfg, git_cwd, monkeypatch):
    _write("project", title="one node", body="x")

    async def fake_stream(role, messages, tools=None):
        yield "done", type("T", (), {"text": "not json at all"})()

    monkeypatch.setattr(llm, "stream", fake_stream)
    result = await consolidate.run(cfg, "project", force=True)
    assert result == "consolidation skipped: unparseable model output"


@pytest.mark.asyncio
async def test_parses_response_wrapped_in_code_fences(cfg, git_cwd, monkeypatch):
    _write("project", title="one node", body="x")

    async def fake_stream(role, messages, tools=None):
        text = "```json\n" + json.dumps({"merge": [], "contradict": [], "retag": []}) + "\n```"
        yield "done", type("T", (), {"text": text})()

    monkeypatch.setattr(llm, "stream", fake_stream)
    result = await consolidate.run(cfg, "project", force=True)
    assert result == "memory: nothing to consolidate"


@pytest.mark.asyncio
async def test_no_recent_nodes_reports_nothing_to_consolidate(cfg, git_cwd):
    result = await consolidate.run(cfg, "project", force=True)
    assert result == "memory: nothing to consolidate"


@pytest.mark.asyncio
async def test_falls_back_to_compact_role_when_memory_role_missing(git_cwd, monkeypatch):
    provider = config.Provider("p", "http://x", "key")
    cfg_no_memory = config.Config(roles={"compact": config.Role("m", provider, 128000)})
    _write("project", title="one node", body="x")
    _stub_llm(monkeypatch, {"merge": [], "contradict": [], "retag": []})
    result = await consolidate.run(cfg_no_memory, "project", force=True)
    assert result == "memory: nothing to consolidate"
