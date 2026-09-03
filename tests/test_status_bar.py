from omega import events
from omega.ui.tui.status import StatusState, format_status


def make_state(usage: events.Usage | None) -> StatusState:
    return StatusState(mode="build", role_name="main", model="claude-opus-5",
                       session_id="20260101-000000-abcd", turns=3, usage=usage, alias="opus")


def test_cache_cell_hidden_when_usage_is_none():
    assert "cache" not in format_status(make_state(None))


def test_cache_cell_hidden_when_cache_read_is_zero():
    usage = events.Usage(prompt_tokens=1000, completion_tokens=50, used=1050, limit=1_000_000,
                         cache_read=0, cache_write=100)
    assert "cache" not in format_status(make_state(usage))


def test_cache_cell_shown_after_tokens_cell():
    usage = events.Usage(prompt_tokens=1000, completion_tokens=50, used=1050, limit=1_000_000,
                         cache_read=870, cache_write=50)
    out = format_status(make_state(usage))
    assert "cache 87%" in out
    assert out.index("tokens") < out.index("cache 87%")


def test_cache_pct_rounds_from_cache_read_over_prompt_tokens():
    usage = events.Usage(prompt_tokens=200, completion_tokens=10, used=210, limit=1_000_000,
                         cache_read=100, cache_write=0)
    out = format_status(make_state(usage))
    assert "cache 50%" in out
