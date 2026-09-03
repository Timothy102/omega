"""Retry ladder + provider/model fallback chain (llm.stream, both backends)."""
import httpx
import pytest
from conftest import Chunk

from omega import llm
from omega.config import Provider, Role

pytestmark = pytest.mark.asyncio


def make_openai_role(model: str = "m") -> Role:
    return Role(model=model, provider=Provider(name="fake-openai", type="openai"), context=100_000)


def make_anthropic_role(model: str = "claude-opus-5") -> Role:
    return Role(model=model, provider=Provider(name="fake-anthropic", type="anthropic",
                                               api_key_literal="test-key"), context=1_000_000, effort="high")


def status_error(cls: type, status_code: int, retry_after: str | None = None) -> Exception:
    request = httpx.Request("POST", "https://example.com")
    headers = {"retry-after": retry_after} if retry_after else {}
    response = httpx.Response(status_code, request=request, headers=headers)
    return cls("boom", response=response, body=None)  # type: ignore[call-arg]


def connection_error(cls: type) -> Exception:
    request = httpx.Request("POST", "https://example.com")
    return cls(request=request)  # type: ignore[call-arg]


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch):
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(llm, "_sleep", fake_sleep)
    yield slept


# ---- OpenAI backend ----------------------------------------------------------------

class FlakyCompletions:
    def __init__(self, plans: list) -> None:
        self._plans = list(plans)
        self.calls = 0

    async def create(self, **kw):
        self.calls += 1
        plan = self._plans.pop(0)
        if isinstance(plan, BaseException):
            raise plan

        async def gen():
            for c in plan:
                yield c
        return gen()


def install_openai_client(role: Role, plans: list) -> FlakyCompletions:
    completions = FlakyCompletions(plans)
    client = type("C", (), {"chat": type("X", (), {"completions": completions})()})()
    llm._clients[role.provider.name] = client
    return completions


async def test_openai_retries_retryable_error_then_succeeds(no_real_sleep):
    role = make_openai_role()
    success = [Chunk(content="hi"), Chunk(finish_reason="stop")]
    completions = install_openai_client(role, [status_error(__import__("openai").APIStatusError, 503), success])

    events = [ev async for ev in llm.stream(role, [{"role": "user", "content": "x"}])]
    kinds = [k for k, _ in events]
    assert kinds == ["phase", "text", "done"]
    assert events[0] == ("phase", "waiting")
    assert completions.calls == 2
    assert len(no_real_sleep) == 1


async def test_openai_gives_up_after_max_attempts(no_real_sleep):
    role = make_openai_role()
    import openai as openai_mod
    err = status_error(openai_mod.APIStatusError, 500)
    completions = install_openai_client(role, [err, err, err])

    with pytest.raises(openai_mod.APIStatusError):
        async for _ in llm.stream(role, [{"role": "user", "content": "x"}]):
            pass
    assert completions.calls == 3


async def test_openai_does_not_retry_non_retryable_status(no_real_sleep):
    role = make_openai_role()
    import openai as openai_mod
    err = status_error(openai_mod.APIStatusError, 400)
    completions = install_openai_client(role, [err])

    with pytest.raises(openai_mod.APIStatusError):
        async for _ in llm.stream(role, [{"role": "user", "content": "x"}]):
            pass
    assert completions.calls == 1
    assert no_real_sleep == []


async def test_openai_connection_error_is_retried(no_real_sleep):
    role = make_openai_role()
    import openai as openai_mod
    success = [Chunk(content="hi"), Chunk(finish_reason="stop")]
    completions = install_openai_client(role, [connection_error(openai_mod.APIConnectionError), success])

    events = [ev async for ev in llm.stream(role, [{"role": "user", "content": "x"}])]
    assert [k for k, _ in events] == ["phase", "text", "done"]
    assert completions.calls == 2


async def test_openai_honors_retry_after_header(no_real_sleep):
    role = make_openai_role()
    import openai as openai_mod
    success = [Chunk(content="hi"), Chunk(finish_reason="stop")]
    install_openai_client(role, [status_error(openai_mod.APIStatusError, 429, retry_after="7"), success])

    async for _ in llm.stream(role, [{"role": "user", "content": "x"}]):
        pass
    assert no_real_sleep == [7.0]


async def test_openai_no_retry_once_text_was_already_emitted(no_real_sleep):
    """A mid-stream failure after text was emitted must surface as an error,
    never silently restart (which would duplicate the already-emitted text)."""
    role = make_openai_role()
    import openai as openai_mod

    class DiesAfterOneChunk:
        calls = 0

        async def create(self, **kw):
            DiesAfterOneChunk.calls += 1

            async def gen():
                yield Chunk(content="partial")
                raise status_error(openai_mod.APIStatusError, 503)
            return gen()

    client = type("C", (), {"chat": type("X", (), {"completions": DiesAfterOneChunk()})()})()
    llm._clients[role.provider.name] = client

    received = []
    with pytest.raises(openai_mod.APIStatusError):
        async for ev in llm.stream(role, [{"role": "user", "content": "x"}]):
            received.append(ev)
    assert received == [("text", "partial")]
    assert DiesAfterOneChunk.calls == 1
    assert no_real_sleep == []


