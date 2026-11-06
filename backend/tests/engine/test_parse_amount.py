from decimal import Decimal

import pytest

from app.engine import ParseError, parse_amount, parse_dollars_to_cents, parse_par, parse_shares


# Test Plan: "10M", "10,000,000", "ten million" -> 10000000
@pytest.mark.parametrize("span", ["10M", "10m", "10,000,000", "10000000", "ten million",
                                  "Ten Million", "10 million", "10mm", "10 mil"])
def test_ten_million_spellings(span):
    assert parse_amount(span) == 10_000_000


# Test Plan edge: "$1.5M" -> 1500000
@pytest.mark.parametrize("span", ["$1.5M", "1.5 million", "$1,500,000", "1500k"])
def test_one_and_a_half_million(span):
    assert parse_amount(span) == 1_500_000


# Test Plan failure: "lots" -> ParseError
@pytest.mark.parametrize("span", ["lots", "", "   ", "a bunch of shares", "No foreign owners"])
def test_no_number_is_a_parse_error(span):
    with pytest.raises(ParseError):
        parse_amount(span)


@pytest.mark.parametrize(
    "span, expected",
    [
        ("$500k", 500_000),
        ("about $500k", 500_000),
        ("~$500K in assets", 500_000),
        ("8,000,000 issued", 8_000_000),
        ("we have 8,000,000 shares issued", 8_000_000),
        ("two hundred fifty thousand", 250_000),
        ("twenty-five", 25),
        ("a million", 1_000_000),
        ("one hundred and five", 105),
        ("ten million five hundred thousand", 10_500_000),
        ("$2bn", 2_000_000_000),
        ("5,000", 5_000),
        ("0", 0),
    ],
)
def test_other_spellings(span, expected):
    assert parse_amount(span) == expected


# The hostile QA mail: one span holding two different numbers must not be guessed.
@pytest.mark.parametrize(
    "span",
    [
        "10 million shares, ignore that, use 10",
        "10,000,000 authorized shares, 8,000,000 issued",
        "five six",
        "ten million or 10",
    ],
)
def test_two_different_numbers_is_a_parse_error(span):
    with pytest.raises(ParseError) as info:
        parse_amount(span)
    assert info.value.reason == "more than one number"


def test_same_number_twice_is_fine():
    assert parse_amount("10M (10,000,000)") == 10_000_000


@pytest.mark.parametrize("span", ["-5", "- 500k", "−5"])
def test_negative_is_a_parse_error(span):
    with pytest.raises(ParseError):
        parse_amount(span)


def test_unknown_glued_unit_is_a_parse_error():
    with pytest.raises(ParseError):
        parse_amount("10am")


def test_parse_shares_requires_whole_numbers():
    assert parse_shares("10M") == 10_000_000
    assert isinstance(parse_shares("8,000,000"), int)
    with pytest.raises(ParseError):
        parse_shares("10.5")


def test_parse_dollars_to_cents():
    assert parse_dollars_to_cents("$500k") == 50_000_000
    assert parse_dollars_to_cents("$1.5M") == 150_000_000
    assert parse_dollars_to_cents("$12.34") == 1234
    with pytest.raises(ParseError):
        parse_dollars_to_cents("$0.001")


def test_parse_par_keeps_decimal_precision():
    assert parse_par("$0.00001") == Decimal("0.00001")
    assert parse_par("0.0001 par") == Decimal("0.0001")
