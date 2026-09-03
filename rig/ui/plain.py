import asyncio

from rich.console import Console

from .. import events, loop, permissions

console = Console()


async def confirm(name, args, why) -> bool:
    detail = str(args.get("command") or args.get("path")
                 or args.get("name") or "")[:200]
    console.print(f"\n[yellow]⏸  {name}[/yellow] [dim]{why}[/dim]")
    if detail:
        console.print(f"   [bold]{detail}[/bold]", highlight=False)
    try:
        answer = (await asyncio.to_thread(
            input, "   allow? [y]es / [N]o / [a]lways: ")).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    if answer == "a":
        permissions.remember(permissions.rule_for(name, args), permissions.ALLOW)
        return True
    return answer.startswith("y")


async def ask_user(question: str, options: list, multi_select: bool = False) -> str:
    console.print(f"\n[yellow]?[/yellow] {question}", highlight=False)
    labels = [opt.get("label", "") for opt in options]
    for i, opt in enumerate(options, 1):
        desc = opt.get("description", "")
        line = f"   {i}. {opt.get('label', '')}" + (f" — {desc}" if desc else "")
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


def render(ev: events.Event):
    match ev:
        case events.TextDelta(text=text):
            console.print(text, end="", markup=False, highlight=False)
        case events.ToolStart(name=name, args_preview=detail, subagent_id=sid, tier=tier):
            if sid:
                console.print(f"\n  [dim]⏺ {name}  {detail}  ({tier}·{sid})[/dim]",
                              highlight=False)
            else:
                console.print(f"\n[dim]⏺ {name}[/dim] [dim italic]{detail}[/dim italic]",
                              highlight=False)
        case events.ToolEnd(offloaded=True, artifact_id=artifact_id):
            console.print(f"  [dim]↳ offloaded → artifact {artifact_id}[/dim]",
                          highlight=False)
        case events.ToolEnd():
            pass
        case events.SubagentSpawned(subagent_id=sid, tier=tier, task_preview=task_preview):
            console.print(f"\n[dim]⏺ subagent({tier}) {task_preview}  [{sid}][/dim]",
                          highlight=False)
        case events.SubagentDone(subagent_id=sid):
            console.print(f"  [dim]✓ {sid} done[/dim]", highlight=False)
        case events.Compacted(note=note):
            console.print(f"\n[dim]⏺ {note}[/dim]", highlight=False)
        case events.MemoryWrite(type=type_, title=title, scope=scope):
            console.print(f"  [dim]◆ memory: {type_} '{title}' ({scope})[/dim]",
                          highlight=False)
        case events.MemoryConsolidated(summary=summary):
            console.print(f"  [dim]◆ memory: {summary}[/dim]", highlight=False)
        case events.Error(message=message):
            console.print(f"\n[red]error:[/red] {message}", highlight=False)
        case events.Done():
            pass
        case events.Usage():
            pass


async def run_prompt(cfg, history, prompt, mode, sess=None):
    history.append({"role": "user", "content": prompt})

    def emit(ev):
        render(ev)
        if (sess and isinstance(ev, events.Compacted)
                and not ev.note.startswith("compaction skipped")):
            sess.compactions += 1

    interrupted = False
    try:
        await loop.run_turn(cfg, history, mode=mode, emit=emit)
    except KeyboardInterrupt:
        # BaseException, so a bare `except Exception` misses it entirely and the
        # process dies before the session is ever written.
        interrupted = True
        console.print("\n[dim]interrupted[/dim]")
    except Exception as e:
        console.print(f"\n[red]error:[/red] {type(e).__name__}: {e}")
    finally:
        if sess:
            try:
                sess.close_turn(history, mode, interrupted)
            except Exception as e:
                console.print(f"[red]could not save session:[/red] {e}")
    console.print()
