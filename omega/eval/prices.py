# Per-million-token USD prices (input, output), keyed by the model catalog
# alias (config.DEFAULTS["models"]) -- not the provider's raw model id, so a
# `--models opus,sonnet` run can price itself without a network round trip.
PRICES: dict[str, tuple[float, float]] = {
    "fable":  (10.0, 50.0),
    "opus":   (5.0, 25.0),
    "sonnet": (2.0, 10.0),
    "haiku":  (1.0, 5.0),
    "spark":  (1.25, 4.25),
    "kimi":   (3.0, 15.0),
    "glm":    (0.6, 2.2),
}


def estimate_cost(alias: str, tokens_in: int, tokens_out: int) -> float | None:
    """None for an alias absent from PRICES -- an unpriced model must show up
    as "unknown" in a report, never as a silent $0."""
    prices = PRICES.get(alias)
    if prices is None:
        return None
    price_in, price_out = prices
    return tokens_in / 1_000_000 * price_in + tokens_out / 1_000_000 * price_out
