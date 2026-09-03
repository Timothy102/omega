import json
import re

from .. import llm
from . import store

SYSTEM = """You maintain a personal knowledge graph for a coding agent. Given a
list of memory nodes, find near-duplicates to merge, direct contradictions, and
any importance/volatility corrections.

Respond with STRICT JSON only -- no prose, no code fences:
{"merge": [{"keep": "<id>", "drop": "<id>", "merged_body": "<str>"}],
 "contradict": [["<id>", "<id>"]],
 "retag": [{"id": "<id>", "volatility": "<str>", "importance": <float>}]}

merge.keep/drop must be ids taken from the input. retag entries may omit
either field. Return empty lists where nothing applies."""


def _render(nodes: list) -> str:
    return "\n\n".join(
        f"[{n['id']}] type={n['type']} confidence={n['confidence']} "
        f"volatility={n['volatility']} importance={n['importance']}\n"
        f"{n['title']}\n{n['body']}"
        for n in nodes)


def _parse(text: str) -> dict:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    return json.loads(text)


async def run(cfg, scope: str, min_new: int = 5, force: bool = False) -> str:
    if not force and store.since_consolidation(scope) < min_new:
        return ""

    nodes = store.recent(scope, limit=60)
    if not nodes:
        store.reset_consolidation(scope)
        return "memory: nothing to consolidate"

    role = cfg.role("memory") if "memory" in cfg.roles else cfg.role("compact")
    text = ""
    async for kind, payload in llm.stream(
            role, [{"role": "system", "content": SYSTEM},
                   {"role": "user", "content": _render(nodes)}]):
        if kind == "done":
            text = payload.text

    try:
        data = _parse(text)
    except (json.JSONDecodeError, TypeError, AttributeError):
        return "consolidation skipped: unparseable model output"

    by_id = {n["id"]: n for n in nodes}
    merged = contradicted = retagged = 0

    for m in (data.get("merge") or []):
        keep, drop = by_id.get(m.get("keep")), by_id.get(m.get("drop"))
        if keep is None or drop is None or keep["id"] == drop["id"]:
            continue
        new_id = store.write_node(
            scope, keep["type"], keep["title"], m.get("merged_body") or keep["body"],
            confidence=keep["confidence"], volatility=keep["volatility"],
            sensitivity=keep["sensitivity"], importance=keep["importance"])
        store.add_edge(scope, new_id, keep["id"], "supersedes")
        store.add_edge(scope, new_id, drop["id"], "supersedes")
        store.mark_superseded(scope, keep["id"], new_id)
        store.mark_superseded(scope, drop["id"], new_id)
        merged += 1

    for pair in (data.get("contradict") or []):
        if len(pair) != 2 or pair[0] not in by_id or pair[1] not in by_id:
            continue
        store.add_edge(scope, pair[0], pair[1], "contradicts")
        contradicted += 1

    for r in (data.get("retag") or []):
        node_id = r.get("id")
        if node_id not in by_id:
            continue
        fields = {}
        if r.get("volatility") in store.VOLATILITIES:
            fields["volatility"] = r["volatility"]
        if "importance" in r:
            try:
                fields["importance"] = float(r["importance"])
            except (TypeError, ValueError):
                pass
        if fields:
            store.retag(scope, node_id, **fields)
            retagged += 1

    store.reset_consolidation(scope)
    if not (merged or contradicted or retagged):
        return "memory: nothing to consolidate"
    plural = "s" if contradicted != 1 else ""
    return f"memory: merged {merged}, flagged {contradicted} contradiction{plural}, retagged {retagged}"
