"""Delaware franchise tax for tax year 2026 (due 2027-03-01). Pure functions, no I/O.

Authorized-shares method: 5,000 shares or fewer $175; 5,001-10,000 $250; each extra
10,000 shares or part of 10,000 adds $85; capped at $200,000.

Assumed-par-value capital method: assumed par = gross assets / issued shares; when the
stated par is higher, the stated par is used instead; capital = par used x authorized
shares; tax = $400 per $1,000,000 of capital or part of $1,000,000; min $400, max $200,000.

The corporation pays the lower of the two, plus the annual report fee. The fee is an
input (sources disagree between $50 and $100), never a constant here.
Arithmetic is exact (Fraction); amounts come out as integer cents.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
from fractions import Fraction
from typing import Literal

from app.engine.errors import ValidationError
from app.engine.money import require_cents

ENGINE_VERSION = "1"
TAX_YEAR = 2026
DUE_DATE = date(2027, 3, 1)
DUE_DATE_TEXT = "March 1, 2027"
DEFAULT_STATED_PAR = Decimal("0.00001")  # used when the client doesn't say; labelled "assumed"

MAX_TAX_CENTS = 200_000_00
APV_MIN_CENTS = 400_00
APV_RATE_CENTS = 400_00  # per $1,000,000 of assumed par value capital, or part
APV_STEP_DOLLARS = 1_000_000
AUTH_UP_TO_5K_CENTS = 175_00
AUTH_UP_TO_10K_CENTS = 250_00
AUTH_STEP_CENTS = 85_00  # per extra 10,000 shares or part
AUTH_STEP_SHARES = 10_000

Method = Literal["assumed_par", "authorized"]


@dataclass(frozen=True)
class FeeQuote:
    """The annual report fee and where it came from (`snapshot`, `apify`, `manual`...)."""

    cents: int
    source: str

    def __post_init__(self) -> None:
        require_cents("annual_report_fee.cents", self.cents)
        if self.cents < 0:
            raise ValidationError("annual_report_fee", "must be >= 0")
        if not self.source:
            raise ValidationError("annual_report_fee", "needs a source label")


@dataclass(frozen=True)
class FranchiseResult:
    authorized_method_cents: int
    assumed_par_method_cents: int
    chosen: Method
    annual_report_fee_cents: int
    fee_source: str
    total_cents: int
    due_date: str
    engine_version: str

    @property
    def tax_cents(self) -> int:
        return min(self.authorized_method_cents, self.assumed_par_method_cents)

    def as_dict(self) -> dict:
        return asdict(self)


def _ceil_div(n: Fraction | int, d: int) -> int:
    return int(-((-Fraction(n)) // d))


def _whole(field: str, value: object, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(field, f"must be a whole number, got {value!r}")
    if value < minimum:
        raise ValidationError(field, f"must be >= {minimum}, got {value}")
    return value


def _par(value: object) -> Fraction:
    if isinstance(value, bool) or not isinstance(value, (Decimal, int)):
        raise ValidationError("stated_par", f"must be a Decimal, got {value!r}")
    if isinstance(value, Decimal) and not value.is_finite():
        raise ValidationError("stated_par", "must be finite")
    if value < 0:
        raise ValidationError("stated_par", f"must be >= 0, got {value}")
    return Fraction(value)


def authorized_shares_method_cents(authorized_shares: int) -> int:
    shares = _whole("authorized_shares", authorized_shares, minimum=1)
    if shares <= 5_000:
        return AUTH_UP_TO_5K_CENTS
    if shares <= 10_000:
        return AUTH_UP_TO_10K_CENTS
    steps = _ceil_div(shares - 10_000, AUTH_STEP_SHARES)
    return min(AUTH_UP_TO_10K_CENTS + steps * AUTH_STEP_CENTS, MAX_TAX_CENTS)


def assumed_par_value_capital(
    authorized_shares: int,
    issued_shares: int,
    gross_assets_cents: int,
    stated_par: Decimal = DEFAULT_STATED_PAR,
) -> Fraction:
    """Assumed par value capital in dollars (exact)."""
    authorized = _whole("authorized_shares", authorized_shares, minimum=1)
    if issued_shares == 0 and not isinstance(issued_shares, bool):
        raise ValidationError("issued_shares", "assumed par needs issued shares > 0")
    issued = _whole("issued_shares", issued_shares, minimum=1)
    if issued > authorized:
        raise ValidationError("issued_shares", "issued shares can't exceed authorized shares")
    assets = require_cents("gross_assets_cents", gross_assets_cents)
    if assets <= 0:
        raise ValidationError("gross_assets", "assumed par needs gross assets above $0")
    assumed_par = Fraction(assets, 100 * issued)
    par_used = max(assumed_par, _par(stated_par))
    return par_used * authorized


def assumed_par_method_cents(
    authorized_shares: int,
    issued_shares: int,
    gross_assets_cents: int,
    stated_par: Decimal = DEFAULT_STATED_PAR,
) -> int:
    capital = assumed_par_value_capital(
        authorized_shares, issued_shares, gross_assets_cents, stated_par
    )
    steps = _ceil_div(capital, APV_STEP_DOLLARS)
    return min(max(steps * APV_RATE_CENTS, APV_MIN_CENTS), MAX_TAX_CENTS)


def compute_franchise_tax(
    *,
    authorized_shares: int,
    issued_shares: int,
    gross_assets_cents: int,
    annual_report_fee: FeeQuote,
    stated_par: Decimal = DEFAULT_STATED_PAR,
) -> FranchiseResult:
    """Both methods, the lower one, plus the annual report fee."""
    if not isinstance(annual_report_fee, FeeQuote):
        raise ValidationError("annual_report_fee", "must be a FeeQuote(cents, source)")
    auth = authorized_shares_method_cents(authorized_shares)
    apv = assumed_par_method_cents(authorized_shares, issued_shares, gross_assets_cents, stated_par)
    chosen: Method = "assumed_par" if apv < auth else "authorized"
    return FranchiseResult(
        authorized_method_cents=auth,
        assumed_par_method_cents=apv,
        chosen=chosen,
        annual_report_fee_cents=annual_report_fee.cents,
        fee_source=annual_report_fee.source,
        total_cents=min(auth, apv) + annual_report_fee.cents,
        due_date=DUE_DATE.isoformat(),
        engine_version=ENGINE_VERSION,
    )
