"""First-run setup: get `~/.rig/config.json` into a working state without
the full browser flow (`rig setup`). `run()` picks the Textual wizard on a
real terminal and falls back to `run_plain()` (the original `input()` flow)
otherwise; both share the provider presets, catalog, and config-merge logic
in this module. No Textual imports here -- `rig/ui/tui/onboarding.py` is the
only place that knows about widgets."""
import asyncio
import getpass
import sys
from dataclasses import dataclass
from typing import Any

import httpx

from . import config, loop
from .setup_server import PROBE_PROMPT as PROBE_PROMPT
from .setup_server import _save

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
        "spark": {"model": "meta/muse-spark-1.3", "provider": "openrouter", "context": 1048576},
        "kimi": {"model": "moonshotai/kimi-k3", "provider": "openrouter", "context": 1048576},
        "glm": {"model": "z-ai/glm-5.3-flash", "provider": "openrouter", "context": 128000},
    },
    default_alias="spark", cheap_alias="glm",
)

# USD per million tokens, (input, output) -- shown in the model picker.
PRICES: dict[str, tuple[float, float]] = {
    "fable": (10, 50), "opus": (5, 25), "sonnet": (2, 10), "haiku": (1, 5),
    "spark": (1.25, 4.25), "kimi": (3, 15), "glm": (0.6, 2.2),
}

PURPOSES: dict[str, str] = {
    "fable": "hardest problems, deepest reasoning",
    "opus": "best for coding (recommended)",
    "sonnet": "fast and strong",
    "haiku": "cheapest, quick tasks",
    "spark": "Meta's frontier — strong at agentic coding, cheap",
    "kimi": "strong open-weights, 1M context",
    "glm": "cheap and fast",
}

# provider_key -> (label, one-liner, env var checked for "key found in environment")
PROVIDER_INFO: list[tuple[str, str, str, str]] = [
    ("anthropic", "Anthropic  — Claude (recommended)",
     "Native Anthropic API access -- fable, opus, sonnet, haiku.", "ANTHROPIC_API_KEY"),
    ("openrouter", "OpenRouter  — Claude, Kimi, GLM, GPT, Gemini and 200+ more",
     "One key, hundreds of models, pay-as-you-go.", "OPENROUTER_API_KEY"),
    ("other", "Other OpenAI-compatible  — any /chat/completions endpoint",
     "Bring your own base URL: local models, other clouds, self-hosted.", ""),
]


def env_var_for(provider_key: str) -> str:
    for key, _label, _desc, env in PROVIDER_INFO:
        if key == provider_key or (key == "other" and provider_key == "custom"):
            return env
    return ""


def choice_for(provider_key: str, base_url: str = "") -> _Choice:
    if provider_key == "anthropic":
        return _ANTHROPIC
    if provider_key == "openrouter":
        return _OPENROUTER
    return _Choice(provider_key="custom", provider={"type": "openai", "baseUrl": base_url, "apiKey": ""},
                  catalog={}, default_alias="", cheap_alias="")


def build_config(choice: _Choice, main_alias: str, cheap_alias: str, models: dict[str, Body]) -> Body:
    """Merge this choice into whatever config already exists on disk --
    onboarding's own six roles win, everything else in the file is untouched."""
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
    return raw


def save_config(raw: Body) -> None:
    _save(raw)


async def validate_key(choice: _Choice, key: str) -> tuple[bool, str]:
    """Probe that `key` actually works for `choice`'s provider. Returns
    (ok, message) -- message is "connected" on success or a short error."""
    if choice.provider.get("type") == "anthropic":
        from anthropic import AsyncAnthropic
        try:
            client = AsyncAnthropic(api_key=key)
            await client.models.list(limit=1)
            return True, "connected"
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"[:120]

    base = (choice.provider.get("baseUrl") or "").rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=15) as http_client:
            r = await http_client.get(f"{base}/models", headers={"Authorization": f"Bearer {key}"})
        if r.status_code != 200:
            return False, f"HTTP {r.status_code}: {r.text[:100]}"
        return True, "connected"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"[:120]


async def run() -> bool:
    """Entry point for both `rig onboard` and the first-run gate: the
    Textual wizard on a real terminal, the original `input()` flow otherwise
    (piped/scripted invocations, or a dumb terminal)."""
    if sys.stdin.isatty() and sys.stdout.isatty():
        from .ui.tui.onboarding import OnboardingApp
        return bool(await OnboardingApp().run_async())
    await run_plain()
    return config.CONFIG_PATH.exists()


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


async def run_plain() -> None:
    print("rig needs a model to drive it -- let's set one up (`rig setup` opens the full browser flow).")
    pick = await _ask_choice("Pick a provider:",
                             ["Anthropic (native)", "OpenRouter", "Other OpenAI-compatible"])

    if pick == 1:
        choice = _ANTHROPIC
    elif pick == 2:
        choice = _OPENROUTER
    else:
        base_url = await _ask("Base URL (e.g. https://api.example.com/v1): ")
        choice = choice_for("other", base_url)

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

    save_config(build_config(choice, main_alias, cheap_alias, models))
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
