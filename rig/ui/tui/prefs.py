"""Per-machine TUI preferences persisted at ~/.rig/ui.json -- separate from
~/.rig/config.json, which holds provider/model/role configuration, not UI
state like whether the side panel is open."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

PATH = Path(os.environ.get("RIG_UI_PREFS", Path.home() / ".rig" / "ui.json"))


def load() -> dict[str, Any]:
    if not PATH.exists():
        return {}
    try:
        return dict(json.loads(PATH.read_text()))
    except (json.JSONDecodeError, OSError):
        return {}


def save(data: dict[str, Any]) -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(data, indent=2) + "\n")
