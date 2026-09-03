import json

import pytest

from omega import tools
from omega.llm import ToolCall
from omega.memory import store


def call(name, **args):
    return ToolCall("id", name, json.dumps(args))


@pytest.fixture(autouse=True)
def isolate_global_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "GLOBAL_DIR", tmp_path / "global")
    yield


@pytest.fixture
def git_cwd(tmp_path):
    (tmp_path / ".git").mkdir()
    return tmp_path


@pytest.mark.asyncio
async def test_remember_writes_a_node(git_cwd):
    result = await tools.run(call("remember", title="uses postgres", body="prod db is postgres 16"))
    assert "remembered" in result
    hits = store.search("project", "postgres")
    assert len(hits) == 1
    assert hits[0]["title"] == "uses postgres"


@pytest.mark.asyncio
async def test_remember_forces_sensitivity_on_api_key(git_cwd):
    result = await tools.run(call("remember", title="stripe key",
                                  body="use sk-abcdefghijklmnopqrstuvwxyz for billing"))
    assert "sensitivity forced to 'sensitive'" in result
    all_nodes = store.all_nodes("project")
    assert all_nodes[0]["sensitivity"] == "sensitive"


@pytest.mark.asyncio
async def test_remember_forces_sensitivity_on_email(git_cwd):
    result = await tools.run(call("remember", title="contact", body="reach me at tim@example.com"))
    assert "sensitivity forced to 'sensitive'" in result
    all_nodes = store.all_nodes("project", include_superseded=True)
    assert all_nodes[0]["sensitivity"] == "sensitive"


@pytest.mark.asyncio
async def test_remember_leaves_sensitivity_normal_when_clean(git_cwd):
    result = await tools.run(call("remember", title="build tool", body="we use make for builds"))
    assert "sensitivity forced" not in result
    all_nodes = store.all_nodes("project")
    assert all_nodes[0]["sensitivity"] == "normal"


@pytest.mark.asyncio
async def test_remember_does_not_treat_paths_or_identifiers_as_tokens(git_cwd):
    body = ("entry point is /Users/someone/Documents/code/project/src/module/ and "
            "the handler is handle_incoming_webhook_event_with_retry_policy")
    result = await tools.run(call("remember", title="layout", body=body, type="file_note"))
    assert "sensitivity forced" not in result
    assert store.all_nodes("project")[0]["sensitivity"] == "normal"


@pytest.mark.asyncio
async def test_remember_honours_caller_sensitivity(git_cwd):
    await tools.run(call("remember", title="timezone", body="works from CET",
                         sensitivity="personal", importance=0.9))
    assert store.all_nodes("project")[0]["sensitivity"] == "personal"


@pytest.mark.asyncio
async def test_remember_resolves_relates_to_by_title(git_cwd):
    await tools.run(call("remember", title="Auth service", body="handles login"))
    result = await tools.run(call("remember", title="Auth bug", body="token expiry off by one",
                                  relates_to=["Auth service", "no such node"]))
    assert "could not resolve relates_to: no such node" in result

    nodes = {n["title"]: n for n in store.all_nodes("project")}
    edges_target = nodes["Auth service"]["id"]
    bug_id = nodes["Auth bug"]["id"]
    neighbors = store.neighbors("project", bug_id, depth=1)
    assert any(n["id"] == edges_target for n in neighbors)


@pytest.mark.asyncio
async def test_recall_scope_both_orders_project_before_global(git_cwd):
    await tools.run(call("remember", title="project fact", body="local thing", scope="project"))
    await tools.run(call("remember", title="global fact", body="local thing", scope="global"))
    result = await tools.run(call("recall", query="local thing", scope="both"))
    assert result.index("project fact") < result.index("global fact")


@pytest.mark.asyncio
async def test_recall_returns_no_matches_message(git_cwd):
    result = await tools.run(call("recall", query="nothing saved yet"))
    assert "no matching memories" in result


@pytest.mark.asyncio
async def test_supersede_hides_old_from_recall(git_cwd):
    await tools.run(call("remember", title="deploy target", body="we deploy to heroku"))
    result = await tools.run(call("supersede", old="deploy target", new_body="we deploy to fly.io"))
    assert "superseded" in result
    recalled = await tools.run(call("recall", query="deploy"))
    assert "fly.io" in recalled
    assert "heroku" not in recalled


@pytest.mark.asyncio
async def test_supersede_unknown_node_returns_error(git_cwd):
    result = await tools.run(call("supersede", old="nothing here", new_body="x"))
    assert "error" in result.lower()


@pytest.mark.asyncio
async def test_link_adds_edge_between_two_nodes(git_cwd):
    await tools.run(call("remember", title="A node", body="a"))
    await tools.run(call("remember", title="B node", body="b"))
    result = await tools.run(call("link", a="A node", b="B node", relation="depends_on"))
    assert "linked" in result

    nodes = {n["title"]: n for n in store.all_nodes("project")}
    neighbors = store.neighbors("project", nodes["B node"]["id"], depth=1)
    assert any(n["id"] == nodes["A node"]["id"] and n["relation"] == "depends_on"
              for n in neighbors)


@pytest.mark.asyncio
async def test_link_rejects_invalid_relation(git_cwd):
    await tools.run(call("remember", title="A node", body="a"))
    await tools.run(call("remember", title="B node", body="b"))
    result = await tools.run(call("link", a="A node", b="B node", relation="bogus"))
    assert "error" in result.lower()


def test_permissions_allow_remember_supersede_link():
    from omega import permissions
    for name in ("remember", "supersede", "link"):
        assert permissions.decide(name, {})[0] == permissions.ALLOW
