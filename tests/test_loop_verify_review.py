import json

import pytest

from omega import checkpoint, events, llm, loop, subagent, tools, verify
from omega.llm import ToolCall, Turn


class FakeProvider:
    name = "fake-provider"


class FakeRole:
    context = 1_000_000
    model = "fake-model"
    alias = None
    fallback_alias = None
    provider = FakeProvider()


class FakeCfg:
    def __init__(self, verify_auto=True, verify_checks=None, review_auto=True):
        self.verify_auto = verify_auto
        self.verify_checks = verify_checks
        self.review_auto = review_auto

    def role(self, name):
        return FakeRole()

    def model(self, alias):
        return FakeRole()


def scripted_stream(rounds: list):
    async def stream(role, messages, schemas=None, fallback=None):
        for kind, payload in rounds.pop(0):
            yield kind, payload
    return stream


WRITE_CALL = ToolCall(id="call_1", name="write",
                      arguments=json.dumps({"path": "x.py", "content": "x = 1"}))


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    monkeypatch.setattr(tools, "SESSION_ID", "sess-verify")

    async def fake_tool_run(call, allowed=None):
        return "wrote 5 chars"
    monkeypatch.setattr(tools, "run", fake_tool_run)
    yield


# ---- verification loop: fail then pass on retry -----------------------------


@pytest.mark.asyncio
async def test_verification_failure_triggers_a_retry_round(monkeypatch):
    fail = verify.Result(check=verify.Check("pytest", "pytest", "test"),
                         ok=False, exit_code=1, tail="AssertionError: boom")
    ok = verify.Result(check=verify.Check("pytest", "pytest", "test"),
                       ok=True, exit_code=0, tail="1 passed")
    call_results = [[fail], [ok]]

    monkeypatch.setattr(verify, "resolve", lambda cwd, override: [fail.check])
    monkeypatch.setattr(verify, "run", lambda checks, cwd: call_results.pop(0))

    rounds = [
        [("tool", WRITE_CALL), ("done", Turn(text="", tool_calls=[WRITE_CALL]))],
        [("done", Turn(text="applied the fix", tool_calls=[]))],
        [("done", Turn(text="fixed", tool_calls=[]))],
    ]
    monkeypatch.setattr(llm, "stream", scripted_stream(rounds))

    received = []
    history = [{"role": "user", "content": "add a function"}]
    result = await loop.run_agent(FakeCfg(review_auto=False), "main", "sys", history,
                                  emit=received.append, verify_enabled=True, turn_number=1)

    assert result == "fixed"
    assert not call_results  # both canned results were consumed

    verified = [e for e in received if isinstance(e, events.Verified)]
    assert [v.ok for v in verified] == [False, True]

    verification_messages = [m for m in history
                             if m.get("role") == "user"
                             and isinstance(m.get("content"), str)
                             and m["content"].startswith("[verification]")]
    assert len(verification_messages) == 1
    assert "pytest: exit 1" in verification_messages[0]["content"]
    assert "AssertionError: boom" in verification_messages[0]["content"]


@pytest.mark.asyncio
async def test_verification_gives_up_after_max_fixes_and_reports_in_final_text(monkeypatch):
    fail = verify.Result(check=verify.Check("pytest", "pytest", "test"),
                         ok=False, exit_code=1, tail="still broken")
    monkeypatch.setattr(verify, "resolve", lambda cwd, override: [fail.check])
    monkeypatch.setattr(verify, "run", lambda checks, cwd: [fail])

    rounds = [
        [("tool", WRITE_CALL), ("done", Turn(text="", tool_calls=[WRITE_CALL]))],
        [("done", Turn(text="try 1", tool_calls=[]))],
        [("done", Turn(text="try 2", tool_calls=[]))],
        [("done", Turn(text="try 3", tool_calls=[]))],
    ]
    monkeypatch.setattr(llm, "stream", scripted_stream(rounds))

    history = [{"role": "user", "content": "add a function"}]
    result = await loop.run_agent(FakeCfg(review_auto=False), "main", "sys", history,
                                  emit=lambda e: None, verify_enabled=True, turn_number=1)

    assert result.startswith("try 3")
    assert "still failing after 2 fix attempt(s)" in result
    assert "still broken" in result


@pytest.mark.asyncio
async def test_verify_auto_false_skips_verification_entirely(monkeypatch):
    resolve_calls = []
    monkeypatch.setattr(verify, "resolve", lambda cwd, override: resolve_calls.append(1) or [])

    rounds = [
        [("tool", WRITE_CALL), ("done", Turn(text="", tool_calls=[WRITE_CALL]))],
        [("done", Turn(text="done", tool_calls=[]))],
    ]
    monkeypatch.setattr(llm, "stream", scripted_stream(rounds))

    history = [{"role": "user", "content": "add a function"}]
    result = await loop.run_agent(FakeCfg(verify_auto=False, review_auto=False), "main", "sys",
                                  history, emit=lambda e: None, verify_enabled=True, turn_number=1)

    assert result == "done"
    assert resolve_calls == []


@pytest.mark.asyncio
async def test_plan_mode_style_call_never_verifies_even_if_files_mutated(monkeypatch):
    """verify_enabled defaults to False -- run_turn only sets it True for
    mode == "build"; a plan/discuss turn (and every subagent call) must not
    trigger verification even though its tool calls could in principle mutate."""
    resolve_calls = []
    monkeypatch.setattr(verify, "resolve", lambda cwd, override: resolve_calls.append(1) or [])

    rounds = [
        [("tool", WRITE_CALL), ("done", Turn(text="", tool_calls=[WRITE_CALL]))],
        [("done", Turn(text="done", tool_calls=[]))],
    ]
    monkeypatch.setattr(llm, "stream", scripted_stream(rounds))

    history = [{"role": "user", "content": "add a function"}]
    result = await loop.run_agent(FakeCfg(), "main", "sys", history, emit=lambda e: None)

    assert result == "done"
    assert resolve_calls == []


