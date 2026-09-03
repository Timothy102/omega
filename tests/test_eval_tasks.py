from pathlib import Path

import pytest

from omega.eval import tasks


def test_parse_task_requires_name_prompt_check():
    with pytest.raises(tasks.TaskError, match="missing required"):
        tasks.parse_task({"name": "x"}, source="t.yaml")


def test_parse_task_rejects_bad_mode():
    with pytest.raises(tasks.TaskError, match="mode"):
        tasks.parse_task({"name": "x", "prompt": "p", "check": "true", "mode": "bogus"})


def test_parse_task_rejects_bad_timeout():
    with pytest.raises(tasks.TaskError, match="timeout_s"):
        tasks.parse_task({"name": "x", "prompt": "p", "check": "true", "timeout_s": -5})
    with pytest.raises(tasks.TaskError, match="timeout_s"):
        tasks.parse_task({"name": "x", "prompt": "p", "check": "true", "timeout_s": "soon"})


def test_parse_task_rejects_bad_tags():
    with pytest.raises(tasks.TaskError, match="tags"):
        tasks.parse_task({"name": "x", "prompt": "p", "check": "true", "tags": "not-a-list"})


def test_parse_task_defaults():
    t = tasks.parse_task({"name": "x", "prompt": "p", "check": "true"})
    assert t.repo == "." and t.setup is None
    assert t.timeout_s == 600 and t.mode == "build" and t.tags == ()


def test_parse_task_rejects_non_mapping():
    with pytest.raises(tasks.TaskError, match="mapping"):
        tasks.parse_task(["not", "a", "dict"])  # type: ignore[arg-type]


def test_load_task_file(tmp_path: Path):
    p = tmp_path / "a.yaml"
    p.write_text("name: a\nprompt: do it\ncheck: 'true'\ntags: [x, y]\n")
    t = tasks.load_task_file(p)
    assert t.name == "a" and t.tags == ("x", "y") and t.source == str(p)


def test_load_task_file_bad_yaml(tmp_path: Path):
    p = tmp_path / "bad.yaml"
    p.write_text("name: [unterminated\n")
    with pytest.raises(tasks.TaskError, match="invalid YAML"):
        tasks.load_task_file(p)


def test_discover_task_files_default_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    evals = tmp_path / ".omega" / "evals"
    evals.mkdir(parents=True)
    (evals / "a.yaml").write_text("name: a\nprompt: p\ncheck: 'true'\n")
    (evals / "b.yml").write_text("name: b\nprompt: p\ncheck: 'true'\n")
    found = tasks.discover_task_files(None)
    assert {f.name for f in found} == {"a.yaml", "b.yml"}


def test_discover_task_files_no_default_dir_returns_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    assert tasks.discover_task_files(None) == []


def test_discover_task_files_explicit_file(tmp_path: Path):
    p = tmp_path / "one.yaml"
    p.write_text("name: a\nprompt: p\ncheck: 'true'\n")
    assert tasks.discover_task_files(str(p)) == [p]


def test_discover_task_files_missing_path_raises(tmp_path: Path):
    with pytest.raises(tasks.TaskError):
        tasks.discover_task_files(str(tmp_path / "nope"))


def test_load_tasks_from_directory(tmp_path: Path):
    (tmp_path / "a.yaml").write_text("name: a\nprompt: p\ncheck: 'true'\n")
    (tmp_path / "z.yaml").write_text("name: z\nprompt: p\ncheck: 'true'\n")
    loaded = tasks.load_tasks(str(tmp_path))
    assert [t.name for t in loaded] == ["a", "z"]


def test_init_examples_copies_three_tasks(tmp_path: Path):
    written = tasks.init_examples(tmp_path / ".omega" / "evals")
    assert len(written) == 3
    loaded = [tasks.load_task_file(p) for p in written]
    names = {t.name for t in loaded}
    assert names == {"version-flag", "relative-age-negative-delta", "plan-version-flag"}
    modes = {t.mode for t in loaded}
    assert modes == {"build", "plan"}
    for t in loaded:
        assert t.name and t.prompt and t.check
