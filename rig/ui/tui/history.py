"""~/.rig/history file-backed line history with up/down cycling. Pure Python
(no Textual dependency) so it is trivially unit-testable and reusable if
another front end ever wants it."""
from __future__ import annotations

from pathlib import Path


class InputHistory:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lines: list[str] = self._load()
        self.pos: int = len(self.lines)

    def _load(self) -> list[str]:
        if not self.path.exists():
            return []
        return [line for line in self.path.read_text().splitlines() if line]

    def append(self, line: str) -> None:
        self.lines.append(line)
        self.pos = len(self.lines)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as f:
            f.write(line + "\n")

    def prev(self, current: str) -> str | None:
        """One entry back, or None if already at the oldest entry or the
        input holds unsaved free text (not empty, not a history entry)."""
        if self.pos <= 0 or not self._showing(current):
            return None
        self.pos -= 1
        return self.lines[self.pos]

    def next(self, current: str) -> str | None:
        """One entry forward, "" once past the newest entry, or None if the
        input holds unsaved free text."""
        if not self._showing(current):
            return None
        if self.pos >= len(self.lines) - 1:
            self.pos = len(self.lines)
            return ""
        self.pos += 1
        return self.lines[self.pos]

    def _showing(self, current: str) -> bool:
        return current == "" or (0 <= self.pos < len(self.lines) and self.lines[self.pos] == current)

    def reset(self) -> None:
        self.pos = len(self.lines)
