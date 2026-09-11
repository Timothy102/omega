"""Where provider API keys actually live.

A key written into `config.json` is a key that leaks: into a screenshot, into
`cat` output pasted in a bug report, into every backup that copies the file,
into the `.bak` written beside it. The file being mode 0600 stops another
*user* reading it and nothing else -- most of the ways a key escapes a laptop
run as you.

So the config holds a *reference* to a secret rather than the secret. Three
kinds, in the order `config.Provider` tries them:

  apiKeyEnv  "OPENROUTER_API_KEY"          -- an environment variable
  apiKeyCmd  "op read op://dev/or/key"     -- anything that prints it to stdout
  (keychain) looked up automatically       -- the OS store, service "omega"

The keychain is the default target rather than an encrypted file of our own
making. It is already encrypted at rest, already unlocked by your login, and
already access-controlled per application; a passphrase-encrypted file would
either prompt on every single invocation or cache its derived key on disk,
which just moves the plaintext somewhere less examined. `apiKeyCmd` is the
escape hatch for anyone who keeps secrets in 1Password, pass, or a cloud
secret manager and wants omega to never store one at all.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys

SERVICE = "omega"

# Resolving a secret costs a keychain round-trip or a whole subprocess, and
# `Provider.api_key` is read on every request. Cached for the life of the
# process; a rotated key takes effect on the next run, same as a config edit.
_cache: dict[str, str] = {}


def clear_cache() -> None:
    _cache.clear()


# ---- the OS keychain --------------------------------------------------------

def keychain_available() -> bool:
    """macOS only for now. Linux's Secret Service and Windows' Credential
    Locker have no equivalent single binary to shell out to, so those
    platforms get `apiKeyEnv`/`apiKeyCmd`, which reach every secret manager
    worth using anyway."""
    return sys.platform == "darwin" and shutil.which("security") is not None


def keychain_get(account: str) -> str | None:
    if not keychain_available():
        return None
    key = f"keychain:{account}"
    if key in _cache:
        return _cache[key]
    proc = subprocess.run(
        ["security", "find-generic-password", "-s", SERVICE, "-a", account, "-w"],
        capture_output=True, text=True)
    if proc.returncode != 0:
        return None
    secret = proc.stdout.strip()
    if secret:
        _cache[key] = secret
    return secret or None


def keychain_set(account: str, secret: str) -> None:
    """Store (or replace) a key.

    The secret goes through `security`'s argv, which is visible to `ps` for
    the instant the process lives -- the CLI's own `-w` prompt cannot be
    piped (it demands an interactive retype), and the alternative is a ctypes
    binding to the Security framework whose memory handling is easy to get
    subtly wrong. This runs at most once per key, on `omega keys set`, versus
    reads that happen constantly and expose nothing. Anyone who considers
    even that moment too much should use `apiKeyCmd` and keep the secret in a
    manager omega never writes to."""
    if not keychain_available():
        raise RuntimeError("no OS keychain available on this platform")
    proc = subprocess.run(
        ["security", "add-generic-password", "-U", "-s", SERVICE, "-a", account,
         "-w", secret, "-D", "omega provider key",
         "-j", f"API key for omega provider {account!r}"],
        capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "keychain write failed")
    _cache.pop(f"keychain:{account}", None)


def keychain_delete(account: str) -> bool:
    if not keychain_available():
        return False
    proc = subprocess.run(
        ["security", "delete-generic-password", "-s", SERVICE, "-a", account],
        capture_output=True, text=True)
    _cache.pop(f"keychain:{account}", None)
    return proc.returncode == 0


def keychain_accounts(names: list[str]) -> list[str]:
    """Which of `names` the keychain holds. Probed one at a time rather than
    by dumping the service: `security dump-keychain` prompts for permission
    per item and is the kind of thing that trains people to click Allow."""
    return [n for n in names if keychain_get(n) is not None]


# ---- an external command ----------------------------------------------------

def from_command(cmd: str, *, timeout: int = 20) -> str:
    """Run `cmd` and take its stdout as the secret.

    Deliberately a shell string, not an argv list: the point is to paste in
    whatever incantation a secret manager already documents (`op read ...`,
    `pass show ...`, `gcloud secrets versions access ...`) without translating
    it. It only ever runs commands the config's owner wrote there themselves.
    """
    key = f"cmd:{cmd}"
    if key in _cache:
        return _cache[key]
    try:
        proc = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"apiKeyCmd timed out after {timeout}s: {cmd}") from None
    if proc.returncode != 0:
        detail = proc.stderr.strip().splitlines()
        raise RuntimeError(f"apiKeyCmd failed (exit {proc.returncode}): "
                           f"{detail[0] if detail else cmd}")
    secret = proc.stdout.strip()
    if not secret:
        raise RuntimeError(f"apiKeyCmd printed nothing: {cmd}")
    _cache[key] = secret
    return secret


# ---- redaction --------------------------------------------------------------

# Provider key shapes, longest-prefix first so `sk-ant-` is not eaten by the
# shorter `sk-` alternative before it can match.
_KEY_RE = re.compile(r"\b(sk-ant-[A-Za-z0-9_\-]{8,}|sk-or-[A-Za-z0-9_\-]{8,}"
                     r"|sk-proj-[A-Za-z0-9_\-]{8,}|sk-[A-Za-z0-9_\-]{16,}"
                     r"|ghp_[A-Za-z0-9]{16,}|xox[baprs]-[A-Za-z0-9\-]{10,})")


def redact(text: str) -> str:
    """Mask anything key-shaped, keeping enough of the head to tell two keys
    apart. For error messages and any diagnostic a user might paste back."""
    def mask(m: re.Match[str]) -> str:
        raw = m.group(0)
        return f"{raw[:11]}…{raw[-4:]} [redacted]" if len(raw) > 20 else "[redacted]"
    return _KEY_RE.sub(mask, text)


def looks_like_key(value: str) -> bool:
    return bool(_KEY_RE.fullmatch(value.strip()))


def describe_source(env: str, cmd: str, literal: str, account: str) -> str:
    """Where a provider's key is coming from, for `omega keys` and `doctor`.
    Never the value."""
    if literal:
        return "config.json (plaintext)"
    if cmd:
        return f"command: {cmd}"
    if env and os.environ.get(env):
        return f"env: {env}"
    if keychain_get(account) is not None:
        return "keychain"
    return "missing"
