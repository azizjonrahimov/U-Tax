import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.engine import (
    DUE_DATE_TEXT,
    FeeQuote,
    ValidationError,
    assumed_par_method_cents,
    authorized_shares_method_cents,
    compute_franchise_tax,
    format_usd,
)

FEE = FeeQuote(cents=5000, source="snapshot")
SAMPLE = Path(__file__).resolve().parents[3] / "contracts" / "events.sample.jsonl"


# ---------------------------------------------------------------- authorized-shares method


@pytest.mark.parametrize(
    "shares, cents",
    [
        (10_000_000, 85_165_00),  # Test Plan: 10M -> $85,165
        (5_000, 175_00),  # Test Plan: 5,000 -> $175
        (10_000, 250_00),  # Test Plan: 10,000 -> $250
        (10_001, 335_00),  # Test Plan edge: 10,001 -> $335
        (1, 175_00),
        (5_001, 250_00),
        (20_000, 335_00),
        (20_001, 420_00),
        (100_000_000, 200_000_00),  # capped
    ],
)
def test_authorized_shares_method(shares, cents):
    assert authorized_shares_method_cents(shares) == cents


@pytest.mark.parametrize("bad", [-1, 0, -10_000_000, 1.5, "10M", True, None])
def test_authorized_shares_rejects_bad_input(bad):
    with pytest.raises(ValidationError):
        authorized_shares_method_cents(bad)


# ---------------------------------------------------------------- assumed-par method


def test_assumed_par_sample_case():
    # Test Plan: 10M authorized, 8M issued, $500k assets -> $400
    assert assumed_par_method_cents(10_000_000, 8_000_000, 500_000_00) == 400_00


def test_assumed_par_zero_issued_is_a_validation_error():
    with pytest.raises(ValidationError) as info:
        assumed_par_method_cents(10_000_000, 0, 500_000_00)
    assert info.value.field == "issued_shares"
    assert "issued shares > 0" in info.value.message


def test_assumed_par_huge_assets_capped():
    # Test Plan edge: huge assets -> capped $200,000
    assert assumed_par_method_cents(10_000_000, 8_000_000, 10_000_000_000_00) == 200_000_00


def test_assumed_par_one_million_or_part():
    # capital exactly $1,000,000 -> $400; one cent more -> $800
    assert assumed_par_method_cents(1_000_000, 1_000_000, 1_000_000_00) == 400_00
    assert assumed_par_method_cents(1_000_000, 1_000_000, 1_000_000_01) == 800_00


def test_assumed_par_uses_stated_par_when_higher():
    # Test Plan edge: stated par > assumed par uses stated. Assumed par is $0.0625;
    # stated $1.00 -> capital $10M -> 10 x $400.
    assert assumed_par_method_cents(10_000_000, 8_000_000, 500_000_00, Decimal("1")) == 4_000_00
    # and a lower stated par changes nothing
    assert assumed_par_method_cents(10_000_000, 8_000_000, 500_000_00, Decimal("0.00001")) == 400_00


@pytest.mark.parametrize(
    "authorized, issued, assets, field",
    [
        (10_000_000, 12_000_000, 500_000_00, "issued_shares"),  # issued > authorized
        (10_000_000, -1, 500_000_00, "issued_shares"),
        (10_000_000, 8_000_000, -1, "gross_assets"),
        (10_000_000, 8_000_000, 0, "gross_assets"),  # Empty/zero path: $0 assets
        (-5, 1, 500_000_00, "authorized_shares"),
    ],
)
def test_assumed_par_validation(authorized, issued, assets, field):
    with pytest.raises(ValidationError) as info:
        assumed_par_method_cents(authorized, issued, assets)
    assert info.value.field == field


def test_assumed_par_rejects_float_money_and_par():
    with pytest.raises(TypeError):
        assumed_par_method_cents(10_000_000, 8_000_000, 500000.0)
    with pytest.raises(ValidationError):
        assumed_par_method_cents(10_000_000, 8_000_000, 500_000_00, 0.00001)
    with pytest.raises(ValidationError):
        assumed_par_method_cents(10_000_000, 8_000_000, 500_000_00, Decimal("-0.1"))


# ---------------------------------------------------------------- lower of two + due date


def test_compute_returns_lower_plus_fee_and_due_date():
    result = compute_franchise_tax(
        authorized_shares=10_000_000, issued_shares=8_000_000,
        gross_assets_cents=500_000_00, annual_report_fee=FEE,
    )
    assert result.authorized_method_cents == 85_165_00
    assert result.assumed_par_method_cents == 400_00
    assert result.chosen == "assumed_par"
    assert result.tax_cents == 400_00
    assert result.total_cents == 450_00
    assert result.due_date == "2027-03-01"
    assert DUE_DATE_TEXT == "March 1, 2027"


def test_compute_chooses_authorized_when_lower():
    # tiny cap table, big assets: authorized method ($175) beats assumed par
    result = compute_franchise_tax(
        authorized_shares=5_000, issued_shares=5_000,
        gross_assets_cents=50_000_000_00, annual_report_fee=FEE,
    )
    assert result.chosen == "authorized"
    assert result.total_cents == 175_00 + 50_00


def test_fee_is_an_input_not_a_constant():
    base = dict(authorized_shares=10_000_000, issued_shares=8_000_000, gross_assets_cents=500_000_00)
    with_100 = compute_franchise_tax(**base, annual_report_fee=FeeQuote(10_000, "apify"))
    assert with_100.total_cents == 500_00
    assert with_100.fee_source == "apify"
    with pytest.raises(TypeError):
        compute_franchise_tax(**base)  # no default fee
    with pytest.raises(ValidationError):
        compute_franchise_tax(**base, annual_report_fee=5000)
    with pytest.raises(ValidationError):
        FeeQuote(5000, "")
    with pytest.raises(TypeError):
        FeeQuote(50.0, "snapshot")


def test_result_matches_the_contract_sample_event():
    sample = next(
        json.loads(line) for line in SAMPLE.read_text(encoding="utf-8").splitlines()
        if line.strip() and json.loads(line)["type"] == "tax.computed"
    )["payload"]
    result = compute_franchise_tax(
        authorized_shares=10_000_000, issued_shares=8_000_000,
        gross_assets_cents=500_000_00, annual_report_fee=FEE,
    ).as_dict()
    for key, value in result.items():
        assert sample[key] == value, key
    # the gateway adds the two flags; everything else comes from the engine
    assert set(sample) - set(result) == {"form5472_flag", "rnd_flag"}


@pytest.mark.parametrize(
    "cents, text",
    [(85_165_00, "$85,165"), (450_00, "$450"), (50_50, "$50.50"), (0, "$0"), (-400_00, "-$400")],
)
def test_format_usd(cents, text):
    assert format_usd(cents) == text
