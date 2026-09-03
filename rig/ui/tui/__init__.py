from pathlib import Path

from .app import RigApp

# Module-level so tests can `monkeypatch.setattr(tui, "HISTORY", tmp_path / "history")`
# before constructing a RigApp; app.py re-reads this via a deferred import.
HISTORY: Path = Path.home() / ".rig" / "history"

__all__ = ["RigApp", "HISTORY"]
