import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONFIG_PATH = Path(os.environ.get("RIG_CONFIG", Path.home() / ".rig" / "config.json"))

DEFAULTS: dict[str, Any] = {
    "providers": {
        "inference-net": {
            "baseUrl": "https://api.inference.net/v1",
            "apiKeyEnv": "INFERENCE_API_KEY",
        }
    },
    "roles": {
        "main":          {"model": "kimi-k3", "provider": "inference-net", "context": 1048576},
        "plan":          {"model": "kimi-k3", "provider": "inference-net", "context": 1048576},
        "subagent_fast": {"model": "glm-5.3-flash", "provider": "inference-net", "context": 128000},
        "subagent_mid":  {"model": "kimi-k3", "provider": "inference-net", "context": 1048576},
        "compact":       {"model": "glm-5.3-flash", "provider": "inference-net", "context": 128000},
        "memory":        {"model": "glm-5.3-flash", "provider": "inference-net", "context": 128000},
    },
}


@dataclass
class Provider:
    name: str
    base_url: str
    api_key: str


@dataclass
class Role:
    model: str
    provider: Provider
    context: int = 128000


@dataclass
class Config:
    roles: dict[str, Role] = field(default_factory=dict)

    def role(self, name: str) -> Role:
        if name not in self.roles:
            raise KeyError(f"no role {name!r}; have {sorted(self.roles)}")
        return self.roles[name]


def _strip_jsonc(text: str) -> str:
    text = re.sub(r"^\s*//.*$", "", text, flags=re.M)
    return re.sub(r",(\s*[}\]])", r"\1", text)


def load() -> Config:
    raw: dict[str, Any] = DEFAULTS
    if CONFIG_PATH.exists():
        raw = json.loads(_strip_jsonc(CONFIG_PATH.read_text()))

    providers: dict[str, Provider] = {}
    for name, p in raw["providers"].items():
        key = p.get("apiKey") or os.environ.get(p.get("apiKeyEnv", ""), "")
        if not key:
            hint = (f"export {p['apiKeyEnv']}=..." if p.get("apiKeyEnv")
                    else f'set "apiKey" in {CONFIG_PATH}')
            raise SystemExit(
                f"rig: no API key for provider {name!r}.\n"
                f"  Run `rig setup` to configure one, or {hint}")
        providers[name] = Provider(name, p["baseUrl"].rstrip("/"), key)

    roles: dict[str, Role] = {}
    for name, r in raw["roles"].items():
        roles[name] = Role(r["model"], providers[r["provider"]], r.get("context", 128000))
    return Config(roles)


def mcp_names() -> list[str]:
    if not CONFIG_PATH.exists():
        return []
    import json as _json
    return list(_json.loads(_strip_jsonc(CONFIG_PATH.read_text())).get("mcp", {}))
