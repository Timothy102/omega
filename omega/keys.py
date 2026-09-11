"""`omega keys` -- inspect and move provider API keys.

The interesting command is `migrate`: it lifts every plaintext `apiKey` out
of config.json into the OS keychain and rewrites the file without them, so a
config that has accumulated keys over months can stop being a liability in
one step instead of by hand-editing JSON around live credentials.
"""
from __future__ import annotations

import getpass
import json
import shutil
import sys
from pathlib import Path
from typing import Any

from . import config, secrets

USAGE = """usage:
  omega keys                     where each provider's key comes from
  omega keys set <provider>      store a key in the OS keychain (prompted, not echoed)
  omega keys rm <provider>       delete a key from the OS keychain
  omega keys migrate [--dry-run] move plaintext keys out of config.json into the keychain
"""


def _providers() -> dict[str, dict[str, Any]]:
    raw = config._json_or_default()
    providers = raw.get("providers")
    return dict(providers) if isinstance(providers, dict) else {}


def _write_providers(providers: dict[str, dict[str, Any]]) -> None:
    """Same merge-and-rewrite pattern as `mcp._write_mcp`: touch the
    "providers" key and leave every other setting exactly as it was."""
    raw = config._json_or_default()
    raw["providers"] = providers
    config.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.CONFIG_PATH.write_text(json.dumps(raw, indent=2) + "\n")
    config.CONFIG_PATH.chmod(0o600)


def _list() -> int:
    providers = _providers()
    if not providers:
        print(f"no providers configured in {config.CONFIG_PATH}")
        return 0
    width = max(len(n) for n in providers)
    exposed = []
    for name, p in sorted(providers.items()):
        source = secrets.describe_source(p.get("apiKeyEnv", ""), p.get("apiKeyCmd", ""),
                                         p.get("apiKey", ""), name)
        print(f"  {name:<{width}}  {source}")
        if p.get("apiKey"):
            exposed.append(name)
    if exposed:
        print(f"\n{len(exposed)} key(s) stored in plaintext in {config.CONFIG_PATH}.")
        print("  run `omega keys migrate` to move them into the keychain")
    return 0


def _set(name: str) -> int:
    if not secrets.keychain_available():
        print("omega: no OS keychain on this platform -- use apiKeyEnv or "
              "apiKeyCmd instead", file=sys.stderr)
        return 1
    secret = getpass.getpass(f"API key for {name!r} (not echoed): ").strip()
    if not secret:
        print("omega: nothing entered", file=sys.stderr)
        return 1
    if not secrets.looks_like_key(secret):
        # A warning, not a refusal: self-hosted and enterprise gateways issue
        # keys in shapes this has never seen.
        print("note: that doesn't look like a usual provider key -- storing it anyway")
    secrets.keychain_set(name, secret)
    print(f"stored {name!r} in the {secrets.SERVICE!r} keychain service")

    providers = _providers()
    if providers.get(name, {}).get("apiKey"):
        print(f"warning: {config.CONFIG_PATH} still holds a plaintext apiKey for "
              f"{name!r}, which takes precedence.\n"
              f"  run `omega keys migrate` to remove it")
    return 0


def _rm(name: str) -> int:
    if secrets.keychain_delete(name):
        print(f"deleted {name!r} from the keychain")
        return 0
    print(f"omega: no keychain entry for {name!r}", file=sys.stderr)
    return 1


def _backup_path() -> Path:
    return config.CONFIG_PATH.with_suffix(config.CONFIG_PATH.suffix + ".pre-migrate")


def _migrate(dry_run: bool) -> int:
    if not secrets.keychain_available():
        print("omega: no OS keychain on this platform -- move keys to apiKeyEnv "
              "or apiKeyCmd by hand", file=sys.stderr)
        return 1
    providers = _providers()
    plaintext = {n: p["apiKey"] for n, p in providers.items() if p.get("apiKey")}
    if not plaintext:
        print(f"no plaintext keys in {config.CONFIG_PATH} -- nothing to do")
        return 0

    print(f"{len(plaintext)} plaintext key(s) in {config.CONFIG_PATH}:")
    for name in sorted(plaintext):
        print(f"  {name}  ->  keychain ({secrets.SERVICE}/{name})")
    if dry_run:
        print("\n--dry-run: nothing written")
        return 0

    for name, secret in plaintext.items():
        secrets.keychain_set(name, secret)
    # Verify every key reads back BEFORE the file loses them -- a keychain
    # write that silently failed would otherwise take the only copy with it.
    unreadable = [n for n, s in plaintext.items() if secrets.keychain_get(n) != s]
    if unreadable:
        print(f"omega: keychain did not return {unreadable} as written; "
              f"config.json left untouched", file=sys.stderr)
        return 1

    backup = _backup_path()
    shutil.copy2(config.CONFIG_PATH, backup)
    backup.chmod(0o600)
    for name in plaintext:
        providers[name].pop("apiKey", None)
    _write_providers(providers)

    print(f"\nmoved {len(plaintext)} key(s) into the keychain and removed them "
          f"from {config.CONFIG_PATH}")
    print(f"a copy WITH the keys is at {backup} -- delete it once you have "
          f"confirmed omega still runs:")
    print(f"  rm {backup}")
    _warn_about_stale_copies()
    return 0


def _warn_about_stale_copies() -> None:
    """Point at the other files that may still hold the old keys. Migrating
    the live config is only half the job if a `.bak` beside it still has
    them, which is exactly the file nobody thinks to check."""
    stale = [p for p in config.CONFIG_PATH.parent.glob(f"{config.CONFIG_PATH.name}.*")
             if p != _backup_path() and "apiKey" in _safe_read(p)]
    if stale:
        print("\nthese files still contain plaintext keys:")
        for p in stale:
            print(f"  {p}")


def _safe_read(path: Path) -> str:
    try:
        return path.read_text()
    except OSError:
        return ""


def main(argv: list[str]) -> int:
    if not argv:
        return _list()
    cmd, rest = argv[0], argv[1:]
    if cmd == "migrate":
        return _migrate(dry_run="--dry-run" in rest)
    if cmd in ("set", "rm"):
        if not rest:
            print(f"omega: `keys {cmd}` needs a provider name\n\n{USAGE}", file=sys.stderr)
            return 1
        return _set(rest[0]) if cmd == "set" else _rm(rest[0])
    print(USAGE, file=sys.stderr)
    return 1
