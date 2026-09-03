import re

from .. import tools
from . import store

S = {"type": "string"}
I = {"type": "integer"}
F = {"type": "number"}

# Safety net, same shape as permissions.FORBIDDEN_PATTERNS: mislabeling a
# node's sensitivity must not be the only thing standing between a leaked
# secret and the preamble every future turn gets injected with.
SENSITIVE_PATTERNS = [
    (re.compile(r"\bsk-[A-Za-z0-9]{10,}"), "looks like an API key (sk-...)"),
    (re.compile(r"\bghp_[A-Za-z0-9]{10,}"), "looks like a GitHub token (ghp_...)"),
    (re.compile(r"\bxox[bp]-[A-Za-z0-9-]{10,}"), "looks like a Slack token (xox[bp]-...)"),
    (re.compile(r"\bAKIA[A-Z0-9]{12,}"), "looks like an AWS access key (AKIA...)"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY"), "contains a private key block"),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "contains an email address"),
    (re.compile(r"\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"),
     "contains a phone number"),
    (re.compile(r"\b(password|passwd|secret)\s*[:=]", re.I), "contains a password/secret assignment"),
    # No `/` in the class and letters+digits both required: file paths and
    # long snake_case identifiers are the normal content of a coding agent's
    # memory and must not be mistaken for secrets.
    (re.compile(r"(?=[A-Za-z0-9+=_-]*\d)(?=[A-Za-z0-9+=_-]*[A-Za-z])[A-Za-z0-9+=_-]{32,}"),
     "contains a long token-like blob"),
]


def _scan_sensitive(*texts) -> list:
    blob = "\n".join(texts)
    reasons = []
    for pattern, reason in SENSITIVE_PATTERNS:
        if pattern.search(blob) and reason not in reasons:
            reasons.append(reason)
    return reasons


def _resolve(id_or_title):
    """Look up a node across both scopes, project first. Never triggers the
    project db's .gitignore bootstrap by probing a db that doesn't exist yet."""
    for scope in ("project", "global"):
        if scope == "project" and not store.db_exists("project"):
            continue
        node = store.get(scope, id_or_title)
        if node:
            return scope, node
    return None, None


@tools.tool(
    "remember",
    "Save a durable fact about the user or their projects to long-term memory "
    "so it survives into FUTURE sessions. The current conversation is already "
    "remembered without this. type: fact|preference|decision|entity|file_note|"
    "open_question. scope: project (default) or global (true across projects). "
    "volatility: stable|medium|volatile -- volatile facts (current bug, mood, "
    "this week's focus) are never auto-injected, only recallable. sensitivity: "
    "normal|personal|sensitive -- personal facts are rarely injected, sensitive "
    "never. confidence and importance are 0-1.",
    {"title": S, "body": S, "type": S, "scope": S, "confidence": F,
     "volatility": S, "sensitivity": S, "importance": F,
     "relates_to": {"type": "array", "items": S}},
    ["title", "body"], mutates=True)
def _remember(title, body, type="fact", scope="project", confidence=0.8,
              volatility="stable", sensitivity="normal", importance=0.5,
              relates_to=None):
    reasons = _scan_sensitive(title, body)
    if reasons:
        sensitivity = "sensitive"
    try:
        node_id = store.write_node(scope, type, title, body, confidence=confidence,
                                   volatility=volatility, sensitivity=sensitivity,
                                   importance=importance, source_session_id=tools.SESSION_ID)
    except ValueError as e:
        return f"error: {e}"

    unresolved = []
    for ref in relates_to or []:
        target = store.get(scope, ref)
        if target is None:
            unresolved.append(ref)
        else:
            store.add_edge(scope, node_id, target["id"], "relates_to")
    store.bump_since_consolidation(scope)

    msg = f"remembered [{node_id}] {title!r} ({scope})"
    if reasons:
        msg += f"; sensitivity forced to 'sensitive': {'; '.join(reasons)}"
    if unresolved:
        msg += f"; could not resolve relates_to: {', '.join(unresolved)}"
    return msg


@tools.tool(
    "recall",
    "Search LONG-TERM memory notes saved across sessions with the `remember` "
    "tool. This is NOT the conversation: everything said in this session, "
    "including a resumed one, is already in your context above. Do not call "
    "this to answer questions about what was just discussed.",
    {"query": S, "scope": S, "type": S, "depth": I}, ["query"])
def _recall(query, scope="both", type=None, depth=1):
    if scope not in ("both", "project", "global"):
        return f"error: invalid scope {scope!r}; expected 'both', 'project' or 'global'"
    scopes = ["project", "global"] if scope == "both" else [scope]

    blocks = []
    for sc in scopes:
        if sc == "project" and not store.db_exists("project"):
            continue
        try:
            hits = store.search(sc, query, type=type, limit=8)
        except ValueError as e:
            return f"error: {e}"
        for node in hits:
            store.touch(sc, node["id"])
            lines = [f"[{node['id']}] {node['type']} · {sc} · conf {node['confidence']} · "
                     f"{node['volatility']} · importance {node['importance']}",
                     node["title"], node["body"]]
            for nb in store.neighbors(sc, node["id"], depth=depth):
                lines.append(f"  → {nb['relation']} ({nb['direction']}): "
                             f"[{nb['id']}] {nb['title']}")
            blocks.append("\n".join(lines))

    if not blocks:
        return "(no matching memories)"
    return tools.truncate("\n\n".join(blocks))


@tools.tool(
    "supersede",
    "Replace an outdated memory node with a corrected one. The old node stops "
    "appearing in recall/preamble by default but stays queryable by id.",
    {"old": S, "new_body": S, "confidence": F}, ["old", "new_body"], mutates=True)
def _supersede(old, new_body, confidence=None):
    scope, node = _resolve(old)
    if node is None:
        return f"error: no memory node found matching {old!r}"
    new_id = store.write_node(
        scope, node["type"], node["title"], new_body,
        confidence=confidence if confidence is not None else node["confidence"],
        volatility=node["volatility"], sensitivity=node["sensitivity"],
        importance=node["importance"], source_session_id=tools.SESSION_ID)
    store.add_edge(scope, new_id, node["id"], "supersedes")
    store.mark_superseded(scope, node["id"], new_id)
    store.bump_since_consolidation(scope)
    return f"superseded [{node['id']}] with [{new_id}] ({scope})"


@tools.tool(
    "link",
    "Add an explicit relation between two existing memory nodes (contradicts, "
    "depends_on, part_of, mentions, relates_to).",
    {"a": S, "b": S, "relation": S}, ["a", "b", "relation"], mutates=True)
def _link(a, b, relation):
    scope_a, node_a = _resolve(a)
    if node_a is None:
        return f"error: no memory node found matching {a!r}"
    # Edges live inside a single sqlite db, so both ends must be in the same
    # scope -- look b up in a's scope directly rather than resolving it
    # independently, which could silently pick the wrong-scope node.
    node_b = store.get(scope_a, b)
    if node_b is None:
        scope_b, other = _resolve(b)
        if other is None:
            return f"error: no memory node found matching {b!r}"
        return (f"error: {a!r} is in scope {scope_a!r} but {b!r} is in scope "
                f"{scope_b!r}; link requires both nodes in the same scope")
    try:
        store.add_edge(scope_a, node_a["id"], node_b["id"], relation)
    except ValueError as e:
        return f"error: {e}"
    return f"linked [{node_a['id']}] --{relation}--> [{node_b['id']}] ({scope_a})"
