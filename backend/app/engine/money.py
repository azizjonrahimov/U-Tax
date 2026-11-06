"""Money helpers. Tax amounts are integer cents everywhere; no floats."""

from decimal import Decimal


def require_cents(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be integer cents, got {value!r}")
    return value


def format_usd(cents: int) -> str:
    """8516500 -> "$85,165"; 5050 -> "$50.50"; -40000 -> "-$400"."""
    require_cents("cents", cents)
    sign = "-" if cents < 0 else ""
    dollars, rem = divmod(abs(cents), 100)
    text = f"{sign}${dollars:,}"
    return f"{text}.{rem:02d}" if rem else text


def cents_to_decimal(cents: int) -> Decimal:
    return Decimal(require_cents("cents", cents)) / 100
