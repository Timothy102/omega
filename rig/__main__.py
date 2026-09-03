import asyncio
import sys
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings

from . import config, mcp, session, subagent, tools
from .memory import consolidate
from .ui import plain

console = plain.console
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
    if argv and argv[0] == "memory":
        # gc needs config.load(), unlike setup/sessions above -- keep it local
        # to this branch instead of moving it above the flag-parsing section.
        cfg = config.load()
        if len(argv) > 1 and argv[1] == "gc":
            console.print(await consolidate.run(cfg, "project", force=True))
            console.print(await consolidate.run(cfg, "global", force=True))
        else:
            console.print("usage: rig memory gc")
        return

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
        tools.CONFIRM = plain.confirm
        tools.ASK_USER = plain.ask_user

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
    tools.SESSION_ID = sess.id
    history = sess.history
    if history:
        # Without this the model has no signal it is mid-conversation and can
        # mistake a resumed session for a cold start.
        history.append({"role": "user", "content":
                        f"[session resumed — the {len(history)} messages above "
                        f"are our earlier conversation and are available to you]"})

    prompt = " ".join(argv).strip()
    if prompt:
        await plain.run_prompt(cfg, history, prompt, mode, sess)
        await _consolidate_on_close(cfg)
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
            await _consolidate_on_close(cfg)
            return
        if line in ("/plan", "/build"):
            mode = line[1:]
            console.print(f"[dim]mode: {mode}[/dim]")
            continue
        if line == "/memory-gc":
            console.print(f"[dim]{await consolidate.run(cfg, 'project', force=True)}[/dim]")
            console.print(f"[dim]{await consolidate.run(cfg, 'global', force=True)}[/dim]")
            continue
        if line:
            await plain.run_prompt(cfg, history, line, mode, sess)


async def _consolidate_on_close(cfg):
    # A provider error here must never block exit -- consolidation is a
    # courtesy, not a precondition for closing the session.
    try:
        for scope in ("project", "global"):
            summary = await consolidate.run(cfg, scope, force=False)
            if summary:
                console.print(f"[dim]{summary}[/dim]")
    except Exception:
        pass


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
