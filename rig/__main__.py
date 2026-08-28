import asyncio, sys
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from rich.console import Console

from . import config, loop, mcp, permissions, session, subagent, tools

console = Console()
BANNER = "[dim]rig — /plan to plan, /build to execute, ctrl-d to exit[/dim]"

HISTORY = Path.home() / ".rig" / "history"


def bindings() -> KeyBindings:
    kb = KeyBindings()

    def kill_line(event):
        buf = event.current_buffer
        start = buf.document.get_start_of_line_position()
        if start:
            buf.delete_before_cursor(-start)
        else:
            buf.delete(len(buf.document.text_after_cursor))

    # c-u is the portable one; the escapes are what iTerm2/Ghostty emit when
    # cmd+delete is mapped through to the terminal.
    for key in ("c-u", "escape,backspace", "escape,c-h"):
        kb.add(*key.split(","))(kill_line)
    return kb


def prompt_session() -> PromptSession:
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    return PromptSession(history=FileHistory(str(HISTORY)),
                         key_bindings=bindings())


async def main():
    argv = sys.argv[1:]
    # Dispatch subcommands BEFORE config.load(): it exits when no API key is
    # set, which made `rig setup` -- the flow that sets the key -- unreachable.
    if argv and argv[0] == "setup":
        from .setup_server import serve
        return serve()
    if argv and argv[0] == "sessions":
        # `sessions` lists and exits; silently swallowing further flags made
        # `rig sessions --resume X` look like it had done something.
        extra = [a for a in argv[1:] if a != "--"]
        if extra:
            console.print(f"[yellow]note:[/yellow] `sessions` only lists — "
                          f"ignoring {' '.join(extra)}")
            if "--resume" in extra:
                i = extra.index("--resume")
                sid = extra[i + 1] if i + 1 < len(extra) else "<id>"
                console.print(f"      to open it:  [bold]rig --resume {sid}[/bold]")
        return console.print(session.render_list())

    # Parse every flag up front so order never matters.
    flags = {a for a in argv if a.startswith("-")}
    use_mcp, yolo = "--mcp" in flags, "--yolo" in flags
    want_plan = bool(flags & {"--plan", "-p"})
    want_continue = bool(flags & {"--continue", "-c"})
    resume_id = None
    if "--resume" in argv:
        i = argv.index("--resume")
        if i + 1 >= len(argv):
            return console.print("[red]--resume needs a session id[/red] "
                                 "(see `rig sessions`)")
        resume_id = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    argv = [a for a in argv
            if a not in ("--mcp", "--yolo", "--plan", "-p", "--continue", "-c")]

    cfg = config.load()
    subagent.CFG = cfg
    if not yolo and sys.stdin.isatty():
        tools.CONFIRM = confirm

    sess = None
    if want_continue:
        sess = session.latest()
        if sess is None:
            return console.print("[dim]no session for this directory[/dim]")
    elif resume_id:
        sess = session.load(resume_id)
    if sess:
        console.print(f"[dim]resumed {sess.id} — {sess.turns} turns, "
                      f"{len(sess.history)} messages · {sess.cwd}[/dim]")

    # An explicit --plan must win over the stored mode: silently ignoring a
    # read-only flag is a safety bug, not a papercut.
    mode = "plan" if want_plan else (sess.mode if sess else "build")

    if use_mcp:
        with console.status("[dim]connecting MCP servers…[/dim]"):
            for name, status in (await mcp.load(only=set(config.mcp_names()))).items():
                console.print(f"[dim]  {name}: {status}[/dim]")

    if sess is None:
        sess = session.Session.new(mode=mode)
    history = sess.history

    prompt = " ".join(argv).strip()
    if prompt:
        await one_shot(cfg, history, prompt, mode, sess)
        return

    console.print(BANNER)
    ps = prompt_session()
    while True:
        try:
            line = (await ps.prompt_async(f"{mode}› ")).strip()
        except KeyboardInterrupt:
            continue
        except EOFError:
            console.print()
            return
        if line in ("/plan", "/build"):
            mode = line[1:]
            console.print(f"[dim]mode: {mode}[/dim]")
            continue
        if line:
            await one_shot(cfg, history, line, mode, sess)


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


async def one_shot(cfg, history, prompt, mode, sess=None):
    history.append({"role": "user", "content": prompt})

    def on_text(delta):
        console.print(delta, end="", markup=False, highlight=False)

    def on_compact(note):
        if sess:
            sess.compactions += 1
        console.print(f"\n[dim]⏺ {note}[/dim]", highlight=False)

    def on_tool(call):
        detail = ""
        try:
            args = call.args()
            detail = str(args.get("path") or args.get("pattern")
                         or args.get("command") or args.get("task") or "")[:60]
        except Exception:
            pass
        console.print(f"\n[dim]⏺ {call.name}[/dim] [dim italic]{detail}[/dim italic]",
                      highlight=False)

    interrupted = False
    try:
        await loop.run_turn(cfg, history, mode=mode, on_text=on_text,
                            on_tool=on_tool, on_compact=on_compact)
    except KeyboardInterrupt:
        # BaseException, so a bare `except Exception` misses it entirely and the
        # process dies before the session is ever written.
        interrupted = True
        console.print("\n[dim]interrupted[/dim]")
    except Exception as e:
        console.print(f"\n[red]error:[/red] {type(e).__name__}: {e}")
    finally:
        if sess:
            sess.mode = mode
            session.repair(history)
            if interrupted:
                history.append({"role": "user",
                                "content": "[previous turn interrupted by user]"})
            try:
                sess.save()
            except Exception as e:
                console.print(f"[red]could not save session:[/red] {e}")
    console.print()


async def _main_guarded():
    try:
        await main()
    finally:
        await mcp.shutdown()


def cli():
    try:
        asyncio.run(_main_guarded())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    cli()
