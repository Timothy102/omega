"""First-run terminal onboarding: a fast path to a working `~/.rig/config.json`
for someone with no config yet or no usable key for the `main` role, without
requiring the full browser flow (`rig setup`). Re-runnable via `rig onboard`."""
import asyncio
import getpass
from dataclasses import dataclass
from typing import Any

from . import config, loop
from .setup_server import PROBE_PROMPT, _save

Body = dict[str, Any]


@dataclass
class _Choice:
    provider_key: str
    provider: Body
    catalog: dict[str, Body]  # alias -> model entry, this provider only
    default_alias: str
    cheap_alias: str


_ANTHROPIC = _Choice(
    provider_key="anthropic",
    provider={"type": "anthropic", "apiKey": ""},
    catalog={
        "fable": {"model": "claude-fable-5-1", "provider": "anthropic", "context": 1048576, "effort": "xhigh"},
        "opus": {"model": "claude-opus-5", "provider": "anthropic", "context": 1048576, "effort": "high"},
        "sonnet": {"model": "claude-sonnet-5", "provider": "anthropic", "context": 1048576, "effort": "high"},
        "haiku": {"model": "claude-haiku-4-5", "provider": "anthropic", "context": 200000},
    },
    default_alias="opus", cheap_alias="haiku",
)
_OPENROUTER = _Choice(
    provider_key="openrouter",
    provider={"type": "openai", "baseUrl": "https://openrouter.ai/api/v1", "apiKey": ""},
    catalog={
        "kimi": {"model": "moonshotai/kimi-k3", "provider": "openrouter", "context": 1048576},
        "glm": {"model": "z-ai/glm-5.3-flash", "provider": "openrouter", "context": 128000},
    },
    default_alias="kimi", cheap_alias="glm",
)


async def _ask(prompt: str) -> str:
    return (await asyncio.to_thread(input, prompt)).strip()


async def _ask_choice(prompt: str, options: list[str], default: int = 1) -> int:
    print(prompt)
    for i, opt in enumerate(options, 1):
        print(f"  {i}. {opt}")
    raw = await _ask(f"choice [{default}]: ")
    if not raw:
        return default
    if raw.isdigit() and 1 <= int(raw) <= len(options):
        return int(raw)
    return default


async def run() -> None:
    print("rig needs a model to drive it -- let's set one up (`rig setup` opens the full browser flow).")
    pick = await _ask_choice("Pick a provider:",
                             ["Anthropic (native)", "OpenRouter", "Other OpenAI-compatible"])

    if pick == 1:
        choice = _ANTHROPIC
    elif pick == 2:
        choice = _OPENROUTER
    else:
        base_url = await _ask("Base URL (e.g. https://api.example.com/v1): ")
        choice = _Choice(
            provider_key="custom", provider={"type": "openai", "baseUrl": base_url, "apiKey": ""},
            catalog={}, default_alias="", cheap_alias="")

    key = await asyncio.to_thread(getpass.getpass, "API key (hidden): ")
    choice.provider["apiKey"] = key

    if choice.catalog:
        aliases = sorted(choice.catalog)
        default_idx = aliases.index(choice.default_alias) + 1
        labels = [f"{a}  {choice.catalog[a]['model']}" for a in aliases]
        picked = await _ask_choice("Default model for `main`:", labels, default=default_idx)
        main_alias = aliases[picked - 1]
        cheap_alias = choice.cheap_alias
        models = dict(choice.catalog)
    else:
        model_id = await _ask("Model id served by this endpoint: ")
        main_alias = cheap_alias = "main-model"
        models = {main_alias: {"model": model_id, "provider": choice.provider_key, "context": 128000}}

    raw = config._json_or_default()
    raw.setdefault("providers", {})[choice.provider_key] = choice.provider
    raw.setdefault("models", {}).update(models)
    roles = raw.setdefault("roles", {})
    roles["main"] = {"alias": main_alias}
    roles["plan"] = {"alias": main_alias}
    roles["subagent_mid"] = {"alias": main_alias}
    roles["subagent_fast"] = {"alias": cheap_alias}
    roles["compact"] = {"alias": cheap_alias}
    roles["memory"] = {"alias": cheap_alias}
    _save(raw)
    print(f"wrote {config.CONFIG_PATH}")

    print("\nrunning a test turn...")
    try:
        cfg = config.load()
        text = await loop.run_agent(cfg, "main", loop.BUILD_SYSTEM,
                                    [{"role": "user", "content": PROBE_PROMPT}])
        print(text)
    except Exception as e:
        print(f"test turn failed ({type(e).__name__}: {e}) -- config is saved; check the key and retry.")

    print("\nrig                    interactive TUI\n"
          "rig \"do the thing\"     one-shot\n"
          "rig setup              full browser setup, more providers/roles/MCP")
