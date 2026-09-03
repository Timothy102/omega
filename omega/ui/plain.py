import asyncio
from typing import Any

from rich.console import Console

from .. import events, loop, permissions, trace
from ..config import Config
from ..session import Message, Session
from . import format

_Checkpoint = getattr(events, "Checkpoint", None)
_Verified = getattr(events, "Verified", None)
_JobStarted = getattr(events, "JobStarted", None)
_JobFinished = getattr(events, "JobFinished", None)

console = Console()


async def confirm(name: str, args: dict[str, Any], why: str) -> bool:
    detail = str(args.get("command") or args.get("path")
                 or args.get("name") or "")[:200]
    console.print(f"\n[yellow]⏸  {name}[/yellow] [dim]{format.esc(why)}[/dim]")
    if detail:
        console.print(f"   [bold]{format.esc(detail)}[/bold]", highlight=False)
    try:
        answer = (await asyncio.to_thread(
            input, "   allow? [y]es / [N]o / [a]lways: ")).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    if answer == "a":
        permissions.remember(permissions.rule_for(name, args), permissions.ALLOW)
        return True
    return answer.startswith("y")


async def ask_user(question: str, options: list[events.Option], multi_select: bool = False) -> str:
    console.print(f"\n[yellow]?[/yellow] {format.esc(question)}", highlight=False)
    labels = [opt.get("label", "") for opt in options]
    for i, opt in enumerate(options, 1):
        desc = opt.get("description", "")
        line = f"   {i}. {format.esc(opt.get('label', ''))}" + (f" — {format.esc(desc)}" if desc else "")
        console.print(line, highlight=False)
    prompt = ("   answer (comma-separated numbers): " if multi_select
              else "   answer: ")
    try:
        answer = (await asyncio.to_thread(input, prompt)).strip()
    except (EOFError, KeyboardInterrupt):
        return "(no answer)"
    if not answer:
        return "(no answer)"

    def resolve(token: str) -> str:
        token = token.strip()
        if token.isdigit() and 1 <= int(token) <= len(labels):
            return labels[int(token) - 1]
        return token

    if multi_select:
        return ", ".join(resolve(t) for t in answer.split(",") if t.strip())
    return resolve(answer)


def render(ev: events.Event) -> None:
    match ev:
        case events.TextDelta(text=text):
            console.print(text, end="", markup=False, highlight=False)
        case events.ToolStart():
            console.print("\n" + format.tool_start(ev), highlight=False)
        case events.ToolEnd():
            end_text = format.tool_end(ev)
            if end_text is not None:
                console.print(end_text, highlight=False)
        case events.SubagentSpawned():
            console.print("\n" + format.subagent_spawned(ev), highlight=False)
        case events.SubagentDone():
            console.print(format.subagent_done(ev), highlight=False)
        case events.Compacted():
            console.print("\n" + format.compacted(ev), highlight=False)
        case events.MemoryWrite():
            console.print(format.memory_write(ev), highlight=False)
        case events.MemoryConsolidated():
            console.print(format.memory_consolidated(ev), highlight=False)
        case events.Error():
            console.print("\n" + format.error(ev), highlight=False)
        case events.Done():
            pass
        case events.Usage():
            pass
        case events.ModelUsed():
            pass
        case _ if _Checkpoint is not None and isinstance(ev, _Checkpoint):
            console.print(format.checkpoint(ev), highlight=False)
        case _ if _Verified is not None and isinstance(ev, _Verified):
            console.print(format.verified(ev), highlight=False)
        case _ if _JobStarted is not None and isinstance(ev, _JobStarted):
            console.print(format.job_started(ev), highlight=False)
        case _ if _JobFinished is not None and isinstance(ev, _JobFinished):
            console.print(format.job_finished(ev), highlight=False)


async def run_prompt(cfg: Config, history: list[Message], prompt: str, mode: str,
                     sess: Session | None = None, model: str | None = None) -> None:
    history.append({"role": "user", "content": prompt})

    def emit(ev: events.Event) -> None:
        # A rendering bug (an unescaped model string hitting rich's markup
        # parser, say) must never abort the turn the model is mid-way through
        # -- log it to the trace and keep going instead of letting it propagate.
        try:
            render(ev)
        except Exception as e:
            console.print(f"\n[yellow]⚠ render error: {type(e).__name__}: "
                          f"{format.esc(str(e)[:120])}[/yellow]", highlight=False)
        if sess:
            trace.append(sess.id, ev, sess.turns)
            if (isinstance(ev, events.Compacted)
                    and not ev.note.startswith("compaction skipped")):
                sess.compactions += 1

    interrupted = False
    try:
        await loop.run_turn(cfg, history, mode=mode, emit=emit, model=model)
    except KeyboardInterrupt:
        # BaseException, so a bare `except Exception` misses it entirely and the
        # process dies before the session is ever written.
        interrupted = True
        console.print("\n[dim]interrupted[/dim]")
    except Exception as e:
        console.print(f"\n[red]error:[/red] {type(e).__name__}: {format.esc(str(e))}")
    finally:
        if sess:
            try:
                sess.close_turn(history, mode, interrupted)
            except Exception as e:
                console.print(f"[red]could not save session:[/red] {format.esc(str(e))}")
    console.print()
