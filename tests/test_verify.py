import json

from omega import verify


def test_detect_pytest_from_tests_dir(tmp_path):
    (tmp_path / "tests").mkdir()
    checks = verify.detect(str(tmp_path))
    assert any(c.name == "pytest" for c in checks)


def test_detect_pytest_prefers_uv_when_lockfile_present(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "uv.lock").write_text("")
    checks = verify.detect(str(tmp_path))
    pytest_check = next(c for c in checks if c.name == "pytest")
    assert pytest_check.command.startswith("uv run pytest")


def test_detect_pytest_without_uv_lock_falls_back_to_python(tmp_path):
    (tmp_path / "tests").mkdir()
    checks = verify.detect(str(tmp_path))
    pytest_check = next(c for c in checks if c.name == "pytest")
    assert pytest_check.command == "python -m pytest -q -x"


def test_detect_ruff_from_pyproject_tool_section(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\nline-length = 100\n")
    checks = verify.detect(str(tmp_path))
    ruff_check = next(c for c in checks if c.name == "ruff")
    assert ruff_check.kind == "lint"


def test_detect_ruff_from_standalone_config_file(tmp_path):
    (tmp_path / "ruff.toml").write_text("line-length = 100\n")
    checks = verify.detect(str(tmp_path))
    assert any(c.name == "ruff" for c in checks)


def test_detect_mypy_from_pyproject_tool_section(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.mypy]\nstrict = true\n")
    checks = verify.detect(str(tmp_path))
    mypy_check = next(c for c in checks if c.name == "mypy")
    assert mypy_check.kind == "types"


def test_detect_mypy_from_mypy_ini(tmp_path):
    (tmp_path / "mypy.ini").write_text("[mypy]\nstrict = True\n")
    checks = verify.detect(str(tmp_path))
    assert any(c.name == "mypy" for c in checks)


def test_detect_mypy_from_setup_cfg(tmp_path):
    (tmp_path / "setup.cfg").write_text("[mypy]\nstrict = True\n")
    checks = verify.detect(str(tmp_path))
    assert any(c.name == "mypy" for c in checks)


def test_detect_package_json_scripts(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps(
        {"scripts": {"test": "vitest run", "lint": "eslint .", "typecheck": "tsc --noEmit"}}))
    checks = verify.detect(str(tmp_path))
    names = {c.name for c in checks}
    assert {"test", "lint", "typecheck"} <= names
    test_check = next(c for c in checks if c.name == "test")
    assert test_check.command == "npm run test"


def test_detect_package_json_prefers_pnpm_lockfile(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "vitest run"}}))
    (tmp_path / "pnpm-lock.yaml").write_text("")
    checks = verify.detect(str(tmp_path))
    test_check = next(c for c in checks if c.name == "test")
    assert test_check.command == "pnpm run test"


def test_detect_package_json_ignores_scripts_not_in_the_known_set(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"build": "vite build"}}))
    checks = verify.detect(str(tmp_path))
    assert checks == []


def test_detect_cargo_toml(tmp_path):
    (tmp_path / "Cargo.toml").write_text("[package]\nname = \"x\"\n")
    checks = verify.detect(str(tmp_path))
    assert checks == [verify.Check("cargo-test", "cargo test", "test")]


def test_detect_go_mod(tmp_path):
    (tmp_path / "go.mod").write_text("module x\n")
    checks = verify.detect(str(tmp_path))
    assert checks == [verify.Check("go-test", "go test ./...", "test")]


def test_detect_makefile_with_test_target(tmp_path):
    (tmp_path / "Makefile").write_text("test:\n\tpytest\n")
    checks = verify.detect(str(tmp_path))
    assert any(c.name == "make-test" for c in checks)


def test_detect_makefile_without_test_target_is_ignored(tmp_path):
    (tmp_path / "Makefile").write_text("build:\n\tgo build\n")
    checks = verify.detect(str(tmp_path))
    assert checks == []


def test_detect_empty_project_yields_no_checks(tmp_path):
    assert verify.detect(str(tmp_path)) == []


def test_resolve_uses_config_override_verbatim(tmp_path):
    (tmp_path / "tests").mkdir()  # would otherwise auto-detect pytest
    checks = verify.resolve(str(tmp_path), ["./run-checks.sh"])
    assert checks == [verify.Check("./run-checks.sh", "./run-checks.sh", "test")]


def test_resolve_falls_back_to_detect_when_no_override(tmp_path):
    (tmp_path / "tests").mkdir()
    checks = verify.resolve(str(tmp_path), None)
    assert any(c.name == "pytest" for c in checks)


def test_run_reports_success_and_tail(tmp_path):
    check = verify.Check("ok", "python3 -c \"print('hello')\"", "test")
    results = verify.run([check], str(tmp_path))
    assert len(results) == 1
    assert results[0].ok is True
    assert results[0].exit_code == 0
    assert "hello" in results[0].tail


def test_run_reports_failure_and_exit_code(tmp_path):
    check = verify.Check("bad", "python3 -c \"import sys; sys.exit(3)\"", "test")
    results = verify.run([check], str(tmp_path))
    assert results[0].ok is False
    assert results[0].exit_code == 3


def test_run_tail_is_capped_to_last_40_lines(tmp_path):
    check = verify.Check("many-lines", "python3 -c \"[print(i) for i in range(200)]\"", "test")
    results = verify.run([check], str(tmp_path))
    lines = results[0].tail.splitlines()
    assert len(lines) == 40
    assert lines[-1] == "199"


def test_summarize_reports_ok_and_failed(tmp_path):
    ok_check = verify.Check("pytest", "python3 -c \"pass\"", "test")
    bad_check = verify.Check("ruff", "python3 -c \"import sys; sys.exit(1)\"", "lint")
    results = verify.run([ok_check, bad_check], str(tmp_path))
    summary = verify.summarize(results)
    assert "pytest ok" in summary
    assert "ruff FAILED(exit 1)" in summary
