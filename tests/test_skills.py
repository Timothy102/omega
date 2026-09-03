import pytest

from omega import skills, tools
from omega.llm import ToolCall


def call(tool_name, **args):
    import json
    return ToolCall("id", tool_name, json.dumps(args))


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    """Real ~/.omega/skills and ~/.claude/skills may exist on the machine
    running these tests -- point everything at empty tmp dirs instead."""
    monkeypatch.setattr(skills, "_GLOBAL_DIR", tmp_path / "global_skills")
    monkeypatch.setattr(skills, "_CLAUDE_DIR", tmp_path / "claude_skills")
    yield


def _write_skill(root, dirname, *, name=None, description="", body="do the thing",
                  extra_frontmatter=""):
    d = root / dirname
    d.mkdir(parents=True, exist_ok=True)
    frontmatter = ""
    if name is not None or description or extra_frontmatter:
        lines = ["---"]
        if name is not None:
            lines.append(f"name: {name}")
        if description:
            lines.append(f"description: {description}")
        lines.append(extra_frontmatter)
        lines.append("---")
        frontmatter = "\n".join(line for line in lines if line) + "\n"
    (d / "SKILL.md").write_text(f"{frontmatter}{body}")
    return d


def test_discovery_finds_project_global_and_claude_skills(tmp_path):
    _write_skill(tmp_path / ".omega" / "skills", "proj", name="proj-skill",
                 description="a project skill")
    _write_skill(skills._GLOBAL_DIR, "glob", name="global-skill",
                 description="a global skill")
    _write_skill(skills._CLAUDE_DIR, "cla", name="claude-skill",
                 description="a claude skill")

    found = {s.name: s for s in skills.catalog(cwd=tmp_path)}
    assert set(found) == {"proj-skill", "global-skill", "claude-skill"}
    assert found["proj-skill"].source == "project"
    assert found["global-skill"].source == "global"
    assert found["claude-skill"].source == "claude"


def test_project_overrides_global_overrides_claude(tmp_path):
    _write_skill(tmp_path / ".omega" / "skills", "x", name="dup", description="project version")
    _write_skill(skills._GLOBAL_DIR, "x", name="dup", description="global version")
    _write_skill(skills._CLAUDE_DIR, "x", name="dup", description="claude version")

    found = skills.find("dup", cwd=tmp_path)
    assert found is not None
    assert found.source == "project"
    assert found.description == "project version"


def test_global_overrides_claude_when_no_project_entry(tmp_path):
    _write_skill(skills._GLOBAL_DIR, "x", name="dup", description="global version")
    _write_skill(skills._CLAUDE_DIR, "x", name="dup", description="claude version")

    found = skills.find("dup", cwd=tmp_path)
    assert found is not None
    assert found.source == "global"


def test_frontmatter_parsing(tmp_path):
    _write_skill(tmp_path / ".omega" / "skills", "fm", name="my-skill",
                 description="Does a thing.", body="# My Skill\n\nSteps here.")
    found = skills.find("my-skill", cwd=tmp_path)
    assert found is not None
    assert found.description == "Does a thing."


def test_missing_frontmatter_falls_back_to_dirname(tmp_path):
    _write_skill(tmp_path / ".omega" / "skills", "no-frontmatter", name=None,
                 body="just a body, no frontmatter")
    found = skills.find("no-frontmatter", cwd=tmp_path)
    assert found is not None
    assert found.description == ""


def test_relative_link_rewritten_to_absolute_path(tmp_path):
    d = _write_skill(tmp_path / ".omega" / "skills", "refs", name="refs-skill",
                     body="See [the template](template.md) for details.")
    (d / "template.md").write_text("template contents")

    body = skills.load_body("refs-skill", cwd=tmp_path)
    assert body is not None
    assert str((d / "template.md").resolve()) in body
    assert "(template.md)" not in body


def test_absolute_and_web_links_left_alone(tmp_path):
    _write_skill(tmp_path / ".omega" / "skills", "refs2", name="refs2-skill",
                 body="[abs](/etc/hosts) and [web](https://example.com/x)")
    body = skills.load_body("refs2-skill", cwd=tmp_path)
    assert body is not None
    assert "(/etc/hosts)" in body
    assert "(https://example.com/x)" in body


def test_nonexistent_relative_link_left_alone(tmp_path):
    _write_skill(tmp_path / ".omega" / "skills", "refs3", name="refs3-skill",
                 body="[missing](nope.md)")
    body = skills.load_body("refs3-skill", cwd=tmp_path)
    assert body is not None
    assert "(nope.md)" in body


def test_frontmatter_stripped_and_wrapped(tmp_path):
    _write_skill(tmp_path / ".omega" / "skills", "wrap", name="wrap-skill",
                 description="d", body="the actual body")
    body = skills.load_body("wrap-skill", cwd=tmp_path)
    assert body == '<skill name="wrap-skill">\nthe actual body\n</skill>'
    assert "description:" not in body
    assert "---" not in body


def test_body_capped_at_body_max(tmp_path, monkeypatch):
    monkeypatch.setattr(skills, "BODY_MAX", 100)
    _write_skill(tmp_path / ".omega" / "skills", "big", name="big-skill",
                 body="y" * 5000)
    body = skills.load_body("big-skill", cwd=tmp_path)
    assert body is not None
    assert len(body) < 5000
    assert "truncated" in body


def test_index_caps_entries_and_description_length(tmp_path, monkeypatch):
    monkeypatch.setattr(skills, "INDEX_MAX", 2)
    for i in range(5):
        _write_skill(tmp_path / ".omega" / "skills", f"s{i}", name=f"skill-{i}",
                     description="d" * 200)
    index = skills.render_index(cwd=tmp_path)
    assert index.count(" — ") == 2
    for line in index.splitlines():
        if " — " in line:
            desc = line.split(" — ", 1)[1]
            assert len(desc) <= skills.DESC_MAX


def test_render_index_mentions_the_skill_tool(tmp_path):
    _write_skill(tmp_path / ".omega" / "skills", "one", name="one", description="d")
    index = skills.render_index(cwd=tmp_path)
    assert index.startswith("## Skills")
    assert "skill(name)" in index


def test_render_index_empty_when_no_skills(tmp_path):
    assert skills.render_index(cwd=tmp_path) == ""
    assert skills.system_block(cwd=tmp_path) == ""


@pytest.mark.asyncio
async def test_skill_tool_returns_body(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_skill(tmp_path / ".omega" / "skills", "runme", name="runme",
                 description="d", body="follow these steps")
    result = await tools.run(call("skill", name="runme"))
    assert "follow these steps" in result
    assert '<skill name="runme">' in result


@pytest.mark.asyncio
async def test_skill_tool_unknown_name_lists_available(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_skill(tmp_path / ".omega" / "skills", "known", name="known", description="d")
    result = await tools.run(call("skill", name="nope"))
    assert "error" in result
    assert "known" in result


@pytest.mark.asyncio
async def test_skill_tool_is_available_in_plan_mode(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_skill(tmp_path / ".omega" / "skills", "planok", name="planok", description="d")
    result = await tools.run(call("skill", name="planok"), allowed=tools.READ_ONLY | {"skill"})
    assert '<skill name="planok">' in result
