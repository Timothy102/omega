import time

from .. import compact
from . import store
from .store import Node

HALF_LIFE_DAYS = 30

Sections = dict[str, list[str]]


def _recency_decay(updated: float) -> float:
    age_days = max(0.0, (time.time() - updated) / 86400)
    return float(0.5 ** (age_days / HALF_LIFE_DAYS))


def _eligible(node: Node) -> bool:
    if node.get("superseded_by"):
        return False
    if node["sensitivity"] == "sensitive":
        return False
    if node["sensitivity"] == "personal":
        return bool(node["importance"] >= 0.7 and node["volatility"] == "stable")
    if node["volatility"] == "volatile":
        return False
    return True


def _score(node: Node, weight: float) -> float:
    return float(node["importance"] * _recency_decay(node["updated"])
                * (1 + 0.1 * node["access_count"]) * weight)


def _render_line(node: Node) -> str:
    body = " ".join(node["body"].split())
    if len(body) > 200:
        body = body[:200] + "…"
    return f"- **{node['title']}** ({node['type']}, conf {node['confidence']}): {body}"


def _render(sections: Sections) -> str:
    parts = [f"## {name}\n" + "\n".join(lines)
             for name in ("Project", "Global") if (lines := sections[name])]
    return "\n\n".join(parts)


def preamble(cwd: str | None = None, budget_tokens: int = 500) -> str:
    scored: list[tuple[str, float, Node]] = []
    # Reading must never create a project db as a side effect -- that would
    # bootstrap .gitignore for a directory the user never asked omega to touch.
    if store.db_exists("project", cwd):
        for node in store.all_nodes("project", cwd=cwd):
            if _eligible(node):
                scored.append(("Project", _score(node, 1.5), node))
    if store.db_exists("global"):
        for node in store.all_nodes("global"):
            if _eligible(node):
                scored.append(("Global", _score(node, 1.0), node))
    scored.sort(key=lambda x: -x[1])

    sections: Sections = {"Project": [], "Global": []}
    rendered = ""
    for section, _, node in scored:
        candidate = {**sections, section: sections[section] + [_render_line(node)]}
        text = _render(candidate)
        if compact.estimate_tokens([{"role": "user", "content": text}]) > budget_tokens:
            break
        sections, rendered = candidate, text
    return rendered.strip()
