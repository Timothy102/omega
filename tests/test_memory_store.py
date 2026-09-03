import pytest

from rig.memory import store


@pytest.fixture(autouse=True)
def isolate_global_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "GLOBAL_DIR", tmp_path / "global")
    yield


@pytest.fixture
def git_cwd(tmp_path):
    (tmp_path / ".git").mkdir()
    return tmp_path


def test_write_and_get_by_id_and_title(git_cwd):
    node_id = store.write_node("project", "fact", "Auth uses JWT", "see auth.py",
                               source_session_id="s1")
    assert store.get("project", node_id)["title"] == "Auth uses JWT"
    assert store.get("project", "auth uses jwt")["id"] == node_id
    assert store.get("project", "no such thing") is None


def test_write_node_rejects_invalid_enum(git_cwd):
    with pytest.raises(ValueError):
        store.write_node("project", "not-a-type", "t", "b")
    with pytest.raises(ValueError):
        store.write_node("project", "fact", "t", "b", volatility="bogus")


def test_add_edge_rejects_invalid_relation(git_cwd):
    a = store.write_node("project", "fact", "A", "a")
    b = store.write_node("project", "fact", "B", "b")
    with pytest.raises(ValueError):
        store.add_edge("project", a, b, "bogus_relation")


def test_fts_search_finds_and_ranks_by_relevance(git_cwd):
    exact = store.write_node("project", "fact", "migrations migrations migrations",
                             "migrations migrations migrations migrations migrations")
    tangential = store.write_node("project", "fact", "unrelated note",
                                  "this note mentions migrations only once in passing")
    hits = store.search("project", "migrations")
    ids = [h["id"] for h in hits]
    assert exact in ids and tangential in ids
    assert ids.index(exact) < ids.index(tangential)


def test_search_excludes_superseded_by_default_but_can_include(git_cwd):
    old = store.write_node("project", "fact", "old rule", "always use tabs")
    new = store.write_node("project", "fact", "new rule", "always use spaces")
    store.mark_superseded("project", old, new)

    hits = store.search("project", "rule")
    assert old not in [h["id"] for h in hits]

    hits_all = store.search("project", "rule", include_superseded=True)
    assert old in [h["id"] for h in hits_all]


def test_search_sanitizes_a_query_with_quotes_and_operators(git_cwd):
    store.write_node("project", "fact", "weird query test",
                     'body mentions AND "quoted" text OR other things')
    # A raw FTS5 syntax error (unbalanced quote, bare boolean operators) must
    # not raise -- it should be retried with each term quoted.
    result = store.search("project", 'unbalanced " quote AND OR')
    assert isinstance(result, list)


def test_neighbors_depth_is_hard_capped_at_two(git_cwd):
    a = store.write_node("project", "fact", "A", "a")
    b = store.write_node("project", "fact", "B", "b")
    c = store.write_node("project", "fact", "C", "c")
    d = store.write_node("project", "fact", "D", "d")
    store.add_edge("project", a, b, "relates_to")
    store.add_edge("project", b, c, "relates_to")
    store.add_edge("project", c, d, "relates_to")

    one_hop = {n["id"] for n in store.neighbors("project", a, depth=1)}
    assert one_hop == {b}

    capped = {n["id"] for n in store.neighbors("project", a, depth=99)}
    assert capped == {b, c}
    assert d not in capped


def test_neighbors_annotated_with_relation_and_direction(git_cwd):
    a = store.write_node("project", "fact", "A", "a")
    b = store.write_node("project", "fact", "B", "b")
    store.add_edge("project", a, b, "depends_on")
    [nb] = store.neighbors("project", b, depth=1)
    assert nb["id"] == a
    assert nb["relation"] == "depends_on"
    assert nb["direction"] == "in"


def test_touch_bumps_access_count_and_last_accessed(git_cwd):
    node_id = store.write_node("project", "fact", "t", "b")
    before = store.get("project", node_id)
    store.touch("project", node_id)
    after = store.get("project", node_id)
    assert after["access_count"] == before["access_count"] + 1
    assert after["last_accessed"] >= before["last_accessed"]


def test_consolidation_counters_round_trip(git_cwd):
    assert store.since_consolidation("project") == 0
    store.bump_since_consolidation("project")
    store.bump_since_consolidation("project")
    assert store.since_consolidation("project") == 2
    store.reset_consolidation("project")
    assert store.since_consolidation("project") == 0


def test_gitignore_bootstrap_happens_for_a_git_cwd(git_cwd):
    store.write_node("project", "fact", "t", "b")
    gitignore = git_cwd / ".gitignore"
    assert gitignore.exists()
    assert ".rig/" in gitignore.read_text().splitlines()
    assert (git_cwd / ".rig").is_dir()


def test_gitignore_bootstrap_does_not_happen_for_a_non_git_cwd(tmp_path):
    store.write_node("project", "fact", "t", "b", cwd=str(tmp_path))
    assert not (tmp_path / ".gitignore").exists()
    assert (tmp_path / ".rig").is_dir()


def test_gitignore_bootstrap_appends_without_duplicating(git_cwd):
    gitignore = git_cwd / ".gitignore"
    gitignore.write_text("node_modules/\n")
    store.write_node("project", "fact", "t", "b")
    store.write_node("project", "fact", "t2", "b2")
    lines = gitignore.read_text().splitlines()
    assert lines.count(".rig/") == 1
    assert "node_modules/" in lines


def test_invalid_scope_raises(git_cwd):
    with pytest.raises(ValueError):
        store.write_node("nowhere", "fact", "t", "b")


def test_global_and_project_scopes_are_isolated(git_cwd):
    p = store.write_node("project", "fact", "proj note", "x")
    g = store.write_node("global", "fact", "global note", "y")
    assert store.get("project", g) is None
    assert store.get("global", p) is None
