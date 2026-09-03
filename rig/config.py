import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

CONFIG_PATH = Path(os.environ.get("RIG_CONFIG", Path.home() / ".rig" / "config.json"))

DEFAULTS: dict[str, Any] = {
    "providers": {
        "inference-net": {
            "baseUrl": "https://api.inference.net/v1",
            "apiKeyEnv": "INFERENCE_API_KEY",
        },
        "openrouter": {
            "baseUrl": "https://openrouter.ai/api/v1",
            "apiKeyEnv": "OPENROUTER_API_KEY",
        },
        "anthropic": {
            "type": "anthropic",
            "apiKeyEnv": "ANTHROPIC_API_KEY",
        },
    },
    "models": {
        "fable":  {"model": "claude-fable-5-1", "provider": "anthropic", "context": 1048576, "effort": "xhigh"},
        "opus":   {"model": "claude-opus-5", "provider": "anthropic", "context": 1048576, "effort": "high"},
        "sonnet": {"model": "claude-sonnet-5", "provider": "anthropic", "context": 1048576, "effort": "high"},
        "haiku":  {"model": "claude-haiku-4-5", "provider": "anthropic", "context": 200000},
        "kimi":   {"model": "moonshotai/kimi-k3", "provider": "openrouter", "context": 1048576},
        "glm":    {"model": "z-ai/glm-5.3-flash", "provider": "openrouter", "context": 128000},
    },
    "roles": {
        "main":          {"alias": "opus"},
        "plan":          {"alias": "opus"},
        "subagent_fast": {"alias": "glm"},
        "subagent_mid":  {"alias": "kimi"},
        "compact":       {"alias": "glm"},
        "memory":        {"alias": "glm"},
    },
}


@dataclass
class Provider:
    name: str
    type: Literal["openai", "anthropic"] = "openai"
    base_url: str = ""
    api_key_env: str = ""
    api_key_literal: str = ""

    @property
    def has_key(self) -> bool:
        """Non-raising check -- lets callers (onboarding, first-run detection)
        probe key availability without triggering the SystemExit below."""
        return bool(self.api_key_literal
                   or (self.api_key_env and os.environ.get(self.api_key_env)))

    @property
    def api_key(self) -> str:
        # Resolved lazily -- at load() time we don't yet know which providers a
        # session will actually use, and a provider with no key configured must
        # not block startup for users who haven't set it up yet.
        if self.api_key_literal:
            return self.api_key_literal
        key = os.environ.get(self.api_key_env, "") if self.api_key_env else ""
        if not key:
            hint = (f"export {self.api_key_env}=..." if self.api_key_env
                    else f'set "apiKey" in {CONFIG_PATH}')
            raise SystemExit(
                f"rig: no API key for provider {self.name!r}.\n"
                f"  Run `rig setup` to configure one, or {hint}")
        return key


@dataclass
class Model:
    alias: str
    model: str
    provider: str
    context: int = 128000
    effort: str | None = None


@dataclass
class Role:
    model: str
    provider: Provider
    context: int = 128000
    effort: str | None = None
    alias: str | None = None


@dataclass
class Config:
    roles: dict[str, Role] = field(default_factory=dict)
    models: dict[str, Model] = field(default_factory=dict)
    providers: dict[str, Provider] = field(default_factory=dict)

    def role(self, name: str) -> Role:
        if name not in self.roles:
            raise KeyError(f"no role {name!r}; have {sorted(self.roles)}")
        return self.roles[name]

    def model(self, alias: str) -> Role:
        if alias not in self.models:
            raise KeyError(f"no model {alias!r}; have {sorted(self.models)}")
        m = self.models[alias]
        if m.provider not in self.providers:
            raise KeyError(f"model {alias!r} references unknown provider {m.provider!r}")
        return Role(m.model, self.providers[m.provider], m.context, m.effort, alias)

    def resolve_alias(self, text: str) -> str:
        """Resolve a `--model`/`/model` argument to a catalog alias: an exact
        alias match first, else a bare model id matched against catalog entries."""
        if text in self.models:
            return text
        for alias, m in self.models.items():
            if m.model == text:
                return alias
        raise SystemExit(f"rig: unknown model {text!r}; have {sorted(self.models)}")


def _strip_jsonc(text: str) -> str:
    text = re.sub(r"^\s*//.*$", "", text, flags=re.M)
    return re.sub(r",(\s*[}\]])", r"\1", text)


def _json_or_default() -> dict[str, Any]:
    """The raw config dict as written on disk, or an empty skeleton -- used by
    onboarding to merge its additions into whatever is already there instead
    of clobbering unrelated providers/roles/mcp entries."""
    if CONFIG_PATH.exists():
        return dict(json.loads(_strip_jsonc(CONFIG_PATH.read_text())))
    return {"providers": {}, "models": {}, "roles": {}}


def load() -> Config:
    raw: dict[str, Any] = DEFAULTS
    if CONFIG_PATH.exists():
        raw = json.loads(_strip_jsonc(CONFIG_PATH.read_text()))

    providers: dict[str, Provider] = {}
    for name, p in raw["providers"].items():
        ptype = p.get("type", "openai")
        # Only strip a trailing slash -- an anthropic provider's baseUrl (when
        # given at all) is passed straight to AsyncAnthropic as base_url=, and
        # the SDK's own default already omits a "/v1" suffix.
        base_url = (p.get("baseUrl") or "").rstrip("/")
        if ptype == "openai" and not base_url:
            raise SystemExit(f"rig: provider {name!r} is missing \"baseUrl\" in {CONFIG_PATH}")
        providers[name] = Provider(
            name=name, type=ptype, base_url=base_url,
            api_key_env=p.get("apiKeyEnv", ""),
            api_key_literal=p.get("apiKey", ""),
        )

    models: dict[str, Model] = {}
    for alias, m in (raw.get("models") or {}).items():
        models[alias] = Model(alias, m["model"], m["provider"], m.get("context", 128000), m.get("effort"))
    # A hand-written config predating the catalog would otherwise leave the
    # /model picker empty; built-ins fill in wherever their provider exists.
    for alias, m in DEFAULTS["models"].items():
        if alias not in models and m["provider"] in providers:
            models[alias] = Model(alias, m["model"], m["provider"], m.get("context", 128000), m.get("effort"))

    cfg = Config(models=models, providers=providers)

    roles: dict[str, Role] = {}
    for name, r in raw["roles"].items():
        if "alias" in r:
            roles[name] = cfg.model(r["alias"])
        else:
            roles[name] = Role(r["model"], providers[r["provider"]], r.get("context", 128000), r.get("effort"))
    cfg.roles = roles
    return cfg


def mcp_names() -> list[str]:
    if not CONFIG_PATH.exists():
        return []
    import json as _json
    return list(_json.loads(_strip_jsonc(CONFIG_PATH.read_text())).get("mcp", {}))
