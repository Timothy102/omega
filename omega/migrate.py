"""One-time compatibility copy from the pre-rename `.rig` layout to `.omega`.

Runs from `__main__.cli()` before anything else touches config, permissions,
or sessions, so every other module can assume `~/.omega` (and `<cwd>/.omega`)
already hold whatever `.rig` had -- without ever deleting the old directory,
in case something still points at it."""
import os
import shutil
from pathlib import Path

_PRESERVE_MODE = ("config.json", "permissions.json")


def _copy(old: Path, new: Path) -> bool:
    if new.exists() or not old.is_dir():
        return False
    shutil.copytree(old, new)
    for name in _PRESERVE_MODE:
        dst = new / name
        src = old / name
        if dst.exists() and src.exists():
            dst.chmod(src.stat().st_mode & 0o777)
    return True


def migrate_home(home: Path | None = None) -> bool:
    home = home or Path.home()
    return _copy(home / ".rig", home / ".omega")


def migrate_project(cwd: str | None = None) -> bool:
    root = Path(cwd or os.getcwd())
    return _copy(root / ".rig", root / ".omega")


def run(cwd: str | None = None, home: Path | None = None) -> None:
    if migrate_home(home):
        from rich.console import Console
        Console().print("[dim]migrated ~/.rig → ~/.omega (the old directory was left in place)[/dim]")
    migrate_project(cwd)
