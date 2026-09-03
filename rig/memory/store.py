import os
import re
import secrets
import sqlite3
import time
from pathlib import Path

GLOBAL_DIR = Path.home() / ".rig" / "memory"

SCOPES = {"global", "project"}
TYPES = {"fact", "preference", "decision", "entity", "file_note", "open_question"}
VOLATILITIES = {"stable", "medium", "volatile"}
SENSITIVITIES = {"normal", "personal", "sensitive"}
RELATIONS = {"relates_to", "supersedes", "depends_on", "part_of", "mentions", "contradicts"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
  id TEXT PRIMARY KEY,
  type TEXT NOT NULL,
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  confidence REAL DEFAULT 0.8,
  volatility TEXT DEFAULT 'stable',
  sensitivity TEXT DEFAULT 'normal',
  importance REAL DEFAULT 0.5,
  superseded_by TEXT REFERENCES nodes(id),
  source_session_id TEXT,
  created REAL, updated REAL, last_accessed REAL, access_count INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS edges (
  src TEXT REFERENCES nodes(id), dst TEXT REFERENCES nodes(id),
  relation TEXT,
  created REAL,
  PRIMARY KEY (src, dst, relation)
);
CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts USING fts5(
  title, body, content='nodes', content_rowid='rowid');
CREATE TRIGGER IF NOT EXISTS nodes_ai AFTER INSERT ON nodes BEGIN
  INSERT INTO nodes_fts(rowid, title, body) VALUES (new.rowid, new.title, new.body);
END;
CREATE TRIGGER IF NOT EXISTS nodes_ad AFTER DELETE ON nodes BEGIN
  INSERT INTO nodes_fts(nodes_fts, rowid, title, body) VALUES ('delete', old.rowid, old.title, old.body);
END;
CREATE TRIGGER IF NOT EXISTS nodes_au AFTER UPDATE ON nodes BEGIN
  INSERT INTO nodes_fts(nodes_fts, rowid, title, body) VALUES ('delete', old.rowid, old.title, old.body);
  INSERT INTO nodes_fts(rowid, title, body) VALUES (new.rowid, new.title, new.body);
END;
CREATE TABLE IF NOT EXISTS meta (
  last_consolidated REAL, nodes_since_consolidation INTEGER
);
"""


def project_dir(cwd=None) -> Path:
    return Path(cwd or os.getcwd()) / ".rig"


def _db_path(scope: str, cwd=None) -> Path:
    if scope not in SCOPES:
        raise ValueError(f"invalid scope {scope!r}; expected one of {sorted(SCOPES)}")
    if scope == "global":
        return GLOBAL_DIR / "memory.db"
    return project_dir(cwd) / "memory.db"


def db_exists(scope: str, cwd=None) -> bool:
    return _db_path(scope, cwd).exists()


def _bootstrap_project(cwd=None):
    root = Path(cwd or os.getcwd())
    project_dir(cwd).mkdir(parents=True, exist_ok=True)
    # Never touch .gitignore outside an actual repo -- a bare cwd or $HOME
    # isn't ours to modify.
    if not (root / ".git").is_dir():
        return
    gitignore = root / ".gitignore"
    line = ".rig/"
    existing = gitignore.read_text() if gitignore.exists() else ""
    if line in existing.splitlines():
        return
    with gitignore.open("a") as f:
        if existing and not existing.endswith("\n"):
            f.write("\n")
        f.write(line + "\n")


def connect(scope: str, cwd=None) -> sqlite3.Connection:
    path = _db_path(scope, cwd)
    if scope == "project" and not path.exists():
        _bootstrap_project(cwd)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    if conn.execute("SELECT COUNT(*) FROM meta").fetchone()[0] == 0:
        conn.execute("INSERT INTO meta (last_consolidated, nodes_since_consolidation) "
                     "VALUES (0, 0)")
        conn.commit()
    return conn


def _validate(**kwargs):
    checks = {"type": TYPES, "volatility": VOLATILITIES, "sensitivity": SENSITIVITIES,
              "relation": RELATIONS}
    for field, value in kwargs.items():
        if value is not None and value not in checks[field]:
            raise ValueError(f"invalid {field} {value!r}; expected one of {sorted(checks[field])}")


def write_node(scope, type, title, body, confidence=0.8, volatility="stable",
               sensitivity="normal", importance=0.5, source_session_id=None, cwd=None) -> str:
    _validate(type=type, volatility=volatility, sensitivity=sensitivity)
    conn = connect(scope, cwd)
    try:
        node_id = secrets.token_hex(4)
        now = time.time()
        conn.execute(
            "INSERT INTO nodes (id, type, title, body, confidence, volatility, sensitivity, "
            "importance, source_session_id, created, updated, last_accessed, access_count) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0)",
            (node_id, type, title, body, confidence, volatility, sensitivity, importance,
             source_session_id, now, now, now))
        conn.commit()
        return node_id
    finally:
        conn.close()


def add_edge(scope, src, dst, relation, cwd=None):
    _validate(relation=relation)
    conn = connect(scope, cwd)
    try:
        conn.execute("INSERT OR IGNORE INTO edges (src, dst, relation, created) VALUES (?,?,?,?)",
                     (src, dst, relation, time.time()))
        conn.commit()
    finally:
        conn.close()


def get(scope, id_or_title, cwd=None) -> dict | None:
    conn = connect(scope, cwd)
    try:
        row = conn.execute("SELECT * FROM nodes WHERE id = ?", (id_or_title,)).fetchone()
        if row is None:
            row = conn.execute(
                "SELECT * FROM nodes WHERE lower(title) = lower(?) ORDER BY updated DESC LIMIT 1",
                (id_or_title,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _fts_query_variants(query: str):
    yield query
    terms = re.findall(r"\w+", query)
    if terms:
        yield " ".join(f'"{t}"' for t in terms)


def search(scope, query, type=None, limit=8, include_superseded=False, cwd=None) -> list:
    _validate(type=type)
    conn = connect(scope, cwd)
    try:
        sql = ("SELECT n.*, bm25(nodes_fts) AS rank FROM nodes_fts "
               "JOIN nodes n ON n.rowid = nodes_fts.rowid WHERE nodes_fts MATCH ?")
        extra = []
        if type is not None:
            sql += " AND n.type = ?"
            extra.append(type)
        if not include_superseded:
            sql += " AND n.superseded_by IS NULL"
        sql += " ORDER BY rank LIMIT ?"

        rows = None
        for variant in _fts_query_variants(query):
            try:
                # bm25: smaller (more negative) is a better match, so plain
                # ascending order (the default) already ranks best-first.
                rows = conn.execute(sql, (variant, *extra, limit)).fetchall()
                break
            except sqlite3.OperationalError:
                continue
        return [dict(r) for r in rows] if rows is not None else []
    finally:
        conn.close()


def neighbors(scope, id, depth=1, cwd=None) -> list:
    depth = min(max(depth, 0), 2)
    conn = connect(scope, cwd)
    try:
        visited = {id}
        frontier = [id]
        results, seen = [], set()
        for _ in range(depth):
            next_frontier = []
            for nid in frontier:
                rows = conn.execute(
                    "SELECT dst AS other, relation, 'out' AS direction FROM edges WHERE src = ? "
                    "UNION ALL "
                    "SELECT src AS other, relation, 'in' AS direction FROM edges WHERE dst = ?",
                    (nid, nid)).fetchall()
                for r in rows:
                    other = r["other"]
                    if other in visited:
                        continue
                    node = conn.execute("SELECT * FROM nodes WHERE id = ?", (other,)).fetchone()
                    if node is None:
                        continue
                    visited.add(other)
                    next_frontier.append(other)
                    if other not in seen:
                        seen.add(other)
                        d = dict(node)
                        d["relation"], d["direction"] = r["relation"], r["direction"]
                        results.append(d)
            frontier = next_frontier
        return results
    finally:
        conn.close()


def touch(scope, id, cwd=None):
    conn = connect(scope, cwd)
    try:
        conn.execute("UPDATE nodes SET last_accessed = ?, access_count = access_count + 1 "
                     "WHERE id = ?", (time.time(), id))
        conn.commit()
    finally:
        conn.close()


def mark_superseded(scope, old_id, new_id, cwd=None):
    conn = connect(scope, cwd)
    try:
        conn.execute("UPDATE nodes SET superseded_by = ?, updated = ? WHERE id = ?",
                     (new_id, time.time(), old_id))
        conn.commit()
    finally:
        conn.close()


def retag(scope, id, cwd=None, **fields):
    fields = {k: v for k, v in fields.items() if k in ("volatility", "importance")
              and v is not None}
    if not fields:
        return
    if "volatility" in fields:
        _validate(volatility=fields["volatility"])
    conn = connect(scope, cwd)
    try:
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(f"UPDATE nodes SET {set_clause}, updated = ? WHERE id = ?",
                     (*fields.values(), time.time(), id))
        conn.commit()
    finally:
        conn.close()


def recent(scope, since=None, limit=50, cwd=None) -> list:
    conn = connect(scope, cwd)
    try:
        sql = "SELECT * FROM nodes WHERE superseded_by IS NULL"
        params = []
        if since is not None:
            sql += " AND created >= ?"
            params.append(since)
        sql += " ORDER BY created DESC LIMIT ?"
        params.append(limit)
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def all_nodes(scope, include_superseded=False, cwd=None) -> list:
    conn = connect(scope, cwd)
    try:
        sql = "SELECT * FROM nodes"
        if not include_superseded:
            sql += " WHERE superseded_by IS NULL"
        return [dict(r) for r in conn.execute(sql).fetchall()]
    finally:
        conn.close()


def bump_since_consolidation(scope, cwd=None):
    conn = connect(scope, cwd)
    try:
        conn.execute("UPDATE meta SET nodes_since_consolidation = nodes_since_consolidation + 1")
        conn.commit()
    finally:
        conn.close()


def reset_consolidation(scope, cwd=None):
    conn = connect(scope, cwd)
    try:
        conn.execute("UPDATE meta SET nodes_since_consolidation = 0, last_consolidated = ?",
                     (time.time(),))
        conn.commit()
    finally:
        conn.close()


def since_consolidation(scope, cwd=None) -> int:
    conn = connect(scope, cwd)
    try:
        row = conn.execute("SELECT nodes_since_consolidation FROM meta").fetchone()
        return row[0] if row else 0
    finally:
        conn.close()
