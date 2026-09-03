from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

Mode = Literal["build", "plan"]

_REQUIRED = ("name", "prompt", "check")
_MODES = ("build", "plan")

EXAMPLES_DIR = Path(__file__).parent / "examples"


class TaskError(ValueError):
    """A task file is missing a required field, has an invalid value, or the
    given path/directory doesn't resolve to any task -- distinct from
    ValueError so callers can catch just this without swallowing unrelated
    bugs in yaml.safe_load or dataclass construction."""


@dataclass(frozen=True)
class Task:
    name: str
    prompt: str
    check: str
    repo: str = "."
    setup: str | None = None
    timeout_s: int = 600
    mode: Mode = "build"
    tags: tuple[str, ...] = field(default_factory=tuple)
    source: str = ""


def parse_task(raw: dict[str, Any], source: str = "") -> Task:
    if not isinstance(raw, dict):
        raise TaskError(f"{source}: task must be a YAML mapping, got {type(raw).__name__}")
    missing = [f for f in _REQUIRED if not raw.get(f)]
    if missing:
        raise TaskError(f"{source}: missing required field(s): {', '.join(missing)}")

    mode = raw.get("mode", "build")
    if mode not in _MODES:
        raise TaskError(f"{source}: mode must be one of {_MODES}, got {mode!r}")

    timeout_s = raw.get("timeout_s", 600)
    if not isinstance(timeout_s, int) or isinstance(timeout_s, bool) or timeout_s <= 0:
        raise TaskError(f"{source}: timeout_s must be a positive integer, got {timeout_s!r}")

    tags = raw.get("tags") or []
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        raise TaskError(f"{source}: tags must be a list of strings")

    setup = raw.get("setup")
    return Task(
        name=str(raw["name"]), prompt=str(raw["prompt"]), check=str(raw["check"]),
        repo=str(raw.get("repo", ".")), setup=(str(setup) if setup else None),
        timeout_s=timeout_s, mode=mode, tags=tuple(tags), source=source,
    )


def load_task_file(path: Path) -> Task:
    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        raise TaskError(f"{path}: invalid YAML: {e}") from e
    return parse_task(raw or {}, source=str(path))


def discover_task_files(path: str | None) -> list[Path]:
    """`path=None` is the project default `.omega/evals/`; a directory globs
    its *.yaml/*.yml; a file is used as-is."""
    if path is None:
        root = Path.cwd() / ".omega" / "evals"
        if not root.exists():
            return []
        return sorted(root.glob("*.yaml")) + sorted(root.glob("*.yml"))
    p = Path(path).expanduser()
    if p.is_dir():
        return sorted(p.glob("*.yaml")) + sorted(p.glob("*.yml"))
    if p.is_file():
        return [p]
    raise TaskError(f"no such task file or directory: {p}")


def load_tasks(path: str | None = None) -> list[Task]:
    return [load_task_file(f) for f in discover_task_files(path)]


def init_examples(dest: Path) -> list[Path]:
    dest.mkdir(parents=True, exist_ok=True)
    written = []
    for src in sorted(EXAMPLES_DIR.glob("*.yaml")):
        target = dest / src.name
        target.write_text(src.read_text())
        written.append(target)
    return written
