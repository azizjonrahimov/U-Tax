"""AI finds, code parses (decision E3).

Intake quotes the verbatim span where a number appears ("10M", "10,000,000",
"ten million", "$1.5M", "about $500k"); this module turns the span into exactly one
number. A span with no number, or with two different numbers, is a ParseError, so a
vague or conflicting quote leads to a re-ask instead of a guess.
"""

from __future__ import annotations

import re
from decimal import Decimal

from app.engine.errors import ParseError

_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19,
}
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90,
}
# Suffixes glued to a figure: 500k, 10M, 1.5mm, 2bn.
_SUFFIXES = {"k": 10**3, "m": 10**6, "mm": 10**6, "b": 10**9, "bn": 10**9}
# Scale words, after a figure ("10 million") or inside a word number ("ten million").
_SCALE_WORDS = {"thousand": 10**3, "million": 10**6, "mil": 10**6, "billion": 10**9}

_FIGURE = re.compile(
    r"""
    (?<![\w.,])                           # start of a token
    (?P<sign>-\s*)?
    (?P<num>\d{1,3}(?:,\d{3})+|\d+)       # 10,000,000 or 10000000
    (?P<frac>\.\d+)?
    (?P<suffix>[a-z]{1,2})?               # glued suffix
    (?![\w])
    (?:\s+(?P<word>thousand|million|mil|billion)\b)?
    """,
    re.VERBOSE,
)
_WORD = re.compile(r"[a-z]+")


def _normalize(span: str) -> str:
    text = span.lower()
    for dash in ("−", "–"):
        text = text.replace(dash, "-")
    for junk in (" ", "—", "$", "~"):
        text = text.replace(junk, " ")
    # "twenty-five" -> "twenty five" (a hyphen between words, not a minus sign)
    return re.sub(r"(?<=[a-z])-(?=[a-z])", " ", text)


def _figures(span: str, text: str) -> tuple[list[Decimal], str]:
    """Numbers written with digits. Returns their values and the text with them blanked out."""
    values: list[Decimal] = []

    def take(match: re.Match[str]) -> str:
        if match.group("sign"):
            raise ParseError(span, "negative amount")
        suffix, word = match.group("suffix"), match.group("word")
        if suffix and suffix not in _SUFFIXES:
            raise ParseError(span, f"unknown unit {suffix!r}")
        if suffix and word:
            raise ParseError(span, "two units on one number")
        scale = _SUFFIXES[suffix] if suffix else _SCALE_WORDS[word] if word else 1
        base = Decimal(match.group("num").replace(",", "") + (match.group("frac") or ""))
        values.append(base * scale)
        return " " * len(match.group(0))

    return values, _FIGURE.sub(take, text)


def _word_numbers(text: str) -> list[Decimal]:
    """Numbers written in words: "ten million", "two hundred fifty thousand", "a million"."""
    values: list[Decimal] = []
    total = group = 0
    active = article = False

    def flush() -> None:
        nonlocal total, group, active
        if active:
            values.append(Decimal(total + group))
        total = group = 0
        active = False

    for tok in _WORD.findall(text):
        if tok in _UNITS:
            ones_free = group % 10 == 0 and not 10 <= group % 100 < 20
            if active and not ones_free:
                flush()  # "five six" is two numbers
            group += _UNITS[tok]
            active, article = True, False
        elif tok in _TENS:
            if active and group % 100:
                flush()  # "five twenty" is two numbers
            group += _TENS[tok]
            active, article = True, False
        elif tok == "hundred" and (active or article):
            group = (group or 1) * 100
            active, article = True, False
        elif tok in _SCALE_WORDS and (active or article):
            total += (group or 1) * _SCALE_WORDS[tok]
            group = 0
            active, article = True, False
        elif tok == "and" and active:
            continue
        else:
            flush()
            article = tok in ("a", "an")
    flush()
    return values


def parse_amount(span: str) -> Decimal:
    """Turn one quoted span into one number, in natural units (shares, or dollars).

    "10M", "10,000,000" and "ten million" all give 10000000; "$1.5M" gives 1500000.
    """
    if not isinstance(span, str) or not span.strip():
        raise ParseError(str(span), "empty span")
    figures, rest = _figures(span, _normalize(span))
    values = figures + _word_numbers(rest)
    if not values:
        raise ParseError(span, "no number found")
    if len({v.normalize() for v in values}) > 1:
        raise ParseError(span, "more than one number")
    return values[0]


def parse_shares(span: str) -> int:
    """A share count: must be a whole number."""
    value = parse_amount(span)
    if value != value.to_integral_value():
        raise ParseError(span, "share count is not a whole number")
    return int(value)


def parse_dollars_to_cents(span: str) -> int:
    """A dollar amount ("$500k") as integer cents. Fractions of a cent are refused."""
    cents = parse_amount(span) * 100
    if cents != cents.to_integral_value():
        raise ParseError(span, "amount has fractions of a cent")
    return int(cents)


def parse_par(span: str) -> Decimal:
    """A stated par value per share ("$0.00001")."""
    return parse_amount(span)