async def test_openai_falls_back_to_second_role_after_retries_exhausted(no_real_sleep):
    primary = make_openai_role("m-primary")
    secondary = make_openai_role("m-secondary")
    secondary.provider = Provider(name="fake-openai-2", type="openai")

    import openai as openai_mod
    err = status_error(openai_mod.APIStatusError, 529)
    install_openai_client(primary, [err, err, err])
    install_openai_client(secondary, [[Chunk(content="from fallback"), Chunk(finish_reason="stop")]])

    events = [ev async for ev in llm.stream(primary, [{"role": "user", "content": "x"}], fallback=secondary)]
    kinds = [k for k, _ in events]
    assert kinds.count("fallback") == 1
    fb = next(p for k, p in events if k == "fallback")
    assert fb == ("m-primary", "m-secondary", "HTTP 529")
    assert [p for k, p in events if k == "text"] == ["from fallback"]
    turn = next(p for k, p in events if k == "done")
    assert turn.model == "m-secondary"


async def test_openai_no_fallback_once_output_was_emitted(no_real_sleep):
    primary = make_openai_role("m-primary")
    secondary = make_openai_role("m-secondary")
    secondary.provider = Provider(name="fake-openai-3", type="openai")
    import openai as openai_mod

    class DiesAfterOneChunk:
        async def create(self, **kw):
            async def gen():
                yield Chunk(content="partial")
                raise status_error(openai_mod.APIStatusError, 503)
            return gen()

    client = type("C", (), {"chat": type("X", (), {"completions": DiesAfterOneChunk()})()})()
    llm._clients[primary.provider.name] = client

    received = []
    with pytest.raises(openai_mod.APIStatusError):
        async for ev in llm.stream(primary, [{"role": "user", "content": "x"}], fallback=secondary):
            received.append(ev)
    assert all(k != "fallback" for k, _ in received)


# ---- Anthropic backend --------------------------------------------------------------

class FlakyBetaMessages:
    def __init__(self, plans: list) -> None:
        self._plans = list(plans)
        self.calls = 0
        self.last_kwargs: dict = {}

    def stream(self, **kwargs):
        self.calls += 1
        self.last_kwargs = kwargs
        plan = self._plans.pop(0)
        if isinstance(plan, BaseException):
            raise plan
        return plan


def install_anthropic_client(role: Role, plans: list) -> FlakyBetaMessages:
    beta_messages = FlakyBetaMessages(plans)
    client = type("Cl", (), {"beta": type("Beta", (), {"messages": beta_messages})()})()
    llm._anthropic_clients[role.provider.name] = client
    return beta_messages


def make_success_stream():
    from test_llm_anthropic import FakeAnthropicStream, FakeBlock, FakeDelta, FakeEvent, FakeFinalMessage, FakeUsage

    events = [
        FakeEvent(type="content_block_start", index=0, content_block=FakeBlock("text")),
        FakeEvent(type="content_block_delta", index=0, delta=FakeDelta(type="text_delta", text="hi")),
        FakeEvent(type="content_block_stop", index=0),
    ]
    final = FakeFinalMessage(content=[FakeBlock("text")],
                             usage=FakeUsage(input_tokens=5, output_tokens=1, cache_read_input_tokens=0),
                             stop_reason="end_turn", model="claude-opus-5")
    return FakeAnthropicStream(events, final)


async def test_anthropic_retries_retryable_status_then_succeeds(no_real_sleep):
    role = make_anthropic_role()
    import anthropic as anthropic_mod
    err = status_error(anthropic_mod.APIStatusError, 529)
    beta = install_anthropic_client(role, [err, make_success_stream()])

    events = [ev async for ev in llm.stream(role, [{"role": "user", "content": "hi"}])]
    assert [k for k, _ in events] == ["phase", "text", "done"]
    assert beta.calls == 2


async def test_anthropic_gives_up_after_max_attempts(no_real_sleep):
    role = make_anthropic_role()
    import anthropic as anthropic_mod
    err = status_error(anthropic_mod.APIStatusError, 529)
    beta = install_anthropic_client(role, [err, err, err])

    with pytest.raises(anthropic_mod.APIStatusError):
        async for _ in llm.stream(role, [{"role": "user", "content": "hi"}]):
            pass
    assert beta.calls == 3


async def test_anthropic_connection_error_is_retried(no_real_sleep):
    role = make_anthropic_role()
    import anthropic as anthropic_mod
    beta = install_anthropic_client(
        role, [connection_error(anthropic_mod.APIConnectionError), make_success_stream()])

    events = [ev async for ev in llm.stream(role, [{"role": "user", "content": "hi"}])]
    assert [k for k, _ in events] == ["phase", "text", "done"]
    assert beta.calls == 2


async def test_anthropic_falls_back_to_second_role(no_real_sleep):
    primary = make_anthropic_role("claude-opus-5")
    secondary = make_anthropic_role("claude-sonnet-5")
    secondary.provider = Provider(name="fake-anthropic-2", type="anthropic", api_key_literal="k2")

    import anthropic as anthropic_mod
    err = status_error(anthropic_mod.APIStatusError, 529)
    install_anthropic_client(primary, [err, err, err])
    install_anthropic_client(secondary, [make_success_stream()])

    events = [ev async for ev in
             llm.stream(primary, [{"role": "user", "content": "hi"}], fallback=secondary)]
    fb = next(p for k, p in events if k == "fallback")
    assert fb == ("claude-opus-5", "claude-sonnet-5", "HTTP 529")
    turn = next(p for k, p in events if k == "done")
    assert turn.model == "claude-sonnet-5"
