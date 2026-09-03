import asyncio
import sys

from . import config, mcp, session, subagent, tools
from .config import Config
from .memory import consolidate
from .ui import plain

console = plain.console


async def main() -> None:
    argv = sys.argv[1:]
    # Dispatch subcommands BEFORE config.load(): it exits when no API key is
    # set, which made `rig setup` -- the flow that sets the key -- unreachable.
    if argv and argv[0] == "setup":
        from .setup_server import serve
        return serve()
    if argv and argv[0] == "onboard":
        from . import onboarding
        return await onboarding.run()
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
    if argv and argv[0] == "models":
        return console.print(_render_models_table(config.load()))

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
    model_arg = None
    if "--model" in argv:
        i = argv.index("--model")
        if i + 1 >= len(argv):
            return console.print("[red]--model needs an alias or model id[/red] "
                                 "(see `rig models`)")
        model_arg = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    argv = [a for a in argv
            if a not in ("--mcp", "--yolo", "--plan", "-p", "--continue", "-c")]

    cfg = config.load()
    # First run (no config file) or an unusable `main` role: config.load()
    # itself no longer exits for a missing key (that check is lazy), so this
    # is the one place that must decide between a short interactive setup and
    # the old hard-exit -- never leave it to whatever call happens to touch
    # the key deep inside a turn.
    if not config.CONFIG_PATH.exists() or not cfg.role("main").provider.has_key:
        if yolo or not sys.stdin.isatty():
            _ = cfg.role("main").provider.api_key  # raises the helpful SystemExit
        else:
            from . import onboarding
            await onboarding.run()
            cfg = config.load()
    subagent.CFG = cfg
    # --model overrides both `main` and `plan` for this session; resolved
    # against the catalog now so a typo is reported before any turn runs.
    model_alias = cfg.resolve_alias(model_arg) if model_arg else None
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
    # Deferred: the TUI shows this in-transcript instead of on the console
    # (printed before app.run_async() would just scroll away under the TUI).
    resumed_note = None
    if sess:
        resumed_note = (f"resumed {sess.id} — {sess.turns} turns, "
                        f"{len(sess.history)} messages · {sess.cwd}")

    # An explicit --plan must win over the stored mode: silently ignoring a
    # read-only flag is a safety bug, not a papercut.
    mode = "plan" if want_plan else (sess.mode if sess else "build")

    if use_mcp:
        with console.status("[dim]connecting MCP servers…[/dim]"):
            for name, status in (await mcp.load(only=set(config.mcp_names()))).items():
                console.print(f"[dim]  {name}: {status}[/dim]")

    if sess is None:
        sess = session.Session.new(mode=mode)
    if model_alias:
        sess.model_override = model_alias
    tools.SESSION_ID = sess.id
    history = sess.history
    if history:
        # Without this the model has no signal it is mid-conversation and can
        # mistake a resumed session for a cold start.
        history.append({"role": "user", "content":
                        f"{session.RESUME_PREFIX} — the {len(history)} messages "
                        f"above are our earlier conversation and are available to you]"})

    prompt = " ".join(argv).strip()
    # A one-shot prompt always uses ui/plain.py, even from a real terminal;
    # the TUI only replaces the bare interactive REPL.
    if not prompt and sys.stdin.isatty() and sys.stdout.isatty():
        from .ui.tui import RigApp
        app = RigApp(cfg, sess, mode, history, model_alias=sess.model_override)
        if not yolo:
            tools.CONFIRM = app.confirm
            tools.ASK_USER = app.ask_user
        await app.run_async()
        await _consolidate_on_close(cfg)
        return

    if resumed_note:
        console.print(f"[dim]{resumed_note}[/dim]")

    # No prompt on argv and stdin isn't a terminal: read it from there, so
    # `echo "fix the tests" | rig` works instead of only piping into a REPL
    # that no longer exists for this invocation.
    if not prompt and not sys.stdin.isatty():
        prompt = sys.stdin.read().strip()

    if prompt:
        await plain.run_prompt(cfg, history, prompt, mode, sess, model=sess.model_override)
        await _consolidate_on_close(cfg)
        return

    console.print("[dim]rig: no prompt given and not an interactive terminal[/dim]")


def _render_models_table(cfg: Config) -> str:
    role_defaults: dict[str, list[str]] = {}
    for role_name, role in cfg.roles.items():
        if role.alias:
            role_defaults.setdefault(role.alias, []).append(role_name)

    lines = [f"{'ALIAS':<10}{'MODEL':<26}{'PROVIDER':<16}{'CONTEXT':>10}"
             f"{'EFFORT':>8}  DEFAULT FOR"]
    for alias, m in sorted(cfg.models.items()):
        roles = ", ".join(sorted(role_defaults.get(alias, [])))
        lines.append(f"{alias:<10}{m.model:<26}{m.provider:<16}{m.context:>10,}"
                     f"{m.effort or '-':>8}  {roles}")
    return "\n".join(lines)


async def _consolidate_on_close(cfg: Config) -> None:
    # A provider error here must never block exit -- consolidation is a
    # courtesy, not a precondition for closing the session.
    try:
        for scope in ("project", "global"):
            summary = await consolidate.run(cfg, scope, force=False)
            if summary:
                console.print(f"[dim]{summary}[/dim]")
    except Exception:
        pass


async def _main_guarded() -> None:
    try:
        await main()
    finally:
        await mcp.shutdown()


def cli() -> None:
    try:
        asyncio.run(_main_guarded())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    cli()
