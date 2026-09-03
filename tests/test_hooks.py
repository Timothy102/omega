import json

from omega import hooks
from omega.config import HookRule


def test_pre_hook_allows_when_command_exits_zero(tmp_path):
    rules = [HookRule(tools=["write"], command="true")]
    blocked, why = hooks.run_pre(rules, "write", {"path": "x.py"}, str(tmp_path))
    assert blocked is False
    assert why == ""


def test_pre_hook_blocks_when_command_exits_nonzero(tmp_path):
    rules = [HookRule(tools=["write"], command="echo bad stuff && exit 1")]
    blocked, why = hooks.run_pre(rules, "write", {"path": "x.py"}, str(tmp_path))
    assert blocked is True
    assert "bad stuff" in why


def test_pre_hook_only_applies_to_matching_tools(tmp_path):
    rules = [HookRule(tools=["edit"], command="exit 1")]
    blocked, _ = hooks.run_pre(rules, "write", {"path": "x.py"}, str(tmp_path))
    assert blocked is False


def test_pre_hook_receives_tool_env_vars(tmp_path):
    marker = tmp_path / "seen.json"
    rules = [HookRule(tools=["write"],
                      command=f"env | grep ^OMEGA_ > {marker}")]
    hooks.run_pre(rules, "write", {"path": "x.py", "content": "hi"}, str(tmp_path))
    seen = marker.read_text()
    assert "OMEGA_TOOL=write" in seen
    assert "OMEGA_CWD=" in seen
    assert "OMEGA_ARGS_JSON=" in seen


def test_pre_hook_args_json_round_trips(tmp_path):
    out = tmp_path / "args.txt"
    rules = [HookRule(tools=["write"], command=f'echo "$OMEGA_ARGS_JSON" > {out}')]
    hooks.run_pre(rules, "write", {"path": "x.py", "content": "hi"}, str(tmp_path))
    assert json.loads(out.read_text()) == {"path": "x.py", "content": "hi"}


def test_pre_hook_failure_to_run_never_raises(tmp_path, monkeypatch):
    import subprocess

    def boom(*a, **k):
        raise OSError("no such command")
    monkeypatch.setattr(subprocess, "run", boom)

    rules = [HookRule(tools=["write"], command="true")]
    blocked, why = hooks.run_pre(rules, "write", {}, str(tmp_path))
    assert blocked is False
    assert why == ""


def test_post_hook_appends_stdout_to_result(tmp_path):
    rules = [HookRule(tools=["write"], command="echo formatted")]
    appended = hooks.run_post(rules, "write", {"path": "x.py"}, str(tmp_path), "wrote 5 chars")
    assert appended == "[hook echo] formatted"


def test_post_hook_receives_result_env_var(tmp_path):
    marker = tmp_path / "result.txt"
    rules = [HookRule(tools=["write"], command=f'echo "$OMEGA_RESULT" > {marker}')]
    hooks.run_post(rules, "write", {"path": "x.py"}, str(tmp_path), "wrote 5 chars")
    assert marker.read_text().strip() == "wrote 5 chars"


def test_post_hook_only_applies_to_matching_tools(tmp_path):
    rules = [HookRule(tools=["edit"], command="echo nope")]
    appended = hooks.run_post(rules, "write", {}, str(tmp_path), "result")
    assert appended == ""


def test_post_hook_with_no_output_appends_nothing(tmp_path):
    rules = [HookRule(tools=["write"], command="true")]
    appended = hooks.run_post(rules, "write", {}, str(tmp_path), "result")
    assert appended == ""


def test_post_hook_output_is_capped(tmp_path):
    rules = [HookRule(tools=["write"], command="python3 -c \"print('x' * 5000)\"")]
    appended = hooks.run_post(rules, "write", {}, str(tmp_path), "result")
    assert len(appended) <= hooks.MAX_POST_APPEND


def test_post_hook_failure_to_run_never_raises(tmp_path, monkeypatch):
    import subprocess

    def boom(*a, **k):
        raise OSError("no such command")
    monkeypatch.setattr(subprocess, "run", boom)

    rules = [HookRule(tools=["write"], command="true")]
    appended = hooks.run_post(rules, "write", {}, str(tmp_path), "result")
    assert appended == ""


def test_multiple_pre_hooks_first_blocking_one_wins(tmp_path):
    rules = [HookRule(tools=["write"], command="true"),
             HookRule(tools=["write"], command="echo second blocks && exit 1")]
    blocked, why = hooks.run_pre(rules, "write", {}, str(tmp_path))
    assert blocked is True
    assert "second blocks" in why
