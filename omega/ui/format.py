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
import subprocess
from pathlib import Path
from typing import Any

from rich.markup import escape

from .. import events

# A plain assignment, not `import escape as esc` -- mypy strict's
# no-implicit-reexport rule hides a renamed import from other modules unless
# it's re-exported via `__all__`, but a module-level name binding like this
# one is an ordinary public attribute. `format.esc` is used by every module
# that renders model/tool/user-derived text into rich markup (`transcript.py`,
# `sidebar.py`, `plain.py`) -- a literal "[" in a bash command, a commit
# subject, or a provider error message must never be parsed as a style tag
# (that raised `MarkupError` and aborted the turn before this was added).
esc = escape

# ---- paths ---------------------------------------------------------------


# Worktrees of the cwd's repo, cached for the life of the process (like
# `app.py`'s `_branch_cache`) -- `git worktree list` only ever changes between
# turns, so re-running it on every path we render would be wasteful. Keyed by
# the cwd it was discovered from, sorted longest-path-first so a nested match
# resolves to the most specific worktree.
_worktree_cache: dict[str, list[tuple[Path, str]]] = {}


def _discover_worktrees(cwd: str) -> list[tuple[Path, str]]:
    if cwd in _worktree_cache:
        return _worktree_cache[cwd]
    found: list[tuple[Path, str]] = []
    try:
        proc = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=cwd, capture_output=True, text=True, timeout=2)
        if proc.returncode == 0:
            for line in proc.stdout.splitlines():
                if line.startswith("worktree "):
                    wt_path = Path(line[len("worktree "):]).resolve()
                    found.append((wt_path, wt_path.name))
    except (OSError, subprocess.SubprocessError):
        pass
    found.sort(key=lambda t: len(str(t[0])), reverse=True)
    _worktree_cache[cwd] = found
    return found


def _worktree_relpath(abs_path: Path) -> str | None:
    """`⎇ <worktree-name>/<path-within-worktree>` when `abs_path` lives inside
    one of the cwd repo's git worktrees, else `None`."""
    for wt_path, name in _discover_worktrees(os.getcwd()):
        try:
            rel = abs_path.relative_to(wt_path)
        except ValueError:
            continue
        return f"⎇ {name}" if str(rel) == "." else f"⎇ {name}/{rel}"
    return None


def _tail_segments(path: str, segments: int = 3) -> str:
    parts = [seg for seg in path.split("/") if seg]
    if len(parts) <= segments:
        return "/".join(parts) if parts else path
    return ".../" + "/".join(parts[-segments:])


def relpath(path: str) -> str:
    """`path` relative to the cwd; when it's outside the cwd tree, rendered as
    `⎇ <worktree-name>/<path-within-worktree>` if it lives in a sibling git
    worktree of the cwd's repo, else abbreviated to its last 3 segments -- an
    absolute path is mostly noise once you know where you are, and a bare
    `~`-abbreviated absolute path was unreadable for a worktree checkout many
    directories deep."""
    if not path:
        return path
    try:
        p = Path(path).expanduser()
        abs_p = p if p.is_absolute() else Path(os.getcwd()) / p
        rel = os.path.relpath(abs_p, os.getcwd())
        if not rel.startswith(".."):
            return rel
    except (ValueError, OSError):
        return path
    worktree = _worktree_relpath(abs_p)
    if worktree is not None:
        return worktree
    return _tail_segments(str(abs_p))


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


def truncate_right(text: str, width: int) -> str:
    """Cut from the end, keeping the recognizable start -- for chrome lines
    (header, status) where the most useful information (cwd, mode) comes
    first and a lost tail is more acceptable than a lost head."""
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    if width == 1:
        return "…"
    return text[: width - 1] + "…"


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


# One mark for every call, coloured by category. Shape carries the row's
# STATE (ran / failed) and colour carries its kind -- splitting the two that
# way keeps a burst of rows reading as one column of calls rather than a row
# of unrelated symbols, and leaves `✕` unambiguous when it appears.
CALL_GLYPH = "⏺"
ERROR_GLYPH = "✕"


def glyph_for(name: str, *, failed: bool = False) -> str:
    """The row's gutter mark. A failed call keeps its category colour but
    swaps to `✕` -- failure is the one thing that must be readable from
    across the room, and shape carries further than a red that a dim
    terminal theme may barely differentiate from the surrounding text."""
    return ERROR_GLYPH if failed else CALL_GLYPH


def display_name(name: str) -> str:
    """`call_tool` -> `CallTool`. A capitalised name reads as the subject of
    the row rather than as part of the argument text beside it, which a
    lowercase name padded into a fixed column never quite did."""
    return "".join(part.capitalize() for part in name.split("_"))


# ---- per-tool call descriptions (C9a) --------------------------------------

