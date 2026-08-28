import re
from pathlib import Path

MEM_DIR = Path.home() / ".rig" / "memory"
INDEX = MEM_DIR / "INDEX.md"


def ensure():
    MEM_DIR.mkdir(parents=True, exist_ok=True)
    if not INDEX.exists():
        INDEX.write_text("# Memory Index\n\n")


def preamble() -> str:
    ensure()
    return INDEX.read_text().strip()


def slugify(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60]


def save(title: str, body: str) -> str:
    ensure()
    slug = slugify(title)
    path = MEM_DIR / f"{slug}.md"
    path.write_text(f"# {title}\n\n{body.strip()}\n")

    line = f"- [{title}]({slug}.md)\n"
    index = INDEX.read_text()
    if f"]({slug}.md)" not in index:
        INDEX.write_text(index.rstrip() + "\n" + line)
    return f"saved memory {slug!r}"


def recall(query: str) -> str:
    ensure()
    hits = []
    for p in sorted(MEM_DIR.glob("*.md")):
        if p.name == "INDEX.md":
            continue
        text = p.read_text()
        if re.search(query, text, re.I):
            hits.append(f"--- {p.name} ---\n{text}")
    return "\n\n".join(hits) or "(no matching memories)"
