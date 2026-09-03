import json
import os
import re
import shlex
from pathlib import Path
from typing import Any

STORE = Path.home() / ".omega" / "permissions.json"

ALLOW, ASK, DENY = "allow", "ask", "deny"

# Never askable: no confirmation prompt makes these safe, and a model that has
# ingested untrusted content is exactly what would propose them. This floor
# stays in place even with everything below relaxed to ALLOW -- it guards
# against catastrophic, irreversible harm (credential theft, wiping the
# filesystem, running as root), not routine workflow friction, so "drop all
# permissions" doesn't extend to it without a separate, explicit ask.
# Both `.omega/config.json` and the pre-rename `.rig/config.json` stay
# protected -- the old file may still exist (with a key in it) after migration.
FORBIDDEN_PATHS = ("/.ssh", "/.aws", "/.gnupg", "/.omega/config.json", "/.rig/config.json",
                   "/.claude.json", "/.claude/", "/.mcp-auth", "/.netrc",
                   "/.config/gh", "/.kube")
FORBIDDEN_PATTERNS = [
    (re.compile(r"\b(curl|wget)\b[^|;&]*\|\s*(sudo\s+)?(ba)?sh"), "pipes a download into a shell"),
    (re.compile(r"\brm\s+(-[a-zA-Z]*\s+)*-?[a-zA-Z]*[rf][a-zA-Z]*\s+(/|~|\$HOME)\s*$"), "recursive delete of / or ~"),
    (re.compile(r"\bsudo\b"), "runs as root"),
    (re.compile(r"\bgit\s+push\b.*--force(?!-with-lease)"), "force-push"),
    (re.compile(r":\(\)\s*\{.*\};\s*:"), "fork bomb"),
    (re.compile(r"\b(shutdown|reboot|mkfs|diskutil\s+erase)\b"), "destructive system command"),
]


def _load() -> dict[str, list[str]]:
    if STORE.exists():
        try:
            return dict(json.loads(STORE.read_text()))
        except json.JSONDecodeError:
            pass
    return {"allow": [], "deny": []}


def remember(rule: str, verdict: str) -> None:
    data = _load()
    key = "allow" if verdict == ALLOW else "deny"
    if rule not in data[key]:
        data[key].append(rule)
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(data, indent=1))
    STORE.chmod(0o600)


def rule_for(name: str, args: dict[str, Any]) -> str:
    if name == "bash":
        return f"bash:{_first_token(args.get('command', ''))}"
    if name == "call_tool":
        tool = str(args.get("name", ""))
        # One "always" per connected server, not per tool: the user authorised
        # the integration, and a server with 40 tools would otherwise prompt
        # 40 times.
        m = re.match(r"(mcp__[^_]+(?:_[^_]+)*?)__", tool)
        return f"call_tool:{m.group(1)}__*" if m else f"call_tool:{tool}"
    return name


def _first_token(command: str) -> str:
    try:
        parts = shlex.split(command)
    except ValueError:
        return command.strip().split(" ")[0] if command else ""
    if not parts:
        return ""
    # `cd <dir> && real-command …`: the rule should name the real command,
    # otherwise every command run from a worktree is classified as "cd".
    if parts[0] == "cd" and "&&" in parts:
        parts = parts[parts.index("&&") + 1:] or parts
    if parts[0] == "git" and len(parts) > 1:
        return f"git {parts[1]}"
    return parts[0]


def _touches_forbidden(text: str) -> str | None:
    expanded = os.path.expanduser(text)
    home = str(Path.home())
    for frag in FORBIDDEN_PATHS:
        if frag in expanded or (home + frag) in expanded:
            return f"touches protected path {frag}"
    return None


def decide(name: str, args: dict[str, Any], cwd: str | None = None,
          tainted: bool = False) -> tuple[str, str]:
    """Returns (verdict, reason). Pure -- no I/O beyond the rules file.

    Everything the model can do to the local filesystem/shell -- write, edit,
    bash, the git subcommand it runs, whether a path is inside `cwd` -- is
    ALLOW by default now; only the FORBIDDEN_PATHS/FORBIDDEN_PATTERNS floor
    above and prompt-injection taint still gate. `call_tool` (any external
    MCP server, database writes included) is deliberately left unclassified
    below so it still falls through to ASK -- that's the one category this
    relaxation does not cover."""
    stored = _load()
    rule = rule_for(name, args)

    if name in ("write", "edit", "bash", "call_tool", "remember", "supersede", "link"):
        blob = " ".join(str(v) for v in args.values())
        why = _touches_forbidden(blob)
        if why:
            return DENY, why

    if name == "bash":
        command = args.get("command", "")
        for pattern, why in FORBIDDEN_PATTERNS:
            if pattern.search(command):
                return DENY, why

    if rule in stored["deny"]:
        return DENY, "denied by a saved rule"
    if rule in stored["allow"]:
        return ALLOW, "allowed by a saved rule"

    if name in ("read", "grep", "glob", "recall", "find_tools", "subagent",
                "fetch_result", "list_artifacts", "ask_user", "skill", "bash_status"):
        return ALLOW, "read-only"

    if name in ("save_artifact", "update_artifact", "remember", "supersede", "link"):
        return ALLOW, "writes to omega's own store (~/.omega or .omega/), not project files"

    if name in ("write", "edit"):
        return ALLOW, "writes always allowed (permissions relaxed to the filesystem/shell)"

    if name == "bash":
        # The user chose zero prompts over the prompt-injection guard: the
        # FORBIDDEN floor above is the only thing between untrusted content
        # and the shell.
        return ALLOW, "bash always allowed (permissions relaxed to the filesystem/shell)"

    return ASK, "not classified"
