from decimal import Decimal

import pytest

from app.engine import MissingFactError, ValidationError, form5472_flag, rnd_flag


# ---------------------------------------------------------------- Form 5472


def test_foreign_owner_25_turns_flag_on():
    assert form5472_flag(True) is True
    assert form5472_flag(False) is False


def test_missing_foreign_owner_answer_asks():
    with pytest.raises(MissingFactError) as info:
        form5472_flag(None)
    assert info.value.fact == "foreign_owner_25"


@pytest.mark.parametrize(
    "pct, flag",
    [(25, True), (Decimal("25.0"), True), (Decimal("24.99"), False), (0, False), (100, True)],
)
def test_percentage_threshold_exactly_25_is_on(pct, flag):
    assert form5472_flag(pct) is flag


@pytest.mark.parametrize("bad", [-1, 101, Decimal("NaN"), "yes", 0.25])
def test_bad_foreign_owner_values(bad):
    with pytest.raises(ValidationError):
        form5472_flag(bad)


# ---------------------------------------------------------------- R&D payroll offset


def test_rnd_unknown_is_off():
    assert rnd_flag() is False
    assert rnd_flag(gross_receipts_cents=100_000_00) is False
    assert rnd_flag(years_with_receipts=1) is False


@pytest.mark.parametrize(
    "receipts, years, flag",
    [
        (0, 0, True),
        (4_999_999_99, 5, True),
        (5_000_000_00, 1, False),  # must be under $5M
        (100_000_00, 6, False),  # receipts more than 5 years back
    ],
)
def test_rnd_qualified_small_business(receipts, years, flag):
    assert rnd_flag(receipts, years) is flag


@pytest.mark.parametrize("receipts, years", [(-1, 1), (100, -1), (100, True)])
def test_rnd_bad_inputs(receipts, years):
    with pytest.raises(ValidationError):
        rnd_flag(receipts, years)
