"""Form 5472: a US corporation 25%+ owned by a foreign person must file it
($25,000 penalty per form per year if it isn't filed). We flag it; we never file."""

from __future__ import annotations

from decimal import Decimal

from app.engine.errors import MissingFactError, ValidationError

THRESHOLD_PCT = Decimal(25)


def form5472_flag(foreign_owner_25: bool | int | Decimal | None) -> bool:
    """`True`/`False` from the intake fact, or a foreign ownership percentage (25 -> on).

    A missing answer raises MissingFactError so Intake asks instead of guessing.
    """
    if foreign_owner_25 is None:
        raise MissingFactError("foreign_owner_25")
    if isinstance(foreign_owner_25, bool):
        return foreign_owner_25
    if isinstance(foreign_owner_25, (int, Decimal)):
        pct = Decimal(foreign_owner_25)
        if not pct.is_finite() or not 0 <= pct <= 100:
            raise ValidationError("foreign_owner_25", "percentage must be between 0 and 100")
        return pct >= THRESHOLD_PCT
    raise ValidationError(
        "foreign_owner_25", f"must be yes/no or a percentage, got {foreign_owner_25!r}"
    )
