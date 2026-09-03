"""Shell hooks around tool dispatch -- see config.HookRule and tools.run().

A pre-hook can block a tool call (non-zero exit); its stdout becomes the
denial reason. A post-hook's stdout is appended to the tool result. Hook
failures (bad command, timeout, non-zero post-hook exit) never crash the
turn -- a broken hook just contributes nothing.
"""
import json
import os
import subprocess

from .config import HookRule

TIMEOUT = 60
MAX_POST_APPEND = 2000
MAX_RESULT_ENV = 8000


def _applicable(rules: list[HookRule], tool_name: str) -> list[HookRule]:
    return [r for r in rules if tool_name in r.tools]


def _hook_name(command: str) -> str:
    parts = command.split(None, 1)
    return parts[0] if parts else command


def run_pre(rules: list[HookRule], tool_name: str, args: dict[str, object],
           cwd: str) -> tuple[bool, str]:
    """Returns (blocked, message). The first hook that exits non-zero blocks
    the call; its stdout (or stderr as a fallback) is the message."""
    for rule in _applicable(rules, tool_name):
        env = {**os.environ, "OMEGA_TOOL": tool_name,
               "OMEGA_ARGS_JSON": json.dumps(args, default=str), "OMEGA_CWD": cwd}
        try:
            proc = subprocess.run(rule.command, shell=True, cwd=cwd, env=env,
                                  capture_output=True, text=True, timeout=TIMEOUT)
        except (OSError, subprocess.SubprocessError):
            continue
        if proc.returncode != 0:
            return True, (proc.stdout or proc.stderr or f"hook {_hook_name(rule.command)!r} "
                          f"exited {proc.returncode}").strip()
    return False, ""


def run_post(rules: list[HookRule], tool_name: str, args: dict[str, object], cwd: str,
            result: str) -> str:
    """Returns text to append to the tool result (empty when no post-hook
    produced output)."""
    appended: list[str] = []
    for rule in _applicable(rules, tool_name):
        env = {**os.environ, "OMEGA_TOOL": tool_name,
               "OMEGA_ARGS_JSON": json.dumps(args, default=str), "OMEGA_CWD": cwd,
               "OMEGA_RESULT": result[:MAX_RESULT_ENV]}
        try:
            proc = subprocess.run(rule.command, shell=True, cwd=cwd, env=env,
                                  capture_output=True, text=True, timeout=TIMEOUT)
        except (OSError, subprocess.SubprocessError):
            continue
        out = (proc.stdout or "").strip()
        if out:
            appended.append(f"[hook {_hook_name(rule.command)}] {out}")
    return "\n".join(appended)[:MAX_POST_APPEND]
