import json

from omega import integrations


def test_dict_key_matches_integration_key():
    for key, i in integrations.CATALOG.items():
        assert i.key == key


def test_catalog_has_broad_coverage():
    # A handful of well-known catalog entries, spread across categories.
    assert len(integrations.CATALOG) >= 35
    assert len({i.category for i in integrations.CATALOG.values()}) >= 5


def test_verified_remote_entries_have_https_url():
    for i in integrations.CATALOG.values():
        if i.transport == "remote" and i.verified:
            assert i.url is not None and i.url.startswith("https://"), i.key


def test_stdio_entries_have_nonempty_command():
    for i in integrations.CATALOG.values():
        if i.transport == "stdio":
            assert i.command and all(isinstance(c, str) and c for c in i.command), i.key


def test_remote_entries_have_no_command_and_stdio_entries_have_no_url():
    for i in integrations.CATALOG.values():
        if i.transport == "remote":
            assert i.command is None
        else:
            assert i.url is None


def test_unverified_entries_still_carry_a_docs_link():
    """An entry we couldn't confirm must still point somewhere the user can check."""
    for i in integrations.CATALOG.values():
        if not i.verified:
            assert i.docs, i.key


def test_imported_from_claude_code_never_leaks_env_values(monkeypatch):
    fake = {
        "slack": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-slack"],
                  "env": {"SLACK_BOT_TOKEN": "xoxb-super-secret-value"}},
    }
    monkeypatch.setattr(integrations.mcp, "discover", lambda include_omega=True: fake)
    out = integrations.imported_from_claude_code()
    dumped = json.dumps(out)
    assert "xoxb-super-secret-value" not in dumped
    assert out["slack"]["env"] == ["SLACK_BOT_TOKEN"]


def test_imported_from_claude_code_redacts_inline_header_tokens(monkeypatch):
    fake = {
        "linear": {"command": "npx", "args": [
            "-y", "mcp-remote@0.8.1", "https://mcp.linear.app/mcp",
            "--header", "Authorization:Bearer sekrit-token"]},
    }
    monkeypatch.setattr(integrations.mcp, "discover", lambda include_omega=True: fake)
    out = integrations.imported_from_claude_code()
    dumped = json.dumps(out)
    assert "sekrit-token" not in dumped
    assert "***" in out["linear"]["args"]


def test_imported_from_claude_code_uses_claude_only_view(monkeypatch):
    """include_omega=False must be what's passed -- otherwise omega's own servers
    would masquerade as "found in Claude Code"."""
    seen = {}
    def fake_discover(include_omega=True):
        seen["include_omega"] = include_omega
        return {}
    monkeypatch.setattr(integrations.mcp, "discover", fake_discover)
    integrations.imported_from_claude_code()
    assert seen["include_omega"] is False
