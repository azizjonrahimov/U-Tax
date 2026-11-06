"""Agent cost booking (decision E4): logged tokens priced in code, labelled "estimated".

Prices are USD per 1M tokens (input, output). UNVERIFIED: these are placeholders until
OpenRouter's per-call cost field is wired Saturday; a provider-reported cost always wins.
"""

from __future__ import annotations

from decimal import Decimal

from decimal import ROUND_HALF_UP, Decimal

PRICES_PER_MTOK: dict[str, tuple[Decimal, Decimal]] = {
    "openai/gpt-5-mini": (Decimal("0.25"), Decimal("2.00")),
    "openai/gpt-5": (Decimal("1.25"), Decimal("10.00")),
    "z-ai/glm-5.3-flash": (Decimal("0.10"), Decimal("0.40")),
    "openai/gpt-oss-120b": (Decimal("0.10"), Decimal("0.50")),
}
DEFAULT_PRICE = (Decimal("1.00"), Decimal("4.00"))
FOUR_PLACES = Decimal("0.0001")


def usd_string(value: Decimal) -> str:
    """Fixed-point string with 4 places, the wire format for agent cost (contract v1.3.1).
    `format(d, "f")`, never `str(d)`: str(Decimal("0E-4")) is "0E-4"."""
    return format(value.quantize(FOUR_PLACES, rounding=ROUND_HALF_UP), "f")


def parse_usd(text: str | int | Decimal) -> Decimal:
    if isinstance(text, float) or isinstance(text, bool):
        raise TypeError("cost must be a decimal string, not a float")
    value = Decimal(str(text))
    if not value.is_finite() or value < 0:
        raise ValueError(f"bad cost {text!r}")
    return value


def estimate_usd(model: str | None, tokens_in: int | None, tokens_out: int | None) -> Decimal:
    price_in, price_out = PRICES_PER_MTOK.get(model or "", DEFAULT_PRICE)
    return (Decimal(tokens_in or 0) * price_in + Decimal(tokens_out or 0) * price_out) / 1_000_000


# v1.7 §6a: per-run estimates for the slow sponsors (booked with estimated: true)
APIFY_RUN_USD = Decimal("0.10")  # ApifyScrape caps each run at maxTotalChargeUsd = 0.10
MOBILERUN_STEP_USD = Decimal("0.01")  # UNVERIFIED: no published per-step price read tonight
MOBILERUN_MAX_STEPS = 25  # MobilerunDevice.start() sends maxSteps 25
MOBILERUN_RUN_USD = MOBILERUN_STEP_USD * MOBILERUN_MAX_STEPS