# Matches the `cd <dir> && ` a model prepends to keep a worktree's shell state
# -- `describe_call` collapses this boilerplate down to `(in ⎇ name)` when
# `<dir>` is a git worktree, so a burst of commands in the same worktree reads
# as the actual command instead of a repeated absolute path.
_BASH_CD_PREFIX_RE = re.compile(r"^cd\s+(\S+)\s+&&\s+")

# Generous next to the one-line budget `tool_start` then applies -- the cap
# here only stops a pathological heredoc from reaching the renderer, while
# how much actually fits is decided against the real pane width downstream.
_BASH_PREVIEW_CHARS = 400


def _cd_tag(target: Path) -> str:
    """A short name for a `cd`-ed directory: nothing when it is just the cwd,
    the worktree marker when it is one, else the last path segment."""
    worktree = _worktree_relpath(target)
    if worktree is not None:
        return worktree
    try:
        if target.resolve() == Path(os.getcwd()).resolve():
            return ""
    except OSError:
        pass
    return target.name or str(target)


def _bash_command(raw: str) -> str:
    """The command, with the model's `cd <path> && ` preamble moved to a
    trailing `(in <dir>)` tag.

    Models prefix nearly every call with a `cd` to pin the shell's cwd. Left
    in place it spends most of the row on an absolute path the reader already
    knows -- and since the row is then cut to fit, the cut lands exactly where
    the command starts, which is the only part that differs between two rows.
    Leading with the command puts the distinguishing text where the eye
    already is."""
    command = raw.replace("\n", "⏎")
    match = _BASH_CD_PREFIX_RE.match(command)
    if match is None:
        return _truncate(command, _BASH_PREVIEW_CHARS)
    target = Path(match.group(1)).expanduser()
    if not target.is_absolute():
        target = Path(os.getcwd()) / target
    rest = _truncate(command[match.end():], _BASH_PREVIEW_CHARS)
    tag = _cd_tag(target)
    return f"{rest}  (in {tag})" if tag else rest


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
        return f"bash  $ {_bash_command(str(get('command') or ''))}"
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
# Strips the `<untrusted source="...">...</untrusted>` wrapper (mcp.py) from
# an error PREVIEW only -- the model still sees the full wrapped text, this
# just keeps the wrapper's own markup out of the human-facing "→ error: …"
# line, which was otherwise eating most of an already-short truncation budget.
_UNTRUSTED_WRAP_RE = re.compile(r'<untrusted source="[^"]*">\s*|\s*</untrusted>\s*$')


_WROTE_RE = re.compile(r"wrote (\d+) lines")


