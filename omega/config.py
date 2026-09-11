import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from . import secrets

CONFIG_PATH = Path(os.environ.get("OMEGA_CONFIG", Path.home() / ".omega" / "config.json"))

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
        "openai": {
            "baseUrl": "https://api.openai.com/v1",
            "apiKeyEnv": "OPENAI_API_KEY",
        },
    },
    "models": {
        "fable":  {"model": "claude-fable-5-1", "provider": "anthropic", "context": 1048576,
                   "effort": "xhigh", "fallback": "opus"},
        "opus":   {"model": "claude-opus-5", "provider": "anthropic", "context": 1048576,
                   "effort": "high", "fallback": "sonnet"},
        "sonnet": {"model": "claude-sonnet-5", "provider": "anthropic", "context": 1048576,
                   "effort": "high", "fallback": "haiku"},
        "haiku":  {"model": "claude-haiku-4-5", "provider": "anthropic", "context": 200000},
        "spark":  {"model": "meta/muse-spark-1.3", "provider": "openrouter", "context": 1048576,
                   "fallback": "kimi"},
        "kimi":   {"model": "moonshotai/kimi-k3", "provider": "openrouter", "context": 1048576},
        "glm":    {"model": "z-ai/glm-5.3-flash", "provider": "openrouter", "context": 128000},
        # GPT-6 Astra is in limited rollout and not on OpenRouter yet, so it
        # needs the native OpenAI provider; the GPT-5.6 tiers are on both.
        "astra":  {"model": "gpt-6-astra", "provider": "openai", "context": 1050000,
                   "fallback": "sol"},
        "sol":    {"model": "openai/gpt-5.6-sol", "provider": "openrouter", "context": 1050000,
                   "fallback": "terra"},
        "terra":  {"model": "openai/gpt-5.6-terra", "provider": "openrouter", "context": 1050000,
                   "fallback": "luna"},
        "luna":   {"model": "openai/gpt-5.6-luna", "provider": "openrouter", "context": 1050000},
        "codex":  {"model": "openai/gpt-5.3-codex", "provider": "openrouter", "context": 400000,
                   "fallback": "sol"},
        "grok":   {"model": "x-ai/grok-4.6", "provider": "openrouter", "context": 500000,
                   "fallback": "grok-build"},
        "grok-build": {"model": "x-ai/grok-build-0.1", "provider": "openrouter", "context": 256000},
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
    api_key_cmd: str = ""

    def _resolve(self) -> str:
        """The key, or "" if nothing supplies one.

        Order is most-explicit-first: a literal in the config, then a command,
        then an environment variable, then the OS keychain. The keychain is
        last because it is the one source the config does not mention -- so a
        key written down in front of you always beats one found by
        convention, and `omega keys migrate` can move a literal into the
        keychain without the two fighting over which wins."""
        if self.api_key_literal:
            return self.api_key_literal
        if self.api_key_cmd:
            return secrets.from_command(self.api_key_cmd)
        if self.api_key_env:
            from_env = os.environ.get(self.api_key_env, "")
            if from_env:
                return from_env
        return secrets.keychain_get(self.name) or ""

    @property
    def has_key(self) -> bool:
        """Non-raising check -- lets callers (onboarding, first-run detection)
        probe key availability without triggering the SystemExit below."""
        try:
            return bool(self._resolve())
        except RuntimeError:
            # A broken `apiKeyCmd` is a real misconfiguration, but this
            # property exists precisely so probing cannot blow up its caller.
            return False

    @property
    def key_source(self) -> str:
        """Where the key comes from, for `omega keys` and `omega doctor`.
        Never the value itself."""
        return secrets.describe_source(self.api_key_env, self.api_key_cmd,
                                       self.api_key_literal, self.name)

    @property
    def api_key(self) -> str:
        # Resolved lazily -- at load() time we don't yet know which providers a
        # session will actually use, and a provider with no key configured must
        # not block startup for users who haven't set it up yet.
        try:
            key = self._resolve()
        except RuntimeError as e:
            raise SystemExit(f"omega: could not read the API key for provider "
                             f"{self.name!r}: {secrets.redact(str(e))}") from None
        if not key:
            hint = (f"export {self.api_key_env}=..." if self.api_key_env
                    else f"omega keys set {self.name}")
            raise SystemExit(
                f"omega: no API key for provider {self.name!r}.\n"
                f"  Run `omega setup` to configure one, or {hint}")
        return key


@dataclass
class Model:
    alias: str
    model: str
    provider: str
    context: int = 128000
    effort: str | None = None
    fallback: str | None = None


@dataclass
class Role:
    model: str
    provider: Provider
    context: int = 128000
    effort: str | None = None
    alias: str | None = None
    fallback_alias: str | None = None


@dataclass(frozen=True)
class HookRule:
    """One `hooks.json` entry: `command` runs (with OMEGA_* env vars) whenever a
    call to one of `tools` is dispatched -- see hooks.py."""
    tools: list[str]
    command: str


@dataclass
class Config:
    roles: dict[str, Role] = field(default_factory=dict)
    models: dict[str, Model] = field(default_factory=dict)
    providers: dict[str, Provider] = field(default_factory=dict)
    # B1 edit-safety config: read from top-level "verify"/"hooks"/"review_auto"
    # keys in config.json -- see verify.py, hooks.py and subagent.review().
    verify_auto: bool = True
    verify_checks: list[str] | None = None
    review_auto: bool = True
    hooks: dict[str, list[HookRule]] = field(default_factory=dict)

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
        return Role(m.model, self.providers[m.provider], m.context, m.effort, alias, m.fallback)

    def resolve_alias(self, text: str) -> str:
        """Resolve a `--model`/`/model` argument to a catalog alias: an exact
        alias match first, else a bare model id matched against catalog entries."""
        if text in self.models:
            return text
        for alias, m in self.models.items():
            if m.model == text:
                return alias
        raise SystemExit(f"omega: unknown model {text!r}; have {sorted(self.models)}")


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
            raise SystemExit(f"omega: provider {name!r} is missing \"baseUrl\" in {CONFIG_PATH}")
        providers[name] = Provider(
            name=name, type=ptype, base_url=base_url,
            api_key_env=p.get("apiKeyEnv", ""),
            api_key_literal=p.get("apiKey", ""),
            api_key_cmd=p.get("apiKeyCmd", ""),
        )

    models: dict[str, Model] = {}
    for alias, m in (raw.get("models") or {}).items():
        models[alias] = Model(alias, m["model"], m["provider"], m.get("context", 128000),
                              m.get("effort"), m.get("fallback"))
    # A hand-written config predating the catalog would otherwise leave the
    # /model picker empty; built-ins fill in wherever their provider exists.
    for alias, m in DEFAULTS["models"].items():
        if alias not in models and m["provider"] in providers:
            models[alias] = Model(alias, m["model"], m["provider"], m.get("context", 128000),
                                  m.get("effort"), m.get("fallback"))

    verify_raw = raw.get("verify") or {}
    verify_checks = verify_raw.get("checks")
    if verify_checks is not None:
        verify_checks = [str(c) for c in verify_checks]

    hooks_raw = raw.get("hooks") or {}
    hooks_cfg: dict[str, list[HookRule]] = {}
    for stage in ("pre_tool", "post_tool"):
        hooks_cfg[stage] = [HookRule(tools=list(entry.get("tools", [])), command=entry["command"])
                            for entry in (hooks_raw.get(stage) or [])]

    cfg = Config(models=models, providers=providers,
                verify_auto=bool(verify_raw.get("auto", True)),
                verify_checks=verify_checks,
                review_auto=bool(raw.get("review_auto", True)),
                hooks=hooks_cfg)

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


def mcp_config() -> dict[str, dict[str, Any]]:
    """The omega-owned "mcp" block as written on disk -- unlike mcp.discover(),
    this never mixes in Claude Code's servers, so the connections manager can
    tell "configured in omega" apart from "merely importable"."""
    if not CONFIG_PATH.exists():
        return {}
    raw: dict[str, Any] = json.loads(_strip_jsonc(CONFIG_PATH.read_text())).get("mcp", {})
    return raw
