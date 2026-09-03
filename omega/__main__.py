import asyncio
import sys
from typing import Any

from . import config, mcp, migrate, session, skills, subagent, tools
from .config import Config
from .memory import consolidate
from .ui import plain

console = plain.console


async def main() -> None:
    argv = sys.argv[1:]
    # `--help`/`-h`/`help` used to fall through every check below and run a
    # real agent turn with the literal prompt "--help" -- handle them (and
    # `--version`) before any other dispatch, config.load() included, so a
    # broken or missing config never blocks either.
    if argv and argv[0] in ("-h", "--help", "help"):
        return console.print(_usage_text(), markup=False, highlight=False)
    if argv and argv[0] == "--version":
        return console.print(f"omega {_version()}")
    # Dispatch subcommands BEFORE config.load(): it exits when no API key is
    # set, which made `omega setup` -- the flow that sets the key -- unreachable.
    if argv and argv[0] == "setup":
        from .setup_server import serve
        return serve()
    if argv and argv[0] == "onboard":
        from . import onboarding
        wrote = await onboarding.run()
        if not wrote:
            return console.print("[dim]onboarding cancelled -- no config written.[/dim]")
        return
    if argv and argv[0] == "sessions":
        # `sessions` lists and exits; silently swallowing further flags made
        # `omega sessions --resume X` look like it had done something.
        extra = [a for a in argv[1:] if a != "--"]
        if extra:
            console.print(f"[yellow]note:[/yellow] `sessions` only lists — "
                          f"ignoring {' '.join(extra)}")
            if "--resume" in extra:
                i = extra.index("--resume")
                sid = extra[i + 1] if i + 1 < len(extra) else "<id>"
                console.print(f"      to open it:  [bold]omega --resume {sid}[/bold]")
        return console.print(session.render_list())
    if argv and argv[0] == "memory":
        # gc needs config.load(), unlike setup/sessions above -- keep it local
        # to this branch instead of moving it above the flag-parsing section.
        cfg = config.load()
        if len(argv) > 1 and argv[1] == "gc":
            console.print(await consolidate.run(cfg, "project", force=True))
            console.print(await consolidate.run(cfg, "global", force=True))
        else:
            console.print("usage: omega memory gc")
        return
    if argv and argv[0] == "models":
        return console.print(_render_models_table(config.load()))
    if argv and argv[0] == "skills":
        return _render_skills(argv[1:])
    if argv and argv[0] == "connections":
        return await _connections(argv[1:])
    if argv and argv[0] == "eval":
        from .eval import cli as eval_cli
        return await eval_cli.main(argv[1:])

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
                                 "(see `omega sessions`)")
        resume_id = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    model_arg = None
    if "--model" in argv:
        i = argv.index("--model")
        if i + 1 >= len(argv):
            return console.print("[red]--model needs an alias or model id[/red] "
                                 "(see `omega models`)")
        model_arg = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    argv = [a for a in argv
            if a not in ("--mcp", "--yolo", "--plan", "-p", "--continue", "-c")]
    # Anything else that still looks like a flag at the front is a typo, not a
    # prompt -- silently absorbing it into `" ".join(argv)` is how `--help`
    # ended up being sent to the model as literal text.
    if argv and argv[0].startswith("-"):
        console.print(f"[red]omega: unknown flag {argv[0]!r}[/red]")
        return console.print(_usage_text(), markup=False, highlight=False)

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
            wrote = await onboarding.run()
            if not wrote:
                _ = cfg.role("main").provider.api_key  # raises the helpful SystemExit
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
        from .ui.tui import OmegaApp
        app = OmegaApp(cfg, sess, mode, history, model_alias=sess.model_override)
        if not yolo:
            tools.CONFIRM = app.confirm
            tools.ASK_USER = app.ask_user
        await app.run_async()
        await _consolidate_on_close(cfg)
        return

    if resumed_note:
        console.print(f"[dim]{resumed_note}[/dim]")

    # No prompt on argv and stdin isn't a terminal: read it from there, so
    # `echo "fix the tests" | omega` works instead of only piping into a REPL
    # that no longer exists for this invocation.
    if not prompt and not sys.stdin.isatty():
        prompt = sys.stdin.read().strip()

    if prompt:
        await plain.run_prompt(cfg, history, prompt, mode, sess, model=sess.model_override)
        await _consolidate_on_close(cfg)
        return

    console.print("[dim]omega: no prompt given and not an interactive terminal[/dim]")


