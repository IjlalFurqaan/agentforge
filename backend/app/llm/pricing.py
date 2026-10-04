"""Token pricing (USD per million tokens). Override or extend via MODEL_PRICING_OVERRIDES."""

from __future__ import annotations

from app.llm.types import Usage

# Anthropic first-party list prices (input, output). Cache reads bill at 10% of input, cache
# writes (5-minute TTL) at 125% of input.
DEFAULT_PRICES: dict[str, dict[str, float]] = {
    "anthropic/claude-fable-5-1": {"input": 10.0, "output": 50.0},
    "anthropic/claude-opus-5-5": {"input": 4.0, "output": 20.0, "cache_read": 0.20},
    "anthropic/claude-opus-5": {"input": 5.0, "output": 25.0},
    "anthropic/claude-opus-4-8": {"input": 5.0, "output": 25.0},
    "anthropic/claude-sonnet-5-5": {"input": 2.0, "output": 10.0, "cache_read": 0.20},
    "anthropic/claude-sonnet-5": {"input": 2.0, "output": 10.0},
    "anthropic/claude-haiku-5-5": {"input": 0.10, "output": 0.50},
    "anthropic/claude-haiku-4-5": {"input": 1.0, "output": 5.0},
}
FREE_PROVIDERS = {"ollama"}


def price_for(provider: str, model: str, overrides: dict[str, dict[str, float]] | None = None) -> dict[str, float] | None:
    key = f"{provider}/{model}"
    if overrides and key in overrides:
        return overrides[key]
    if provider in FREE_PROVIDERS:
        return {"input": 0.0, "output": 0.0}
    return DEFAULT_PRICES.get(key)


def cost_usd(provider: str, model: str, usage: Usage, overrides: dict[str, dict[str, float]] | None = None) -> float | None:
    """Return the cost of a call, or None when the model has no known price."""
    p = price_for(provider, model, overrides)
    if p is None:
        return None
    inp = p["input"]
    cache_read = p.get("cache_read", inp * 0.1)
    cache_write = p.get("cache_write", inp * 1.25)
    total = (
        usage.input_tokens * inp
        + usage.output_tokens * p["output"]
        + usage.cache_read_tokens * cache_read
        + usage.cache_write_tokens * cache_write
    ) / 1_000_000
    return round(total, 6)
