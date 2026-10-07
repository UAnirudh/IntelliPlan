"""What a model call costs, in micro-dollars.

Money is kept in integer micro-dollars (1 USD = 1_000_000) everywhere it is
stored or compared, so a month of small calls never drifts the way a running
float total does.

Prices are per million tokens. The Claude rows are Anthropic's first-party
list prices. The Groq rows are estimates for the router's own calls and
should be checked against the Groq price page before billing goes live.
``PREMIUM_PRICE_OVERRIDES`` (JSON: ``{"model": {"input": 2.0, "output": 10.0}}``)
replaces any row without a deploy, so a price change is a config change.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)

MICRO = 1_000_000

#: Cache writes cost this multiple of the input price (5-minute TTL).
CACHE_WRITE_MULTIPLIER = 1.25


@dataclass(frozen=True)
class Price:
    input: float          # USD per million input tokens
    output: float         # USD per million output tokens
    cache_read: float     # USD per million cached input tokens read

    @property
    def cache_write(self) -> float:
        return self.input * CACHE_WRITE_MULTIPLIER


_BASE_PRICES: dict[str, Price] = {
    # Anthropic, first-party API.
    "claude-opus-5-5": Price(4.00, 20.00, 0.20),
    "claude-sonnet-5-5": Price(2.00, 10.00, 0.20),
    "claude-haiku-5-5": Price(0.10, 0.50, 0.01),
    "claude-sonnet-5": Price(2.00, 10.00, 0.20),
    "claude-haiku-4-5": Price(1.00, 5.00, 0.10),
    # Groq, used for routing. Estimates; see the module docstring.
    "openai/gpt-oss-20b": Price(0.10, 0.50, 0.10),
    "openai/gpt-oss-120b": Price(0.15, 0.75, 0.15),
}

#: Charged when a model is not in the table, so an unknown model can never
#: run for free. Deliberately the most expensive Claude row.
_UNKNOWN = _BASE_PRICES["claude-opus-5-5"]


def _overrides() -> dict[str, Price]:
    raw = os.getenv("PREMIUM_PRICE_OVERRIDES", "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return {
            str(model): Price(float(p["input"]), float(p["output"]),
                              float(p.get("cache_read", p["input"] * 0.1)))
            for model, p in data.items()
        }
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        logger.warning("PREMIUM_PRICE_OVERRIDES ignored: %s", exc)
        return {}


def price_for(model: str) -> Price:
    return _overrides().get(model) or _BASE_PRICES.get(model) or _UNKNOWN


def known(model: str) -> bool:
    return model in _overrides() or model in _BASE_PRICES


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @classmethod
    def from_anthropic(cls, usage) -> "Usage":
        """Read an Anthropic ``usage`` object, whose cache fields can be None."""
        return cls(
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            cache_read_tokens=int(getattr(usage, "cache_read_input_tokens", 0) or 0),
            cache_write_tokens=int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
        )

    @classmethod
    def from_openai_style(cls, usage) -> "Usage":
        """Groq and OpenAI report prompt/completion tokens."""
        return cls(
            input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
        )


def cost_micro(model: str, usage: Usage) -> int:
    """Exact cost of one call, rounded up to the next micro-dollar."""
    p = price_for(model)
    usd = (
        usage.input_tokens * p.input
        + usage.output_tokens * p.output
        + usage.cache_read_tokens * p.cache_read
        + usage.cache_write_tokens * p.cache_write
    ) / 1_000_000
    micro = usd * MICRO
    whole = int(micro)
    return whole + (1 if micro > whole else 0)


def worst_case_micro(model: str, input_tokens: int, max_output_tokens: int) -> int:
    """The most a call can cost before it runs: every output token spent,
    every input token uncached. What the budget must have room for."""
    return cost_micro(model, Usage(input_tokens=input_tokens, output_tokens=max_output_tokens))


def estimate_tokens(text: str) -> int:
    """Rough token count for budgeting only (about four characters a token).

    Never used for billing: the provider's reported usage is what is charged.
    """
    return max(1, len(text or "") // 4 + 1)


def usd(micro: int) -> float:
    return round(int(micro) / MICRO, 4)


def micro_from_usd(amount: float) -> int:
    return int(round(float(amount) * MICRO))