def _version() -> str:
    import importlib.metadata
    try:
        return importlib.metadata.version("omega-code")
    except importlib.metadata.PackageNotFoundError:
        return "0.0.0-dev"


def _usage_text() -> str:
    return (
        "omega -- a fast, small coding agent for your terminal\n\n"
        "usage:\n"
        "  omega [prompt] [flags]           one-shot, or bare for the interactive TUI\n"
        "  omega <subcommand> [args]\n\n"
        "subcommands:\n"
        "  sessions                list saved sessions\n"
        "  models                   show the model catalog and role defaults\n"
        "  skills                   list available skills\n"
        "  memory gc                consolidate memory now\n"
        "  connections [...]        manage MCP servers\n"
        "  eval [...]               run the eval harness (see `omega eval --help`)\n"
        "  setup                    browser-based setup wizard\n"
        "  onboard                  terminal setup wizard\n\n"
        "flags:\n"
        "  --plan, -p               read-only planning mode\n"
        "  --model <alias>          override the model for this session\n"
        "  --continue, -c           resume this directory's last session\n"
        "  --resume <id>            resume a specific session (id prefix works)\n"
        "  --yolo                   skip permission prompts\n"
        "  --mcp                    connect all MCP servers eagerly at startup\n"
        "  --version                print the version and exit\n"
        "  -h, --help, help         show this message"
    )


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


def _render_skills(argv: list[str]) -> None:
    from rich.table import Table

    if argv and argv[0] == "show":
        if len(argv) < 2:
            return console.print("[red]usage: omega skills show <name>[/red]")
        body = skills.load_body(argv[1])
        if body is None:
            return console.print(f"[red]no skill named {argv[1]!r}[/red]")
        return console.print(body)

    table = Table(box=None)
    for col in ("NAME", "SOURCE", "DESCRIPTION"):
        table.add_column(col)
    for s in skills.catalog():
        table.add_row(s.name, s.source, s.description)
    console.print(table)


def _fmt_last_used(ts: float | None) -> str:
    if ts is None:
        return "-"
    import datetime
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def _connections_rows() -> list[tuple[str, str, str, str, str, str]]:
    """(name, state, tools, auth, source, last used), built from
    integrations.overview() -- omega's own servers plus what's importable from
    the catalog or Claude Code but isn't configured yet."""
    from . import integrations
    rows: list[tuple[str, str, str, str, str, str]] = []
    for r in integrations.overview():
        state = r["state"]
        if r["source"] == "catalog" and not r["verified"]:
            state = f"{state} (unverified)"
        rows.append((r["name"], state, str(r["tools"]) if r["state"] == "connected" else "-",
                    r["auth"] or "-", r["source"], _fmt_last_used(r["last_used"])))
    return rows