# ---- review subagent ---------------------------------------------------------


BIG_DIFF = "\n".join(f"+line {i}" for i in range(40))
SMALL_DIFF = "+line 1\n-line 2\n"


@pytest.mark.asyncio
async def test_large_diff_triggers_review_and_ok_verdict_ends_the_turn(monkeypatch):
    monkeypatch.setattr(checkpoint, "diff", lambda sid, since_turn=None: BIG_DIFF)

    review_calls = []

    async def fake_review(cfg, request, diff_text, emit):
        review_calls.append((request, diff_text))
        return "OK"
    monkeypatch.setattr(subagent, "review", fake_review)

    rounds = [
        [("tool", WRITE_CALL), ("done", Turn(text="", tool_calls=[WRITE_CALL]))],
        [("done", Turn(text="done", tool_calls=[]))],
    ]
    monkeypatch.setattr(llm, "stream", scripted_stream(rounds))

    history = [{"role": "user", "content": "add a function"}]
    result = await loop.run_agent(FakeCfg(verify_auto=False), "main", "sys", history,
                                  emit=lambda e: None, verify_enabled=True, turn_number=1)

    assert result == "done"
    assert len(review_calls) == 1
    assert review_calls[0] == ("add a function", BIG_DIFF)
    assert not any(m.get("content", "").startswith("[review]") for m in history
                  if isinstance(m.get("content"), str))


@pytest.mark.asyncio
async def test_large_diff_with_issues_appends_review_message_for_one_more_round(monkeypatch):
    monkeypatch.setattr(checkpoint, "diff", lambda sid, since_turn=None: BIG_DIFF)

    async def fake_review(cfg, request, diff_text, emit):
        return "1. missing error handling on line 12"
    monkeypatch.setattr(subagent, "review", fake_review)

    rounds = [
        [("tool", WRITE_CALL), ("done", Turn(text="", tool_calls=[WRITE_CALL]))],
        [("done", Turn(text="done", tool_calls=[]))],
        [("done", Turn(text="addressed the review", tool_calls=[]))],
    ]
    monkeypatch.setattr(llm, "stream", scripted_stream(rounds))

    history = [{"role": "user", "content": "add a function"}]
    result = await loop.run_agent(FakeCfg(verify_auto=False), "main", "sys", history,
                                  emit=lambda e: None, verify_enabled=True, turn_number=1)

    assert result == "addressed the review"
    review_messages = [m["content"] for m in history if isinstance(m.get("content"), str)
                       and m["content"].startswith("[review]")]
    assert review_messages == ["[review] 1. missing error handling on line 12"]


@pytest.mark.asyncio
async def test_small_diff_never_triggers_review(monkeypatch):
    monkeypatch.setattr(checkpoint, "diff", lambda sid, since_turn=None: SMALL_DIFF)

    review_calls = []

    async def fake_review(cfg, request, diff_text, emit):
        review_calls.append(1)
        return "OK"
    monkeypatch.setattr(subagent, "review", fake_review)

    rounds = [
        [("tool", WRITE_CALL), ("done", Turn(text="", tool_calls=[WRITE_CALL]))],
        [("done", Turn(text="done", tool_calls=[]))],
    ]
    monkeypatch.setattr(llm, "stream", scripted_stream(rounds))

    history = [{"role": "user", "content": "add a function"}]
    result = await loop.run_agent(FakeCfg(verify_auto=False), "main", "sys", history,
                                  emit=lambda e: None, verify_enabled=True, turn_number=1)

    assert result == "done"
    assert review_calls == []


@pytest.mark.asyncio
async def test_review_auto_false_skips_review(monkeypatch):
    monkeypatch.setattr(checkpoint, "diff", lambda sid, since_turn=None: BIG_DIFF)

    review_calls = []

    async def fake_review(cfg, request, diff_text, emit):
        review_calls.append(1)
        return "OK"
    monkeypatch.setattr(subagent, "review", fake_review)

    rounds = [
        [("tool", WRITE_CALL), ("done", Turn(text="", tool_calls=[WRITE_CALL]))],
        [("done", Turn(text="done", tool_calls=[]))],
    ]
    monkeypatch.setattr(llm, "stream", scripted_stream(rounds))

    history = [{"role": "user", "content": "add a function"}]
    result = await loop.run_agent(FakeCfg(verify_auto=False, review_auto=False), "main", "sys",
                                  history, emit=lambda e: None, verify_enabled=True, turn_number=1)

    assert result == "done"
    assert review_calls == []


@pytest.mark.asyncio
async def test_no_mutation_skips_both_verify_and_review(monkeypatch):
    resolve_calls = []
    monkeypatch.setattr(verify, "resolve", lambda cwd, override: resolve_calls.append(1) or [])
    review_calls = []

    async def fake_review(cfg, request, diff_text, emit):
        review_calls.append(1)
        return "OK"
    monkeypatch.setattr(subagent, "review", fake_review)

    rounds = [[("done", Turn(text="no changes needed", tool_calls=[]))]]
    monkeypatch.setattr(llm, "stream", scripted_stream(rounds))

    history = [{"role": "user", "content": "is this file already correct?"}]
    result = await loop.run_agent(FakeCfg(), "main", "sys", history, emit=lambda e: None,
                                  verify_enabled=True, turn_number=1)

    assert result == "no changes needed"
    assert resolve_calls == []
    assert review_calls == []
