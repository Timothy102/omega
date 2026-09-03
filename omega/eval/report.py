import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .manifest import ContextManifest


@dataclass(frozen=True)
class RunResult:
    task: str
    model: str
    repeat: int
    passed: bool
    turns: int
    tool_calls: dict[str, int]
    tokens_in: int
    tokens_out: int
    cost_usd: float | None
    wall_time_s: float
    cache_hit_ratio: float | None
    error: str | None
    check_output: str
    manifest: ContextManifest | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task, "model": self.model, "repeat": self.repeat,
            "passed": self.passed, "turns": self.turns, "tool_calls": self.tool_calls,
            "tokens_in": self.tokens_in, "tokens_out": self.tokens_out,
            "cost_usd": self.cost_usd, "wall_time_s": round(self.wall_time_s, 3),
            "cache_hit_ratio": self.cache_hit_ratio, "error": self.error,
            "check_output": self.check_output[:2000],
            "manifest": self.manifest.to_dict() if self.manifest else None,
        }


def _result_from_dict(d: dict[str, Any]) -> RunResult:
    return RunResult(
        task=d["task"], model=d["model"], repeat=d["repeat"], passed=d["passed"],
        turns=d["turns"], tool_calls=dict(d["tool_calls"]), tokens_in=d["tokens_in"],
        tokens_out=d["tokens_out"], cost_usd=d["cost_usd"], wall_time_s=d["wall_time_s"],
        cache_hit_ratio=d["cache_hit_ratio"], error=d["error"], check_output=d["check_output"],
        # `compare` only needs the summary row -- reloading a full ContextManifest
        # from JSON isn't worth the code, so the manifest stays report.json-only.
        manifest=None,
    )


@dataclass(frozen=True)
class Report:
    created: float
    results: tuple[RunResult, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {"created": self.created, "results": [r.to_dict() for r in self.results]}

    def summary_by_model(self) -> dict[str, dict[str, Any]]:
        by_model: dict[str, list[RunResult]] = {}
        for r in self.results:
            by_model.setdefault(r.model, []).append(r)
        out: dict[str, dict[str, Any]] = {}
        for model, rows in by_model.items():
            costs = [r.cost_usd for r in rows if r.cost_usd is not None]
            out[model] = {
                "runs": len(rows),
                "pass_rate": sum(r.passed for r in rows) / len(rows),
                "mean_cost_usd": (sum(costs) / len(costs)) if costs else None,
                "mean_time_s": sum(r.wall_time_s for r in rows) / len(rows),
            }
        return out


def render_table(report: Report) -> str:
    if not report.results:
        return "(no runs)"
    header = (f"{'TASK':<30}{'MODEL':<10}{'RESULT':>7}{'TURNS':>7}{'TOOLS':>7}"
             f"{'TOK IN':>9}{'TOK OUT':>9}{'$':>9}{'TIME':>8}")
    lines = [header]
    for r in report.results:
        tool_total = sum(r.tool_calls.values())
        cost = f"{r.cost_usd:.4f}" if r.cost_usd is not None else "-"
        lines.append(f"{r.task[:29]:<30}{r.model:<10}{'PASS' if r.passed else 'FAIL':>7}"
                     f"{r.turns:>7}{tool_total:>7}{r.tokens_in:>9}{r.tokens_out:>9}"
                     f"{cost:>9}{r.wall_time_s:>7.1f}s")
    lines.append("")
    for model, s in sorted(report.summary_by_model().items()):
        cost = f"${s['mean_cost_usd']:.4f}" if s["mean_cost_usd"] is not None else "$-"
        lines.append(f"{model}: {s['pass_rate'] * 100:.0f}% pass · {cost} mean · "
                     f"{s['mean_time_s']:.1f}s mean · {s['runs']} run(s)")
    return "\n".join(lines)


def compare(a: Report, b: Report) -> str:
    def key(r: RunResult) -> tuple[str, str]:
        return (r.task, r.model)

    a_by = {key(r): r for r in a.results}
    b_by = {key(r): r for r in b.results}
    lines = [f"{'TASK':<30}{'MODEL':<10}{'PASS':>8}{'$ delta':>12}{'TIME delta':>14}"]
    for k in sorted(set(a_by) | set(b_by)):
        task, model = k
        ra, rb = a_by.get(k), b_by.get(k)
        if ra is None:
            pass_change = "new(fail)" if not (rb and rb.passed) else "new(pass)"
        elif rb is None:
            pass_change = "gone"
        elif ra.passed == rb.passed:
            pass_change = "pass" if rb.passed else "fail"
        else:
            pass_change = "fixed" if rb.passed else "REGRESSED"
        cost_delta = ("-" if not (ra and rb and ra.cost_usd is not None and rb.cost_usd is not None)
                     else f"{rb.cost_usd - ra.cost_usd:+.4f}")
        time_delta = "-" if not (ra and rb) else f"{rb.wall_time_s - ra.wall_time_s:+.1f}s"
        lines.append(f"{task[:29]:<30}{model:<10}{pass_change:>8}{cost_delta:>12}{time_delta:>14}")
    return "\n".join(lines)


def write_report(report: Report, run_dir: Path) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "report.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(report.to_dict(), indent=1))
    tmp.replace(path)
    return path


def load_report(path: Path) -> Report:
    raw = json.loads(path.read_text())
    return Report(created=raw["created"], results=tuple(_result_from_dict(r) for r in raw["results"]))


def new_run_dir(runs_root: Path) -> Path:
    return runs_root / time.strftime("%Y%m%d-%H%M%S")