async def _connections(argv: list[str]) -> None:
    from rich.table import Table

    if not argv:
        table = Table(box=None)
        for col in ("NAME", "STATE", "TOOLS", "AUTH", "SOURCE", "LAST USED"):
            table.add_column(col)
        for row in _connections_rows():
            table.add_row(*row)
        return console.print(table)

    sub, rest = argv[0], argv[1:]

    if sub == "catalog":
        from . import integrations
        by_category: dict[str, list[Any]] = {}
        for i in integrations.CATALOG.values():
            by_category.setdefault(i.category, []).append(i)
        for category in sorted(by_category):
            console.print(f"\n[bold]{category}[/bold]")
            for i in sorted(by_category[category], key=lambda x: x.key):
                tag = "" if i.verified else " [dim]unverified[/dim]"
                console.print(f"  [bold]{i.key:<20}[/bold] {i.blurb}{tag}")
        return

    if sub == "add":
        return await _connections_add(rest)

    if sub in ("connect", "test"):
        if not rest:
            return console.print(f"[red]usage: omega connections {sub} <name>[/red]")
        name = rest[0]
        st = await mcp.connect(name)
        if sub == "test":
            await mcp.disconnect(name)
        if st.state == "needs_auth":
            console.print(f"[yellow]{name}: needs auth[/yellow] -- open {st.error} to "
                          f"authorise, then run `omega connections connect {name}` again")
        elif st.state == "connected":
            console.print(f"[green]{name}: connected[/green] ({st.tools} tools)")
        else:
            console.print(f"[red]{name}: {st.state}[/red]" + (f" -- {st.error}" if st.error else ""))
        return

    if sub in ("enable", "disable"):
        if not rest:
            return console.print(f"[red]usage: omega connections {sub} <name>[/red]")
        name = rest[0]
        try:
            await mcp.enable(name, sub == "enable")
        except KeyError:
            return console.print(f"[red]no such server {name!r}[/red] (see `omega connections`)")
        console.print(f"[green]{name}: {'enabled' if sub == 'enable' else 'disabled'}[/green]")
        return

    if sub == "remove":
        if not rest:
            return console.print("[red]usage: omega connections remove <name>[/red]")
        await mcp.remove(rest[0])
        console.print(f"[green]{rest[0]}: removed[/green]")
        return

    console.print(f"[red]unknown `omega connections {sub}`[/red] -- add, connect, enable, "
                  f"disable, remove, test, catalog")


async def _connections_add(rest: list[str]) -> None:
    import shlex

    from . import integrations

    if not rest:
        return console.print("[red]usage: omega connections add <catalog-key|name> "
                             "[--url U | --cmd \"...\"] [--env K=V ...][/red]")
    name = rest[0]
    url = cmd = None
    env: dict[str, str] = {}
    i = 1
    while i < len(rest):
        a = rest[i]
        if a == "--url" and i + 1 < len(rest):
            url, i = rest[i + 1], i + 2
        elif a == "--cmd" and i + 1 < len(rest):
            cmd, i = rest[i + 1], i + 2
        elif a == "--env" and i + 1 < len(rest):
            k, _, v = rest[i + 1].partition("=")
            env[k] = v
            i += 2
        else:
            i += 1

    catalog = integrations.CATALOG.get(name)
    spec: dict[str, Any] = {}
    if catalog is not None:
        spec["catalog"] = catalog.key
        if catalog.transport == "remote" and catalog.url:
            spec["url"] = catalog.url
        elif catalog.command:
            import os
            cmdline = [c.replace("<cwd>", os.getcwd()) for c in catalog.command]
            spec["command"], spec["args"] = cmdline[0], cmdline[1:]

    if url:
        spec["url"] = url
        spec.pop("command", None)
        spec.pop("args", None)
    if cmd:
        parts = shlex.split(cmd)
        spec["command"], spec["args"] = parts[0], parts[1:]
        spec.pop("url", None)
    if env:
        spec["env"] = env

    if not spec.get("command") and not spec.get("url"):
        return console.print("[red]give --url, --cmd, or a known catalog key[/red] "
                             "(see `omega connections catalog`)")

    mcp.add(name, spec)
    hint = ""
    if catalog and catalog.auth == "oauth":
        hint = f" -- run `omega connections connect {name}` to authorise"
    elif catalog and catalog.env and not env:
        hint = f" -- needs env: {', '.join(catalog.env)} (rerun with --env K=V)"
    console.print(f"[green]{name}: added[/green]{hint}")


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
    migrate.run()
    try:
        asyncio.run(_main_guarded())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    cli()
