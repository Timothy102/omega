from pathlib import Path

import pytest

from omega import instructions


@pytest.fixture(autouse=True)
def no_global_file(tmp_path, monkeypatch):
    """Isolate from the real ~/.omega/OMEGA.md, which may exist on the
    machine running these tests."""
    monkeypatch.setattr(instructions, "GLOBAL_PATH", tmp_path / "unused" / "OMEGA.md")
    yield


def _git_root(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    return root


def test_empty_when_nothing_found(tmp_path):
    root = _git_root(tmp_path)
    assert instructions.load(cwd=root) == ""
    assert instructions.system_block(cwd=root) == ""


def test_reads_root_omega_md(tmp_path):
    root = _git_root(tmp_path)
    (root / "OMEGA.md").write_text("use uv, not pip")
    text = instructions.load(cwd=root)
    assert "use uv, not pip" in text
    assert "# Instructions (OMEGA.md at .)" in text


def test_walks_from_git_root_down_to_cwd(tmp_path):
    root = _git_root(tmp_path)
    (root / "OMEGA.md").write_text("root rule")
    sub = root / "packages" / "api"
    sub.mkdir(parents=True)
    (sub / "OMEGA.md").write_text("api-specific rule")

    text = instructions.load(cwd=sub)
    assert text.index("root rule") < text.index("api-specific rule")
    assert "# Instructions (OMEGA.md at .)" in text
    assert "# Instructions (OMEGA.md at packages/api)" in text


def test_claude_md_is_a_per_location_fallback(tmp_path):
    root = _git_root(tmp_path)
    (root / "CLAUDE.md").write_text("fallback rule")
    text = instructions.load(cwd=root)
    assert "fallback rule" in text
    assert "# Instructions (CLAUDE.md at .)" in text


def test_omega_md_wins_over_claude_md_at_the_same_location(tmp_path):
    root = _git_root(tmp_path)
    (root / "OMEGA.md").write_text("omega wins")
    (root / "CLAUDE.md").write_text("claude loses")
    text = instructions.load(cwd=root)
    assert "omega wins" in text
    assert "claude loses" not in text


def test_local_instructions_md_is_appended(tmp_path):
    root = _git_root(tmp_path)
    (root / ".omega").mkdir()
    (root / ".omega" / "instructions.md").write_text("local note")
    text = instructions.load(cwd=root)
    assert "local note" in text
    assert "# Instructions (instructions.md at .omega/instructions.md)" in text


def test_global_omega_md_is_included_first(tmp_path, monkeypatch):
    global_path = tmp_path / "home" / ".omega" / "OMEGA.md"
    global_path.parent.mkdir(parents=True)
    global_path.write_text("global rule")
    monkeypatch.setattr(instructions, "GLOBAL_PATH", global_path)

    root = _git_root(tmp_path)
    (root / "OMEGA.md").write_text("project rule")
    text = instructions.load(cwd=root)
    assert text.index("global rule") < text.index("project rule")


def test_falls_back_to_cwd_when_no_git_root(tmp_path):
    lone = tmp_path / "no_git_here"
    lone.mkdir()
    (lone / "OMEGA.md").write_text("standalone rule")
    text = instructions.load(cwd=lone)
    assert "standalone rule" in text


def test_caps_total_length_with_hint(tmp_path, monkeypatch):
    monkeypatch.setattr(instructions, "MAX_CHARS", 200)
    root = _git_root(tmp_path)
    (root / "OMEGA.md").write_text("x" * 5000)
    text = instructions.load(cwd=root)
    assert len(text) < 5000
    assert "truncated" in text
    assert "`read`" in text
