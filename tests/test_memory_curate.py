
import pytest

from rig.memory import curate, store


@pytest.fixture(autouse=True)
def isolate_global_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "GLOBAL_DIR", tmp_path / "global")
    yield


@pytest.fixture
def git_cwd(tmp_path):
    (tmp_path / ".git").mkdir()
    return tmp_path


def _write(scope, **kw):
    defaults = dict(type="fact", title="t", body="b", confidence=0.8,
                    volatility="stable", sensitivity="normal", importance=0.5)
    defaults.update(kw)
    return store.write_node(scope, **defaults)


def test_preamble_excludes_sensitive_nodes(git_cwd):
    _write("project", title="secret note", body="contains a secret", sensitivity="sensitive",
          importance=0.9)
    assert "secret note" not in curate.preamble()


def test_preamble_excludes_volatile_nodes(git_cwd):
    _write("project", title="volatile note", body="changes constantly", volatility="volatile",
          importance=0.9)
    assert "volatile note" not in curate.preamble()


def test_preamble_excludes_superseded_nodes(git_cwd):
    old = _write("project", title="old note", body="outdated", importance=0.9)
    new = _write("project", title="new note", body="current", importance=0.9)
    store.mark_superseded("project", old, new)
    text = curate.preamble()
    assert "old note" not in text
    assert "new note" in text


def test_preamble_excludes_low_importance_personal(git_cwd):
    _write("project", title="low importance personal", body="p", sensitivity="personal",
          importance=0.3, volatility="stable")
    assert "low importance personal" not in curate.preamble()


def test_preamble_excludes_volatile_personal_even_if_important(git_cwd):
    _write("project", title="volatile personal", body="p", sensitivity="personal",
          importance=0.9, volatility="volatile")
    assert "volatile personal" not in curate.preamble()


def test_preamble_includes_high_importance_stable_personal(git_cwd):
    _write("project", title="important personal fact", body="p", sensitivity="personal",
          importance=0.8, volatility="stable")
    assert "important personal fact" in curate.preamble()


def test_preamble_respects_token_budget(git_cwd):
    for i in range(50):
        _write("project", title=f"note {i}", body="x" * 180, importance=0.9)
    text = curate.preamble(budget_tokens=50)
    from rig import compact
    assert compact.estimate_tokens([{"role": "user", "content": text}]) <= 50


def test_preamble_empty_when_nothing_eligible(git_cwd):
    _write("project", title="secret", body="s", sensitivity="sensitive", importance=0.9)
    assert curate.preamble() == ""


def test_preamble_does_not_create_project_db(git_cwd):
    assert not store.db_exists("project")
    curate.preamble()
    assert not store.db_exists("project")


def test_preamble_groups_project_and_global_sections(git_cwd):
    _write("project", title="project thing", body="p", importance=0.9)
    _write("global", title="global thing", body="g", importance=0.9)
    text = curate.preamble()
    assert "## Project" in text and "## Global" in text
    assert text.index("## Project") < text.index("## Global")


def test_project_weight_beats_a_higher_importance_global_node_under_tight_budget(git_cwd):
    # project weight is 1.5x: importance 0.5 project (score 0.75) should still
    # be picked over importance 0.6 global (score 0.6) when only one node fits.
    _write("project", title="proj node", body="p", importance=0.5)
    _write("global", title="global node", body="g", importance=0.6)
    text = curate.preamble(budget_tokens=30)
    assert "proj node" in text
    assert "global node" not in text
