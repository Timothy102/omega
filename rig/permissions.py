import json
import os
import re
import shlex
from pathlib import Path
from typing import Any

STORE = Path.home() / ".rig" / "permissions.json"

ALLOW, ASK, DENY = "allow", "ask", "deny"

# Never askable: no confirmation prompt makes these safe, and a model that has
# ingested untrusted content is exactly what would propose them.
FORBIDDEN_PATHS = ("/.ssh", "/.aws", "/.gnupg", "/.rig/config.json",
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

# Read-only first tokens. Anything not here is ASK, not DENY.
SAFE_COMMANDS = {
    "ls", "cat", "head", "tail", "wc", "file", "stat", "du", "df", "pwd", "which",
    "echo", "date", "env", "uname", "whoami", "rg", "grep", "find", "tree",
    "python3", "python", "node", "jq", "sort", "uniq", "cut", "awk", "sed",
    "pytest", "npm", "npx", "uv", "pip", "make", "cargo", "go",
}
SAFE_GIT = {"status", "diff", "log", "show", "branch", "remote", "rev-parse", "stash"}
SHELL_META = re.compile(r"[>|]|\$\(|`|&&|;|\|\|")


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
        return f"call_tool:{args.get('name', '')}"
    return name


def _first_token(command: str) -> str:
    try:
        parts = shlex.split(command)
    except ValueError:
        return command.strip().split(" ")[0] if command else ""
    if not parts:
        return ""
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
    """Returns (verdict, reason). Pure -- no I/O beyond the rules file."""
    cwd = cwd or os.getcwd()
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
    if rule in stored["allow"] and not tainted:
        return ALLOW, "allowed by a saved rule"

    if name in ("read", "grep", "glob", "recall", "find_tools", "subagent",
                "fetch_result", "list_artifacts", "ask_user"):
        return ALLOW, "read-only"

    if name in ("save_artifact", "update_artifact", "remember", "supersede", "link"):
        return ALLOW, "writes to rig's own store (~/.rig or .rig/), not project files"

    if name in ("write", "edit"):
        target = Path(os.path.expanduser(args.get("path", ""))).resolve()
        try:
            target.relative_to(Path(cwd).resolve())
        except ValueError:
            return ASK, f"writes outside the working directory ({target})"
        return ALLOW, "writes inside the working directory"

    if name == "bash":
        command = args.get("command", "")
        first = _first_token(command)
        if tainted:
            return ASK, "this turn has read untrusted content"
        if SHELL_META.search(command):
            return ASK, "uses redirection, pipes or command substitution"
        if first.startswith("git "):
            return (ALLOW, "read-only git") if first.split()[1] in SAFE_GIT \
                else (ASK, f"git subcommand {first.split()[1]!r}")
        return (ALLOW, f"{first} is read-only") if first in SAFE_COMMANDS \
            else (ASK, f"{first!r} is not on the safe list")

    return ASK, "not classified"
