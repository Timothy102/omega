"""Project verification checks -- auto-detected from files on disk, or taken
from a `verify.checks` config override. See loop.py for how these are run at
the end of a BUILD-mode turn."""
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

TAIL_LINES = 40
TAIL_CHARS = 4000


@dataclass(frozen=True)
class Check:
    name: str
    command: str
    kind: Literal["test", "lint", "types"]


@dataclass(frozen=True)
class Result:
    check: Check
    ok: bool
    exit_code: int
    tail: str


def _safe_read(path: Path) -> str:
    try:
        return path.read_text()
    except OSError:
        return ""


def _has_mypy_config(root: Path, pyproject_text: str) -> bool:
    if "[tool.mypy]" in pyproject_text:
        return True
    if (root / "mypy.ini").exists():
        return True
    setup_cfg = root / "setup.cfg"
    return setup_cfg.exists() and "[mypy]" in _safe_read(setup_cfg)


def _npm_checks(root: Path, pkg: Path) -> list[Check]:
    try:
        data = json.loads(_safe_read(pkg) or "{}")
    except json.JSONDecodeError:
        return []
    scripts = data.get("scripts") if isinstance(data, dict) else None
    if not isinstance(scripts, dict):
        return []
    runner = ("pnpm run" if (root / "pnpm-lock.yaml").exists()
             else "yarn run" if (root / "yarn.lock").exists()
             else "npm run")
    out: list[Check] = []
    if "test" in scripts:
        out.append(Check("test", f"{runner} test", "test"))
    if "lint" in scripts:
        out.append(Check("lint", f"{runner} lint", "lint"))
    if "typecheck" in scripts:
        out.append(Check("typecheck", f"{runner} typecheck", "types"))
    return out


def detect(cwd: str) -> list[Check]:
    """Project checks inferred from files present in `cwd` -- no config
    override applied here; see `resolve()` for that."""
    root = Path(cwd)
    checks: list[Check] = []

    pyproject = root / "pyproject.toml"
    pyproject_text = _safe_read(pyproject) if pyproject.exists() else ""
    uv_prefix = "uv run " if (root / "uv.lock").exists() else ""

    if "pytest" in pyproject_text or (root / "tests").is_dir():
        runner = f"{uv_prefix}pytest -q -x" if uv_prefix else "python -m pytest -q -x"
        checks.append(Check("pytest", runner, "test"))
    if ("[tool.ruff]" in pyproject_text or (root / "ruff.toml").exists()
            or (root / ".ruff.toml").exists()):
        checks.append(Check("ruff", f"{uv_prefix}ruff check", "lint"))
    if _has_mypy_config(root, pyproject_text):
        checks.append(Check("mypy", f"{uv_prefix}mypy", "types"))

    pkg = root / "package.json"
    if pkg.exists():
        checks.extend(_npm_checks(root, pkg))

    if (root / "Cargo.toml").exists():
        checks.append(Check("cargo-test", "cargo test", "test"))
    if (root / "go.mod").exists():
        checks.append(Check("go-test", "go test ./...", "test"))

    makefile = root / "Makefile"
    if makefile.exists() and re.search(r"(?m)^test\s*:", _safe_read(makefile)):
        checks.append(Check("make-test", "make test", "test"))

    return checks


def resolve(cwd: str, override: list[str] | None) -> list[Check]:
    """`override` (config.Config.verify_checks) replaces auto-detection
    entirely when set -- each entry is run verbatim as a shell command."""
    if override is not None:
        return [Check(name=cmd, command=cmd, kind="test") for cmd in override]
    return detect(cwd)


def run(checks: list[Check], cwd: str, timeout: int = 300) -> list[Result]:
    results: list[Result] = []
    for check in checks:
        try:
            proc = subprocess.run(check.command, shell=True, cwd=cwd, capture_output=True,
                                  text=True, timeout=timeout)
            code = proc.returncode
            output = (proc.stdout or "") + (proc.stderr or "")
        except subprocess.TimeoutExpired:
            code = -1
            output = f"(timed out after {timeout}s)"
        except (OSError, subprocess.SubprocessError) as e:
            code = -1
            output = f"error running check: {type(e).__name__}: {e}"
        tail = "\n".join(output.strip().splitlines()[-TAIL_LINES:])
        if len(tail) > TAIL_CHARS:
            tail = tail[-TAIL_CHARS:]
        results.append(Result(check=check, ok=code == 0, exit_code=code, tail=tail))
    return results


def summarize(results: list[Result]) -> str:
    return "; ".join(f"{r.check.name} {'ok' if r.ok else f'FAILED(exit {r.exit_code})'}"
                     for r in results)
