"""Pure text formatting shared by both UIs: the dim one-liners for
`ToolStart`/`ToolEnd`/`SubagentSpawned`/`SubagentDone`/`Compacted`/
`MemoryWrite`/`MemoryConsolidated`/`Error`, the per-tool call/outcome
descriptions, path shortening, and the category color palette. Shared here so
`ui/plain.py` and `ui/tui/transcript.py` cannot drift on wording or color.
Callers own presentation concerns these functions don't: `ui/plain.py`'s
leading blank-line spacing is added at the call site, not here.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from .. import events

# ---- paths ---------------------------------------------------------------


def relpath(path: str) -> str:
    """`path` relative to the cwd, or `~`-abbreviated when it's outside the
    cwd tree -- an absolute path is mostly noise once you know where you are."""
    if not path:
        return path
    try:
        p = Path(path).expanduser()
        abs_p = p if p.is_absolute() else Path(os.getcwd()) / p
        rel = os.path.relpath(abs_p, os.getcwd())
        if not rel.startswith(".."):
            return rel
    except (ValueError, OSError):
        pass
    home = str(Path.home())
    if path.startswith(home):
        return "~" + path[len(home):]
    return path


def _truncate(text: Any, limit: int = 60) -> str:
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[:limit - 1] + "…"


def _q(text: Any, limit: int = 60) -> str:
    return f"'{_truncate(text, limit)}'"


def truncate_middle(text: str, width: int) -> str:
    """Cut from the middle rather than the end -- a path or command that
    overruns the terminal keeps both its recognizable start and its
    (usually more specific) tail instead of losing the tail to a fixed cutoff."""
    if width <= 0 or len(text) <= width:
        return text
    if width <= 1:
        return text[:width]
    keep = width - 1
    left = (keep + 1) // 2
    right = keep - left
    return text[:left] + "…" + (text[-right:] if right else "")


def fmt_num(n: float) -> str:
    """`13600 -> '13.6k'`, `1_000_000 -> '1.0M'`, `42 -> '42'` -- one shared
    scale so chars/tokens/counts never disagree on how big "big" is."""
    n = float(n)
    sign = "-" if n < 0 else ""
    n = abs(n)
    if n >= 1_000_000:
        return f"{sign}{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{sign}{n / 1_000:.1f}k"
    return f"{sign}{int(n)}"


_TAG_RE = re.compile(r"\[/?[a-zA-Z0-9_ $#,.\-]*\]")


def visible_len(markup: str) -> int:
    """Length of `markup` with `[style]...[/style]` tags removed -- good
    enough for column math since every glyph this UI prints is single-width."""
    return len(_TAG_RE.sub("", markup))


def right_align(left: str, right: str, width: int) -> str:
    """`left`, padded with spaces so `right` lands at column `width`. Falls
    back to a single space when the two would collide in a narrow terminal."""
    gap = width - visible_len(left) - visible_len(right)
    if gap < 1:
        return f"{left} {right}"
    return f"{left}{' ' * gap}{right}"


def _without_name(name: str, preview: str) -> str:
    """`describe_call`'s output always leads with the tool name (sidebar.py's
    path/server extraction depends on that), but the TUI renders the name
    separately in bold -- repeating it verbatim on the same line reads as a
    typo, not emphasis, so strip it back off for display."""
    if preview == name:
        return ""
    prefix = name + "  "
    return preview[len(prefix):] if preview.startswith(prefix) else preview


def pad_name(name: str, width: int = 12) -> str:
    """Left-justify `name` to `width` columns (never truncated) so a run of
    tool lines lines up its detail text in one column."""
    return f"{name:<{width}}" if len(name) < width else name + "  "


def abbrev_cwd(path: str, segments: int = 3) -> str:
    """`~`-abbreviated, trimmed to its last `segments` path components -- the
    header bar has one line to spend on a cwd that can otherwise be very
    long."""
    home = str(Path.home())
    if path == home or path.startswith(home + "/"):
        rest = path[len(home):].lstrip("/")
        parts = rest.split("/") if rest else []
        if len(parts) > segments:
            return "~/…/" + "/".join(parts[-segments:])
        return "~/" + "/".join(parts) if parts else "~"
    parts = [p for p in path.split("/") if p]
    if len(parts) > segments:
        return ".../" + "/".join(parts[-segments:])
    return path


def relative_age(seconds: float) -> str:
    """`3661 -> '1h'` -- coarse, human "how long ago" for a timestamp diff."""
    seconds = max(0.0, seconds)
    if seconds < 60:
        return f"{int(seconds)}s"
    minutes = seconds / 60
    if minutes < 60:
        return f"{int(minutes)}m"
    hours = minutes / 60
    if hours < 24:
        return f"{int(hours)}h"
    return f"{int(hours / 24)}d"


# ---- category colors -------------------------------------------------------

_MEMORY_TOOLS = {"remember", "recall", "supersede", "link"}
_ARTIFACT_TOOLS = {"fetch_result", "list_artifacts", "save_artifact", "update_artifact"}
_MCP_TOOLS = {"call_tool", "find_tools"}

STYLE = {
    "read": "cyan", "write": "yellow", "bash": "magenta", "subagent": "green",
    "memory": "blue", "artifact": "dim cyan", "ask_user": "bold yellow",
    "mcp": "bright_blue", "error": "red", "outcome": "dim",
}


def category(name: str) -> str:
    if name in ("read", "glob", "grep"):
        return "read"
    if name in ("write", "edit"):
        return "write"
    if name == "bash":
        return "bash"
    if name == "subagent":
        return "subagent"
    if name in _MEMORY_TOOLS:
        return "memory"
    if name in _ARTIFACT_TOOLS:
        return "artifact"
    if name == "ask_user":
        return "ask_user"
    if name in _MCP_TOOLS or name.startswith("mcp__"):
        return "mcp"
    return "read"


def style_for(name: str) -> str:
    return STYLE.get(category(name), "white")


# ---- per-tool call descriptions (C9a) --------------------------------------

def describe_call(name: str, args: dict[str, Any]) -> str:
    """What a tool call is actually doing, for `ToolStart.args_preview` --
    shared so plain and the TUI never disagree on the wording."""
    get = args.get
    if name == "read":
        path = relpath(str(get("path") or ""))
        offset, limit = get("offset") or 0, get("limit")
        suffix = f":{offset}+{limit}" if offset or (limit and limit != 2000) else ""
        return f"read  {path}{suffix}"
    if name == "grep":
        pattern = _truncate(get("pattern") or "")
        path = relpath(str(get("path") or "."))
        glob = get("glob") or "*"
        return f"grep  /{pattern}/  in {path}  ({glob})"
    if name == "glob":
        pattern = _truncate(get("pattern") or "")
        path = relpath(str(get("path") or "."))
        return f"glob  {pattern}  in {path}"
    if name == "bash":
        command = str(get("command") or "").replace("\n", "⏎")
        return f"bash  $ {_truncate(command, 80)}"
    if name == "write":
        content = get("content") or ""
        return f"write  {relpath(str(get('path') or ''))}  ({len(content)} chars)"
    if name == "edit":
        return f"edit  {relpath(str(get('path') or ''))}"
    if name == "fetch_result":
        offset, limit = get("offset") or 0, get("limit") or 4000
        return f"fetch_result  artifact {get('id') or ''} @{offset}+{limit}"
    if name == "list_artifacts":
        return "list_artifacts"
    if name == "save_artifact":
        return f"save_artifact  {_q(get('title') or '')}"
    if name == "update_artifact":
        return f"update_artifact  artifact {get('id') or ''}"
    if name == "recall":
        return f"recall  {_q(get('query') or '')}  {get('scope') or 'both'}"
    if name == "remember":
        return f"remember  {_q(get('title') or '')}  {get('type') or 'fact'}/{get('scope') or 'project'}"
    if name == "supersede":
        return f"supersede  {_q(get('old') or '')}"
    if name == "link":
        return f"link  {get('a') or ''} → {get('b') or ''} ({get('relation') or ''})"
    if name == "subagent":
        tier = get("tier") or "fast"
        return f"subagent({tier})  {_truncate(get('task') or '')}"
    if name == "ask_user":
        return f"ask_user  {_q(get('question') or '')}"
    if name == "find_tools":
        return f"find_tools  {_q(get('query') or '')}"
    if name == "call_tool":
        tool_name = str(get("name") or "")
        parts = tool_name.split("__")
        label = f"{parts[1]}:{parts[2]}" if len(parts) >= 3 and tool_name.startswith("mcp__") else tool_name
        kwargs = get("arguments") or {}
        items = list(kwargs.items())
        kv = ", ".join(f"{k}={v}" for k, v in items[:4])
        more = ", …" if len(items) > 4 else ""
        return f"call_tool  {label}({kv}{more})"
    detail = str(get("path") or get("pattern") or get("command") or get("task")
                or get("query") or "")
    return f"{name}  {_truncate(detail)}" if detail else name


# ---- outcome folding (C7b/C9a) ---------------------------------------------

_EXIT_RE = re.compile(r"\[exit (\d+)\]")
_FULL_CHARS_RE = re.compile(r"\[full output: (\d+) chars")


def result_char_count(text: str, offloaded: bool) -> int:
    """The FULL result length, not the (possibly truncated) preview stored in
    `ToolEnd.result_preview` -- an offloaded result states its true length in
    its own footer, so parse that instead of measuring the shortened text."""
    if offloaded:
        m = _FULL_CHARS_RE.search(text)
        if m:
            return int(m.group(1))
    return len(text)


def describe_outcome(name: str, text: str, duration_s: float, offloaded: bool,
                     artifact_id: str | None, result_chars: int) -> str:
    """The `→ …` suffix folded onto a tool's line on `ToolEnd`. Empty string
    means nothing worth showing (a fast, ordinary, non-offloaded call)."""
    stripped = text.strip()
    if stripped.startswith("error:"):
        return f"→ error: {_truncate(stripped[len('error:'):].strip(), 80)}"

    parts: list[str] = []
    no_hits = stripped in ("(no matches)", "")
    if name == "read":
        parts.append(f"{len(text.splitlines())} lines")
    elif name == "grep":
        parts.append(f"{0 if no_hits else len(text.splitlines())} matches")
    elif name == "glob":
        parts.append(f"{0 if no_hits else len(text.splitlines())} files")
    elif name == "bash":
        m = _EXIT_RE.search(text)
        if m and m.group(1) != "0":
            parts.append(f"exit {m.group(1)}")
    elif name == "find_tools":
        if stripped.startswith("no tools matched") or not stripped:
            parts.append("no match")
        else:
            count = stripped.count("\n\n") + 1
            parts.append(f"{count} tool{'' if count == 1 else 's'}")
    elif name == "recall":
        if stripped == "(no matching memories)" or not stripped:
            parts.append("none")
        else:
            count = stripped.count("\n\n") + 1
            parts.append(f"{count} memor{'y' if count == 1 else 'ies'}")
    elif name == "call_tool" and not offloaded:
        parts.append(f"{fmt_num(result_chars)} chars")

    if duration_s >= 1:
        parts.append(f"{duration_s:.1f}s")
    if offloaded:
        parts.append(f"{fmt_num(result_chars)} chars · artifact {artifact_id}")

    return "→ " + " · ".join(parts) if parts else ""


# ---- transcript one-liners --------------------------------------------------

def tool_start(ev: events.ToolStart, *, show_subagent_suffix: bool = True,
               width: int | None = None) -> str:
    style = style_for(ev.name)
    detail = _without_name(ev.name, ev.args_preview)
    if width is not None:
        detail = truncate_middle(detail, width)
    name_col = pad_name(ev.name)
    if ev.subagent_id:
        suffix = f"  ({ev.tier}·{ev.subagent_id})" if show_subagent_suffix else ""
        return f"  [dim]└ [{style}]{name_col}[/{style}]{detail}{suffix}[/dim]"
    return f"[{style}]●[/{style}] [bold {style}]{name_col}[/bold {style}]{detail}"


def tool_end(ev: events.ToolEnd) -> str | None:
    if ev.offloaded:
        return f"  [dim]↳ {ev.outcome or f'offloaded → artifact {ev.artifact_id}'}[/dim]"
    if ev.outcome.startswith("→ error") or (ev.name == "bash" and "exit" in ev.outcome):
        return f"  [dim]{ev.outcome}[/dim]"
    if ev.name in ("find_tools", "call_tool", "recall") and ev.outcome:
        return f"  [dim]{ev.outcome}[/dim]"
    return None


def subagent_spawned(ev: events.SubagentSpawned, *, show_id: bool = False) -> str:
    id_suffix = f"  [dim]{ev.subagent_id}[/dim]" if show_id else ""
    return (f"[green]●[/green] [bold green]subagent[/bold green]([green]{ev.tier}[/green]) "
           f"{ev.task_preview}{id_suffix}")


def subagent_done(ev: events.SubagentDone, task_preview: str = "", elapsed_s: float = 0.0) -> str:
    task = task_preview or ev.subagent_id
    return f'[green]●[/green] Agent [bold]"{task}"[/bold] finished · {elapsed_s:.0f}s'


def compacted(ev: events.Compacted) -> str:
    return f"[dim]⏺ {ev.note}[/dim]"


def memory_write(ev: events.MemoryWrite) -> str:
    return f"  [blue]◆[/blue] [dim]memory: {ev.type} '{ev.title}' ({ev.scope})[/dim]"


def memory_consolidated(ev: events.MemoryConsolidated) -> str:
    return f"  [blue]◆[/blue] [dim]memory: {ev.summary}[/dim]"


def error(ev: events.Error) -> str:
    first_line = ev.message.splitlines()[0] if ev.message else ""
    return f"[red]✗[/red] error  {first_line}"
