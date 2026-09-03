import json

import pytest

from rig import artifacts, permissions, tools
from rig.llm import ToolCall


def call(name, **args):
    return ToolCall("id", name, json.dumps(args))


@pytest.fixture(autouse=True)
def isolate_artifacts_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(artifacts, "DIR", tmp_path)
    yield


@pytest.fixture
def with_session(monkeypatch):
    monkeypatch.setattr(tools, "SESSION_ID", "sess1")
    return "sess1"


@pytest.mark.asyncio
async def test_large_result_is_offloaded(with_session, tmp_path):
    big = tmp_path / "big.txt"
    big.write_text("x" * 5000)
    result = await tools.run(call("read", path=str(big), limit=10000))
    assert "saved as artifact" in result
    assert "fetch_result(" in result


@pytest.mark.asyncio
async def test_small_result_is_not_offloaded(with_session, tmp_path):
    small = tmp_path / "small.txt"
    small.write_text("hi")
    result = await tools.run(call("read", path=str(small)))
    assert "saved as artifact" not in result
    assert "hi" in result


@pytest.mark.asyncio
async def test_no_offload_when_session_id_is_none(tmp_path):
    assert tools.SESSION_ID is None
    big = tmp_path / "big.txt"
    big.write_text("x" * 5000)
    result = await tools.run(call("read", path=str(big), limit=10000))
    assert "saved as artifact" not in result
    assert len(result) > 4000


@pytest.mark.asyncio
async def test_fetch_result_round_trip_on_offloaded_id(with_session, tmp_path):
    big = tmp_path / "big.txt"
    content = "x" * 5000
    big.write_text(content)
    offloaded = await tools.run(call("read", path=str(big), limit=10000))

    marker = "saved as artifact "
    start = offloaded.index(marker) + len(marker)
    artifact_id = offloaded[start:].split(" ", 1)[0].rstrip(".—-)")

    fetched = await tools.run(call("fetch_result", id=artifact_id, limit=10000))
    assert fetched == artifacts.fetch("sess1", artifact_id, limit=10000)


@pytest.mark.asyncio
async def test_ask_user_non_interactive_returns_error_when_unset():
    tools.ASK_USER = None
    result = await tools.run(call("ask_user", question="which one?"))
    assert "non-interactively" in result


@pytest.mark.asyncio
async def test_ask_user_returns_callback_answer_when_set():
    async def fake(question, options, multi_select):
        assert question == "which one?"
        return "the answer"

    tools.ASK_USER = fake
    try:
        result = await tools.run(call("ask_user", question="which one?"))
    finally:
        tools.ASK_USER = None
    assert result == "the answer"


@pytest.mark.parametrize("name", ["fetch_result", "list_artifacts", "ask_user"])
def test_permissions_allow_new_readonly_tools(name):
    assert permissions.decide(name, {})[0] == permissions.ALLOW


@pytest.mark.parametrize("name", ["save_artifact", "update_artifact"])
def test_permissions_allow_artifact_store_writes(name):
    assert permissions.decide(name, {})[0] == permissions.ALLOW
