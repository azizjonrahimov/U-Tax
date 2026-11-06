"""R&D credit payroll offset: a qualified small business (gross receipts under $5M this
year, and no gross receipts in any year before the last 5) can apply up to $500,000 a
year of R&D credit against payroll tax, elected on the original return.

Intake doesn't collect receipts today, so the flag is only on when both facts are known
and qualify. Unknown means off: we never claim an eligibility we can't show.
"""

from __future__ import annotations

from app.engine.errors import ValidationError
from app.engine.money import require_cents

RECEIPTS_LIMIT_CENTS = 5_000_000_00
MAX_YEARS_WITH_RECEIPTS = 5
PAYROLL_OFFSET_MAX_CENTS = 500_000_00


def rnd_flag(
    gross_receipts_cents: int | None = None, years_with_receipts: int | None = None
) -> bool:
    if gross_receipts_cents is None or years_with_receipts is None:
        return False
    receipts = require_cents("gross_receipts_cents", gross_receipts_cents)
    if receipts < 0:
        raise ValidationError("gross_receipts", "must be >= 0")
    if (
        isinstance(years_with_receipts, bool)
        or not isinstance(years_with_receipts, int)
        or years_with_receipts < 0
    ):
        raise ValidationError("years_with_receipts", "must be a whole number >= 0")
    return receipts < RECEIPTS_LIMIT_CENTS and years_with_receipts <= MAX_YEARS_WITH_RECEIPTS
