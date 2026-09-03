import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

Message = dict[str, Any]

DIR = Path.home() / ".omega" / "sessions"

# Shared with __main__'s resumed-session marker so UIs can identify and
# exclude it from turn/message counts shown to the user.
RESUME_PREFIX = "[session resumed"


@dataclass
class Session:
    id: str = ""
    cwd: str = ""
    mode: str = "build"
    created: float = 0.0
    updated: float = 0.0
    history: list[Message] = field(default_factory=list)
    compactions: int = 0
    model_override: str | None = None

    @classmethod
    def new(cls, cwd: str | None = None, mode: str = "build") -> "Session":
        now = time.time()
        sid = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
        return cls(id=sid, cwd=cwd or os.getcwd(), mode=mode, created=now, updated=now)

    @property
    def path(self) -> Path:
        return DIR / f"{self.id}.json"

    @property
    def turns(self) -> int:
        return sum(1 for m in self.history if m.get("role") == "user")

    def first_prompt(self, width: int = 70) -> str:
        for m in self.history:
            if m.get("role") == "user":
                text = " ".join((m.get("content") or "").split())
                return text[:width - 1] + "…" if len(text) > width else text
        return "(empty)"

    def save(self) -> None:
        DIR.mkdir(parents=True, exist_ok=True)
        self.updated = time.time()
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=1))
        tmp.replace(self.path)

    def close_turn(self, history: list[dict[str, Any]], mode: str, interrupted: bool) -> None:
        """Shared by every UI: whatever ran the turn, the session must land in
        the same state afterwards."""
        self.mode = mode
        repair(history)
        if interrupted:
            history.append({"role": "user",
                            "content": "[previous turn interrupted by user]"})
        self.save()


def load(sid: str) -> Session:
    p = DIR / f"{sid}.json"
    if not p.exists():
        matches = sorted(DIR.glob(f"{sid}*.json")) if DIR.exists() else []
        if not matches:
            raise SystemExit(f"omega: no session {sid!r}")
        p = matches[-1]
    raw = json.loads(p.read_text())
    known = set(Session.__dataclass_fields__)
    sess = Session(**{k: v for k, v in raw.items() if k in known})
    repair(sess.history)
    return sess


def repair(history: list[Message]) -> list[Message]:
    """Drop a trailing assistant message whose tool_calls were never answered.
    Providers hard-400 on that shape, which would brick the session forever."""
    while history:
        last = history[-1]
        if last.get("role") == "assistant" and last.get("tool_calls"):
            answered = {m.get("tool_call_id") for m in history if m.get("role") == "tool"}
            if any(c["id"] not in answered for c in last["tool_calls"]):
                history.pop()
                continue
        break
    return history


def all_sessions() -> list[Session]:
    if not DIR.exists():
        return []
    out = []
    for p in DIR.glob("*.json"):
        try:
            out.append(Session(**json.loads(p.read_text())))
        except Exception:
            continue
    return sorted(out, key=lambda s: s.updated, reverse=True)


def latest(cwd: str | None = None) -> Session | None:
    cwd = cwd or os.getcwd()
    # Never fall back to another directory's session: bash and relative paths
    # resolve against the real cwd, so the model would edit the wrong project.
    here = [s for s in all_sessions() if s.cwd == cwd]
    return here[0] if here else None


def render_list(limit: int = 20) -> str:
    """One session per line: wrapped rows make the list unparseable."""
    rows = all_sessions()[:limit]
    if not rows:
        return "no sessions yet"
    now = time.time()
    import shutil
    cols = shutil.get_terminal_size((80, 24)).columns
    lines = [f"{'ID':<22}{'AGE':>6}{'TURNS':>6}  {'CWD':<24}PROMPT"]
    for s in rows:
        age = now - s.updated
        ago = (f"{age/86400:.0f}d" if age > 86400 else
               f"{age/3600:.0f}h" if age > 3600 else f"{age/60:.0f}m")
        cwd = s.cwd.replace(str(Path.home()), "~")
        if len(cwd) > 23:
            cwd = "…" + cwd[-22:]
        lines.append(f"{s.id:<22}{ago:>6}{s.turns:>6}  {cwd:<24}"
                     f"{s.first_prompt(200)}")
    # hard-truncate: a wrapped row makes the list unreadable and unparseable
    # Stay strictly under the terminal width: a line of exactly `cols` chars
    # still wraps in some terminals.
    return "\n".join(l[:cols - 2] + "…" if len(l) >= cols - 1 else l for l in lines)