def _plural(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _written_summary(text: str) -> str:
    m = _WROTE_RE.search(text)
    return f"Wrote {_plural(int(m.group(1)), 'line')}" if m else "Wrote file"


def diff_counts(text: str) -> tuple[int, int]:
    """`(additions, removals)` in a unified diff, ignoring its `---`/`+++`
    file headers -- which start with the same characters as the content lines
    and would otherwise each count as a change."""
    added = removed = 0
    for line in text.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
    return added, removed


def diff_style(line: str) -> str:
    """The colour for one line of a rendered diff. Only ever applied to a
    tool whose result IS a diff -- a grep hit beginning with `-` is ordinary
    text, and colouring it as a removal would be a lie about the file."""
    if line.startswith("+"):
        return "green"
    if line.startswith("-"):
        return "red"
    if line.startswith("@@"):
        return "cyan"
    return "dim"


# Tools whose result is a unified diff, so its lines take diff colouring.
DIFF_RESULT_TOOLS = {"edit"}


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
        detail = _UNTRUSTED_WRAP_RE.sub("", stripped[len('error:'):].strip()).strip()
        # Truncated at the offload threshold, not the ~200 chars the TUI's
        # collapsed row actually shows -- the extra length is what makes the
        # row's expand-on-enter affordance have something real to reveal.
        return f"→ error: {_truncate(detail, 4000)}"

    parts: list[str] = []
    no_hits = stripped in ("(no matches)", "")
    if name == "read":
        parts.append(f"Read {len(text.splitlines())} lines")
    elif name == "grep":
        parts.append("No matches" if no_hits else f"Found {len(text.splitlines())} matches")
    elif name == "glob":
        parts.append("No files" if no_hits else f"Found {len(text.splitlines())} files")
    elif name == "edit":
        added, removed = diff_counts(text)
        parts.append(f"Updated with {_plural(added, 'addition')} "
                     f"and {_plural(removed, 'removal')}")
    elif name == "write":
        parts.append(_written_summary(stripped))
    elif name == "bash":
        m = _EXIT_RE.search(text)
        if m and m.group(1) != "0":
            parts.append(f"Exit {m.group(1)}")
    elif name == "find_tools":
        if stripped.startswith("no tools matched") or not stripped:
            parts.append("No match")
        else:
            count = stripped.count("\n\n") + 1
            parts.append(f"Found {count} tool{'' if count == 1 else 's'}")
    elif name == "recall":
        if stripped == "(no matching memories)" or not stripped:
            parts.append("None")
        else:
            count = stripped.count("\n\n") + 1
            parts.append(f"Found {count} memor{'y' if count == 1 else 'ies'}")
    elif name == "call_tool" and not offloaded:
        parts.append(f"{fmt_num(result_chars)} chars")

    if duration_s >= 1:
        parts.append(f"{duration_s:.1f}s")
    if offloaded:
        parts.append(f"{fmt_num(result_chars)} chars · artifact {artifact_id}")

    return "→ " + " · ".join(parts) if parts else ""


# ---- result previews --------------------------------------------------------

# How many lines of a tool's OWN output are worth showing under its row
# before the reader is better served by expanding it. Budgets differ because
# the tools differ: a bash run's first lines are usually the whole answer, a
# grep's are the matches themselves, while `read`'s are the top of a file the
# reader already chose and can see in their editor -- its row's path and line
# count say everything a preview would.
PREVIEW_BUDGET = {
    "bash": 6, "grep": 5, "glob": 5, "call_tool": 5,
    "recall": 3, "find_tools": 3,
    "edit": 12,          # the diff -- the whole point of showing the row
    "read": 0, "write": 0,
}
PREVIEW_DEFAULT = 3
# What an expanded block shows -- bounded, because `result_preview` can hold
# several thousand characters and a block that outgrows the pane is no more
# readable than one that shows nothing.
PREVIEW_EXPANDED = 40

# Footers the harness itself appends to a result. They are already stated in
# the outcome line (`exit 1`, `12.4k chars · artifact a3`), so repeating them
# inside the preview spends the budget restating what is one line below.
_FOOTER_RE = re.compile(r"^\s*\[(?:exit \d+|full output:.*|truncated.*|stderr)\]\s*$")
_NOISE_PREVIEWS = {"(no matches)", "(no output, exit 0)", "(no matching memories)"}


def preview_lines(name: str, text: str, *, limit: int | None = None,
                  width: int | None = None) -> list[str]:
    """The first few real lines of a tool's own output, for display beneath
    its call row.

    A count alone (`60 lines`, `12 matches`) says a call happened but not
    what it found, which is the one thing the reader is actually watching
    for. This returns the evidence itself -- already trimmed of the harness's
    own footers, blank runs, and anything past `width` -- so the common case
    needs no expansion at all."""
    if limit is None:
        limit = PREVIEW_BUDGET.get(name, PREVIEW_DEFAULT)
    if limit <= 0:
        return []
    stripped = text.strip()
    if not stripped or stripped in _NOISE_PREVIEWS or stripped.startswith("error:"):
        return []
    # A diff result opens with the tool's own `edited <path>` acknowledgement,
    # which the call row above already says. Keeping only real diff lines
    # drops it without having to special-case its wording.
    is_diff = name in DIFF_RESULT_TOOLS
    out: list[str] = []
    for raw in stripped.splitlines():
        line = raw.rstrip()
        if not line.strip() or _FOOTER_RE.match(line):
            continue
        if is_diff and not line.startswith(("+", "-", "@@", " ")):
            continue
        # Tabs render at whatever width the terminal chose, which breaks the
        # rail's alignment; expand them here so every preview line starts
        # exactly where the one above it did.
        line = line.expandtabs(4)
        if width is not None:
            line = truncate_right(line, width)
        out.append(line)
        if len(out) >= limit:
            break
    return out


def preview_hidden(name: str, text: str, shown: int) -> int:
    """How many more non-empty lines the full result holds beyond `shown` --
    the number the collapsed block advertises as worth expanding for."""
    stripped = text.strip()
    if not stripped or stripped in _NOISE_PREVIEWS:
        return 0
    total = sum(1 for line in stripped.splitlines()
                if line.strip() and not _FOOTER_RE.match(line.rstrip()))
    return max(0, total - shown)


# ---- transcript one-liners --------------------------------------------------

# A bash command is the one detail worth more than a single row: it is what
# the reader is deciding about, and cutting it to one line reliably cuts it
# mid-path. Three lines' worth of budget, still middle-truncated so both the
# command's head and its (usually more specific) tail survive.
_WIDTH_BUDGET = {"bash": 3}


def call_args(name: str, args_preview: str) -> str:
    """The parenthesised argument text for a call row. `describe_call` writes
    for a padded-column layout -- double spaces separating fields, a leading
    `$` marking bash's shell text -- both of which are chrome that `Name(...)`
    supplies on its own."""
    detail = _without_name(name, args_preview).strip()
    if name == "bash":
        detail = detail.removeprefix("$ ")
    return re.sub(r"\s{2,}", " ", detail)


def tool_start(ev: events.ToolStart, *, show_subagent_suffix: bool = True,
               width: int | None = None, failed: bool = False) -> str:
    style = style_for(ev.name)
    detail = call_args(ev.name, ev.args_preview)
    label = display_name(ev.name)
    if width is not None:
        width *= _WIDTH_BUDGET.get(ev.name, 1)
        # The name and its brackets are chrome the argument text must fit
        # around, not extra room it can spend.
        width = max(8, width - len(label) - 2)
    if ev.subagent_id:
        # The tier/id suffix is appended AFTER truncation, not before it --
        # its own length must come out of `width`'s budget first, or a
        # subagent row with the suffix shown overflows past the caller's
        # intended width by however many columns the suffix takes.
        suffix = f"  ({esc(ev.tier or '')}·{esc(ev.subagent_id)})" if show_subagent_suffix else ""
        if width is not None:
            detail = truncate_middle(detail, max(0, width - len(suffix)))
        detail = esc(detail)
        # `└` stays: under a subagent it marks NESTING, which the call glyph
        # does not replace -- the glyph follows it so a nested row still says
        # both where it sits and what it is.
        mark = glyph_for(ev.name, failed=failed)
        return (f"  [dim]└ {mark} [{style}]{label}[/{style}]"
                f"({detail}){suffix}[/dim]")
    if width is not None:
        detail = truncate_middle(detail, width)
    detail = esc(detail)
    mark = glyph_for(ev.name, failed=failed)
    mark_style = "bold red" if failed else style
    return (f"[{mark_style}]{mark}[/{mark_style}] [bold {style}]{label}[/bold {style}]"
            f"[dim]([/dim]{detail}[dim])[/dim]")


def tool_end(ev: events.ToolEnd) -> str | None:
    if ev.offloaded:
        text = ev.outcome or f"offloaded → artifact {ev.artifact_id}"
        return f"  [dim]↳ {esc(text)}[/dim]"
    if ev.outcome.startswith("→ error") or (ev.name == "bash" and "exit" in ev.outcome):
        return f"  [dim]{esc(ev.outcome)}[/dim]"
    if ev.name in ("find_tools", "call_tool", "recall") and ev.outcome:
        return f"  [dim]{esc(ev.outcome)}[/dim]"
    return None


def subagent_spawned(ev: events.SubagentSpawned, *, show_id: bool = False) -> str:
    id_suffix = f"  [dim]{esc(ev.subagent_id)}[/dim]" if show_id else ""
    return (f"[green]●[/green] [bold green]subagent[/bold green]([green]{esc(ev.tier)}[/green]) "
           f"{esc(ev.task_preview)}{id_suffix}")


def subagent_done(ev: events.SubagentDone, task_preview: str = "", elapsed_s: float = 0.0) -> str:
    task = esc(task_preview or ev.subagent_id)
    return f'[green]●[/green] Agent [bold]"{task}"[/bold] finished · {elapsed_s:.0f}s'


def compacted(ev: events.Compacted) -> str:
    return f"[dim]⏺ {esc(ev.note)}[/dim]"


def memory_write(ev: events.MemoryWrite) -> str:
    return (f"  [blue]◆[/blue] [dim]memory: {esc(ev.type)} '{esc(ev.title)}' "
           f"({esc(ev.scope)})[/dim]")


def memory_consolidated(ev: events.MemoryConsolidated) -> str:
    return f"  [blue]◆[/blue] [dim]memory: {esc(ev.summary)}[/dim]"


def error(ev: events.Error) -> str:
    first_line = ev.message.splitlines()[0] if ev.message else ""
    return f"[red]✗[/red] error  {esc(first_line)}"


# ---- B1's edit-safety events (Checkpoint/Verified/JobStarted/JobFinished) --
# These dataclasses live in `events.py`, which B1 owns and may not have
# landed yet -- callers duck-type on the attributes below (matching the
# signatures agreed in the plan) rather than importing the classes.


def checkpoint(ev: Any) -> str:
    return "[dim]⎘ checkpoint[/dim]"


def verified(ev: Any) -> str:
    summary = esc(str(ev.results_summary))
    if ev.ok:
        return f"[dim]✓ verified: {summary}[/dim]"
    return f"[red]✗ verification failed: {summary}[/red]"


def job_started(ev: Any) -> str:
    return f"[dim]⟳ job {esc(str(ev.id))} started[/dim]"


def job_finished(ev: Any) -> str:
    job_id = esc(str(ev.id))
    if ev.exit_code == 0:
        return f"[dim]✓ job {job_id} finished (exit 0)[/dim]"
    return f"[dim]✗ job {job_id} finished (exit {ev.exit_code})[/dim]"
