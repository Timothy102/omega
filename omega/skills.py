"""Skill discovery and the `skill` tool.

A skill is a markdown checklist (YAML frontmatter + body) the model loads on
demand and follows in the SAME conversation -- not a subagent. Spawning a
subagent would hide the steps from the transcript and lose the model's
accumulated context on this task; a skill is meant to steer the very loop
that's already running, the way a human would open a runbook mid-task.

Discovery mirrors Claude Code's `SKILL.md` format so a user's existing
``~/.claude/skills/*`` work here unchanged, plus two omega-specific
locations:

- ``.omega/skills/*/SKILL.md``   -- project, highest precedence
- ``~/.omega/skills/*/SKILL.md`` -- global
- ``~/.claude/skills/*/SKILL.md`` -- Claude Code's own skills, lowest precedence

Plugin subdirectories (Claude Code's ``plugin:skill`` namespacing) are not
walked -- only the top-level ``*/SKILL.md`` layout.
"""
import os
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from . import tools

INDEX_MAX = 40
DESC_MAX = 90
BODY_MAX = 24_000
SKILL_TOOL = "skill"

_PROJECT_SUBDIR = ".omega/skills"
_GLOBAL_DIR = Path.home() / ".omega" / "skills"
_CLAUDE_DIR = Path.home() / ".claude" / "skills"

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?\n)---\s*\n?", re.DOTALL)
_MD_LINK_RE = re.compile(r"(\]\()([^)\s]+)(\))")


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    path: Path
    source: str  # "project" | "global" | "claude"


def _parse(path: Path, source: str) -> Skill | None:
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return None
    name, description = path.parent.name, ""
    m = _FRONTMATTER_RE.match(text)
    if m:
        try:
            meta = yaml.safe_load(m.group(1))
        except yaml.YAMLError:
            meta = None
        if isinstance(meta, dict):
            name = str(meta.get("name") or name)
            description = str(meta.get("description") or "")
    return Skill(name=name, description=description, path=path, source=source)


def _discover(root: Path, source: str) -> dict[str, Skill]:
    if not root.is_dir():
        return {}
    found: dict[str, Skill] = {}
    for skill_md in sorted(root.glob("*/SKILL.md")):
        parsed = _parse(skill_md, source)
        if parsed is not None:
            found[parsed.name] = parsed
    return found


def catalog(cwd: str | Path | None = None) -> list[Skill]:
    """Project overrides global overrides claude, keyed by skill name."""
    cwd_path = Path(cwd) if cwd else Path(os.getcwd())
    merged: dict[str, Skill] = {}
    merged.update(_discover(_CLAUDE_DIR, "claude"))
    merged.update(_discover(_GLOBAL_DIR, "global"))
    merged.update(_discover(cwd_path / _PROJECT_SUBDIR, "project"))
    return sorted(merged.values(), key=lambda s: s.name)


def _truncate_desc(description: str, limit: int = DESC_MAX) -> str:
    description = " ".join(description.split())
    if len(description) <= limit:
        return description
    return description[: limit - 1].rstrip() + "…"


def render_index(cwd: str | Path | None = None) -> str:
    entries = catalog(cwd)[:INDEX_MAX]
    if not entries:
        return ""
    lines = [f"{s.name} — {_truncate_desc(s.description)}" for s in entries]
    return ("## Skills\n" + "\n".join(lines) +
            "\n\nCall `skill(name)` to load one; follow it as a checklist "
            "in this same conversation.")


def system_block(cwd: str | Path | None = None) -> str:
    return render_index(cwd)


def find(name: str, cwd: str | Path | None = None) -> Skill | None:
    for s in catalog(cwd):
        if s.name == name:
            return s
    return None


def _rewrite_refs(body: str, skill_dir: Path) -> str:
    """A relative link only resolves from the skill's own directory; the
    `read` tool has no notion of that, so rewrite it to an absolute path
    before the model ever sees it."""
    def repl(m: re.Match[str]) -> str:
        prefix, ref, suffix = m.group(1), m.group(2), m.group(3)
        if ref.startswith(("/", "http://", "https://", "#", "mailto:")):
            return m.group(0)
        resolved = (skill_dir / ref).resolve()
        return f"{prefix}{resolved}{suffix}" if resolved.exists() else m.group(0)

    return _MD_LINK_RE.sub(repl, body)


def load_body(name: str, cwd: str | Path | None = None) -> str | None:
    found = find(name, cwd)
    if found is None:
        return None
    text = found.path.read_text(errors="replace")
    body = _FRONTMATTER_RE.sub("", text, count=1).strip()
    body = _rewrite_refs(body, found.path.parent)
    if len(body) > BODY_MAX:
        dropped = len(body) - BODY_MAX
        body = f"{body[:BODY_MAX]}\n...[truncated {dropped} chars]"
    return f'<skill name="{found.name}">\n{body}\n</skill>'


S = {"type": "string"}


@tools.tool(
    SKILL_TOOL,
    "Load a skill by name -- a pre-written checklist for a sub-workflow "
    "(from the catalog in the system prompt). Returns its instructions to "
    "follow in this same conversation; it does not spawn another agent.",
    {"name": S}, ["name"])
def _skill(name: str) -> str:
    body = load_body(name)
    if body is not None:
        return body
    available = ", ".join(s.name for s in catalog()) or "(none found)"
    return f"error: no skill named {name!r}. Available: {available}"
