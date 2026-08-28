import os, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from rig import tools


class Chunk:
    """Minimal stand-in for an OpenAI streaming chunk."""

    def __init__(self, content=None, tool_calls=None, finish_reason=None, usage=None):
        self.usage = usage
        delta = type("D", (), {"content": content, "tool_calls": tool_calls})()
        choice = type("C", (), {"delta": delta, "finish_reason": finish_reason})()
        self.choices = [choice] if (content or tool_calls or finish_reason) else []


def tc(index, id=None, name=None, arguments=None):
    fn = type("F", (), {"name": name, "arguments": arguments})()
    return type("T", (), {"index": index, "id": id, "function": fn})()


@pytest.fixture(autouse=True)
def clean_tool_state(tmp_path, monkeypatch):
    tools.CONFIRM = None
    tools.set_tainted(False)
    monkeypatch.chdir(tmp_path)
    from rig import permissions
    monkeypatch.setattr(permissions, "STORE", tmp_path / "permissions.json")
    yield
    tools.CONFIRM = None
    tools.set_tainted(False)


@pytest.fixture
def approve_all():
    """For tests exercising tool mechanics rather than the permission policy."""
    async def yes(name, args, why):
        return True
    tools.CONFIRM = yes
    yield
    tools.CONFIRM = None
