from pathlib import Path

from omega.eval import report


def _mk(task: str, model: str, passed: bool, cost: float | None, wall_time: float) -> report.RunResult:
    return report.RunResult(task=task, model=model, repeat=0, passed=passed, turns=3,
                            tool_calls={"read": 2, "bash": 1}, tokens_in=1000, tokens_out=200,
                            cost_usd=cost, wall_time_s=wall_time, cache_hit_ratio=0.5,
                            error=None, check_output="ok", manifest=None)


def test_render_table_includes_tasks_and_pass_fail():
    r = report.Report(created=0.0, results=(
        _mk("t1", "opus", True, 0.01, 12.3),
        _mk("t1", "sonnet", False, 0.02, 8.1),
    ))
    text = report.render_table(r)
    assert "t1" in text and "opus" in text and "sonnet" in text
    assert "PASS" in text and "FAIL" in text


def test_render_table_empty_report():
    assert report.render_table(report.Report(created=0.0)) == "(no runs)"


def test_summary_by_model_pass_rate_and_means():
    r = report.Report(created=0.0, results=(
        _mk("t1", "opus", True, 0.01, 1.0),
        _mk("t2", "opus", False, 0.03, 3.0),
    ))
    s = r.summary_by_model()
    assert s["opus"]["runs"] == 2
    assert s["opus"]["pass_rate"] == 0.5
    assert s["opus"]["mean_cost_usd"] == 0.02
    assert s["opus"]["mean_time_s"] == 2.0


def test_summary_by_model_handles_null_costs():
    r = report.Report(created=0.0, results=(_mk("t1", "glm2", True, None, 1.0),))
    s = r.summary_by_model()
    assert s["glm2"]["mean_cost_usd"] is None


def test_write_and_load_report_roundtrip(tmp_path: Path):
    r = report.Report(created=123.0, results=(_mk("t1", "opus", True, 0.01, 1.5),))
    out = report.write_report(r, tmp_path / "run1")
    assert out.exists() and out.name == "report.json"
    loaded = report.load_report(out)
    assert loaded.created == 123.0
    assert loaded.results[0].task == "t1"
    assert loaded.results[0].cost_usd == 0.01
    assert loaded.results[0].tool_calls == {"read": 2, "bash": 1}


def test_compare_flags_regression_and_fix():
    a = report.Report(created=0.0, results=(
        _mk("t1", "opus", True, 0.01, 1.0),
        _mk("t2", "opus", False, 0.01, 1.0),
    ))
    b = report.Report(created=0.0, results=(
        _mk("t1", "opus", False, 0.01, 1.0),
        _mk("t2", "opus", True, 0.01, 1.0),
    ))
    text = report.compare(a, b)
    assert "REGRESSED" in text
    assert "fixed" in text


def test_new_run_dir_is_timestamped(tmp_path: Path):
    d = report.new_run_dir(tmp_path)
    assert d.parent == tmp_path
    assert len(d.name) >= 8
