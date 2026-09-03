import time

from .. import compact
from . import store

HALF_LIFE_DAYS = 30


def _recency_decay(updated: float) -> float:
    age_days = max(0.0, (time.time() - updated) / 86400)
    return 0.5 ** (age_days / HALF_LIFE_DAYS)


def _eligible(node: dict) -> bool:
    if node.get("superseded_by"):
        return False
    if node["sensitivity"] == "sensitive":
        return False
    if node["sensitivity"] == "personal":
        return node["importance"] >= 0.7 and node["volatility"] == "stable"
    if node["volatility"] == "volatile":
        return False
    return True


def _score(node: dict, weight: float) -> float:
    return (node["importance"] * _recency_decay(node["updated"])
            * (1 + 0.1 * node["access_count"]) * weight)


def _render_line(node: dict) -> str:
    body = " ".join(node["body"].split())
    if len(body) > 200:
        body = body[:200] + "…"
    return f"- **{node['title']}** ({node['type']}, conf {node['confidence']}): {body}"


def _render(sections: dict) -> str:
    parts = [f"## {name}\n" + "\n".join(lines)
             for name in ("Project", "Global") if (lines := sections[name])]
    return "\n\n".join(parts)


def preamble(cwd=None, budget_tokens=500) -> str:
    scored = []
    # Reading must never create a project db as a side effect -- that would
    # bootstrap .gitignore for a directory the user never asked rig to touch.
    if store.db_exists("project", cwd):
        for node in store.all_nodes("project", cwd=cwd):
            if _eligible(node):
                scored.append(("Project", _score(node, 1.5), node))
    if store.db_exists("global"):
        for node in store.all_nodes("global"):
            if _eligible(node):
                scored.append(("Global", _score(node, 1.0), node))
    scored.sort(key=lambda x: -x[1])

    sections = {"Project": [], "Global": []}
    rendered = ""
    for section, _, node in scored:
        candidate = {**sections, section: sections[section] + [_render_line(node)]}
        text = _render(candidate)
        if compact.estimate_tokens([{"role": "user", "content": text}]) > budget_tokens:
            break
        sections, rendered = candidate, text
    return rendered.strip()
