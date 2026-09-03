from pathlib import Path

from .app import OmegaApp

# Module-level so tests can `monkeypatch.setattr(tui, "HISTORY", tmp_path / "history")`
# before constructing an OmegaApp; app.py re-reads this via a deferred import.
HISTORY: Path = Path.home() / ".omega" / "history"

__all__ = ["OmegaApp", "HISTORY"]
