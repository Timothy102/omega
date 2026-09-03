import json
import time
from pathlib import Path

from .. import config
from ..ui import plain
from . import runner
from .report import Report, load_report, new_run_dir, render_table, write_report
from .report import compare as compare_reports
from .tasks import TaskError, init_examples, load_tasks

console = plain.console

_USAGE = """omega eval -- headless task-suite runner

usage:
  omega eval init                    copy example tasks into .omega/evals/
  omega eval run [path] [flags]      run tasks (default: .omega/evals/*.yaml)
  omega eval compare <runA> <runB>   diff two reports (run id, dir, or report.json path)

flags for `run`:
  --models a,b,c    model aliases to run against (default: the `main` role)
  --repeat N        repeat each task N times (default 1)
  --jobs N          max concurrent runs (default 1)
  --json            print the report as JSON instead of a table"""


async def main(argv: list[str]) -> None:
    if not argv or argv[0] in ("-h", "--help"):
        return console.print(_USAGE, markup=False, highlight=False)
    sub, rest = argv[0], argv[1:]

    if sub == "init":
        dest = Path.cwd() / ".omega" / "evals"
        written = init_examples(dest)
        console.print(f"[green]wrote {len(written)} example task(s) to {dest}[/green]")
        for p in written:
            console.print(f"  {p.name}")
        return

    if sub == "run":
        return await _run(rest)

    if sub == "compare":
        return _compare(rest)

    console.print(f"[red]unknown `omega eval {sub}`[/red] -- init, run, compare")


def _parse_run_args(rest: list[str]) -> tuple[str | None, str | None, int, int, bool]:
    models_arg: str | None = None
    repeat, jobs, as_json = 1, 1, False
    positional: list[str] = []
    i = 0
    while i < len(rest):
        a = rest[i]
        if a == "--models" and i + 1 < len(rest):
            models_arg, i = rest[i + 1], i + 2
        elif a == "--repeat" and i + 1 < len(rest):
            repeat, i = int(rest[i + 1]), i + 2
        elif a == "--jobs" and i + 1 < len(rest):
            jobs, i = int(rest[i + 1]), i + 2
        elif a == "--json":
            as_json, i = True, i + 1
        else:
            positional.append(a)
            i += 1
    path = positional[0] if positional else None
    return path, models_arg, repeat, jobs, as_json


async def _run(rest: list[str]) -> None:
    path, models_arg, repeat, jobs, as_json = _parse_run_args(rest)
    try:
        tasks = load_tasks(path)
    except TaskError as e:
        return console.print(f"[red]{e}[/red]")
    if not tasks:
        return console.print("[dim]no eval tasks found -- run `omega eval init` first[/dim]")

    cfg = config.load()
    try:
        roles = runner.resolve_models(cfg, models_arg)
    except KeyError as e:
        return console.print(f"[red]{e}[/red]")

    if not as_json:
        console.print(f"[dim]running {len(tasks)} task(s) x {len(roles)} model(s) "
                      f"x {repeat} repeat(s)…[/dim]")
    results = await runner.run_suite(cfg, tasks, roles, repeat=repeat, jobs=jobs)
    report = Report(created=time.time(), results=tuple(results))

    run_dir = new_run_dir(Path.cwd() / ".omega" / "evals" / "runs")
    out_path = write_report(report, run_dir)

    if as_json:
        console.print(json.dumps(report.to_dict(), indent=1))
    else:
        console.print(render_table(report))
        console.print(f"\n[dim]report saved to {out_path}[/dim]")


def _resolve_run_path(ref: str) -> Path:
    p = Path(ref)
    if p.is_file():
        return p
    if p.is_dir():
        return p / "report.json"
    runs_root = Path.cwd() / ".omega" / "evals" / "runs"
    candidate = runs_root / ref / "report.json"
    if candidate.exists():
        return candidate
    matches = sorted(runs_root.glob(f"{ref}*/report.json")) if runs_root.exists() else []
    if matches:
        return matches[-1]
    raise TaskError(f"no report found for {ref!r}")


def _compare(rest: list[str]) -> None:
    if len(rest) < 2:
        return console.print("[red]usage: omega eval compare <runA> <runB>[/red]")
    try:
        a = load_report(_resolve_run_path(rest[0]))
        b = load_report(_resolve_run_path(rest[1]))
    except (TaskError, FileNotFoundError) as e:
        return console.print(f"[red]{e}[/red]")
    console.print(compare_reports(a, b))
