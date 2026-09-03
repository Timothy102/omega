import pytest

from omega.eval import prices


def test_known_aliases_priced():
    assert prices.PRICES["opus"] == (5.0, 25.0)
    assert prices.PRICES["sonnet"] == (2.0, 10.0)
    assert prices.PRICES["glm"] == (0.6, 2.2)
    assert set(prices.PRICES) == {"fable", "opus", "sonnet", "haiku", "spark", "kimi", "glm"}


def test_estimate_cost_known_alias():
    cost = prices.estimate_cost("sonnet", 1_000_000, 1_000_000)
    assert cost == pytest.approx(2.0 + 10.0)


def test_estimate_cost_zero_tokens_is_zero():
    assert prices.estimate_cost("opus", 0, 0) == 0.0


def test_estimate_cost_unknown_alias_is_none():
    assert prices.estimate_cost("mystery-model", 1000, 1000) is None
