"""Fact intake: the AI points at spans, code checks and parses them (decision E3).

`check_facts()` takes the quotes Intake sent and the texts the client actually wrote, and
returns either clean facts or the list of facts to re-ask, with plain-language problems.
Rules:
- every quote must appear verbatim (case and whitespace aside) in the client's text;
- each quote must parse to exactly one number (parse_amount);
- if the client's text gives two different numbers for the same fact ("10 million shares,
  ignore that, use 10"), the fact is conflicting and is re-asked, never guessed;
- values the engine would reject (issued > authorized, 0 issued, $0 assets) are re-asked
  with the specific problem.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.engine import (
    DEFAULT_STATED_PAR,
    FeeQuote,
    ParseError,
    ValidationError,
    compute_franchise_tax,
    parse_amount,
    parse_dollars_to_cents,
    parse_par,
    parse_shares,
)

REQUIRED = ("incorporated_delaware", "authorized_shares", "issued_shares", "gross_assets",
            "foreign_owner_25")
NUMERIC = ("authorized_shares", "issued_shares", "gross_assets", "stated_par",
           "gross_receipts", "years_with_receipts")
OPTIONAL = ("stated_par", "gross_receipts", "years_with_receipts")
BOOLEAN = ("incorporated_delaware", "foreign_owner_25")

# Sanity bounds: beyond these a human looks first (Security: fake numbers / trolls).
MAX_SHARES = 1_000_000_000
MAX_ASSETS_CENTS = 1_000_000_000_00

_NUMBER = re.compile(
    r"(?i)(?<![\w.,])\$?\s?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[a-z]{1,2})?(?![\w])"
    r"(?:\s+(?:thousand|million|mil|billion)\b)?"
    # Linear: "a"/"an" only first, "and" only between number words, so a run of filler
    # words ("a a a ...", 50 kB) can't make every start position rescan the line.
    r"|\b(?:(?:a|an)[\s-]+)?{w}(?:[\s-]+(?:and[\s-]+)?{w})*\b".replace("{w}", (
        r"(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|"
        r"fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|"
        r"seventy|eighty|ninety|hundred|thousand|million|billion)"))
)
_LABELS = {
    "authorized_shares": re.compile(r"(?i)\bauthori[sz]ed\b"),
    "issued_shares": re.compile(r"(?i)\b(issued|outstanding)\b"),
    "gross_assets": re.compile(r"(?i)\bassets?\b"),
}
_SHARE_WORD = re.compile(r"(?i)\b(shares?|authori[sz]ed|issued|outstanding)\b")
_BOUNDARY = re.compile(r"[.;!?\n]|\d")


# Engine wording -> what the client reads in the re-ask (no engine jargon, no "> 0")
PLAIN_PROBLEMS = {
    "assumed par needs issued shares > 0":
        "your email says no shares have been issued: how many shares does the company have "
        "issued today (held by founders and investors)",
    "issued shares can't exceed authorized shares":
        "the issued shares are more than the authorized shares, so could you check both numbers",
    "assumed par needs gross assets above $0":
        "the method that usually saves the most needs your total assets: roughly what were "
        "the company's total assets at the end of last year",
}
PLAIN_BY_FIELD = {
    "issued_shares": "could you check your issued shares",
    "authorized_shares": "could you check your authorized shares",
    "gross_assets": "could you check your total assets",
}


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def quote_found(quote: str, texts: list[str]) -> bool:
    """The quote appears verbatim as whole tokens: "1" is not found in "10,000,000",
    "$500" is not found in "$500,000" (case and whitespace aside)."""
    q = _squash(quote)
    if not q:
        return False
    pattern = re.compile(r"(?<!\w)(?<!\d[,.])" + re.escape(q) + r"(?!\w)(?![,.]\d)")
    return any(pattern.search(_squash(t)) for t in texts)


def _speaks_to(fact: str, text: str) -> bool:
    found = mentions(text)
    if any(m.label == fact for m in found):
        return True
    return fact in ("authorized_shares", "issued_shares") and any(
        m.is_share and m.label is None for m in found)


def _latest_about(fact: str, texts: list[str]) -> str | None:
    """v1.6.2: a later message supersedes earlier ones for a fact it mentions."""
    return next((t for t in reversed(texts) if _speaks_to(fact, t)), None)


def matches_mentions(fact: str, value: Decimal, texts: list[str]) -> bool:
    """The parsed value is a number the client wrote for this fact in their latest message
    that mentions it: one labelled with it, or (when nothing is labelled) one present."""
    latest = _latest_about(fact, texts)
    pool = [latest] if latest is not None else texts
    all_mentions = [m for t in pool for m in mentions(t)]
    labelled = {m.value for m in all_mentions if m.label == fact}
    if labelled:
        return value in labelled
    return value in {m.value for m in all_mentions}


@dataclass(frozen=True)
class Mention:
    value: Decimal
    label: str | None  # authorized_shares | issued_shares | gross_assets | None
    is_share: bool
    is_money: bool
    span: str = ""  # the text as the client wrote it ("10,000,000", "$500k")


def mentions(text: str) -> list[Mention]:
    """Numbers in the client's text, each tagged with the fact its nearby words name."""
    found: list[Mention] = []
    for match in _NUMBER.finditer(text):
        span = match.group(0).strip()
        try:
            value = parse_amount(span)
        except ParseError:
            continue
        # A number's own words stop at the sentence end or the next number, so "use 10
        # shares. 8,000,000 issued" never labels the 10 as issued.
        after = _BOUNDARY.split(text[match.end():match.end() + 30], maxsplit=1)[0]
        before = _BOUNDARY.split(text[max(0, match.start() - 25):match.start()])[-1]
        after_words = " ".join(after.split()[:3])
        before_words = " ".join(before.split()[-2:])
        # "10,000,000 authorized shares, 8,000,000 issued": the words after a number name
        # it; the words before only count when nothing follows it.
        label = next((f for f, p in _LABELS.items() if p.search(after_words)), None)
        if label is None:
            label = next((f for f, p in _LABELS.items() if p.search(before_words)), None)
        if "$" in span and label in ("authorized_shares", "issued_shares"):
            # "8,000,000 issued, $500k.": a dollar amount is never a share count, even when
            # the only nearby label word belongs to the number before it
            label = None
        is_money = "$" in span or label == "gross_assets"
        is_share = not is_money and bool(_SHARE_WORD.search(after_words)
                                         or _SHARE_WORD.search(before_words))
        found.append(Mention(value=value, label=label, is_share=is_share, is_money=is_money,
                             span=span))
    return found


def _conflict(fact: str, value: Decimal, texts: list[str]) -> bool:
    """Does the client's text give another, different number for this fact? Judged within
    one message, the latest that mentions the fact (v1.6.2): a correction in a later
    email supersedes, it is not a conflict."""
    latest = _latest_about(fact, texts)
    all_mentions = mentions(latest) if latest is not None else []
    if fact in ("authorized_shares", "issued_shares"):
        labelled = {m.value for m in all_mentions if m.label == fact}
        unlabelled = {m.value for m in all_mentions if m.is_share and m.label is None}
        if len(labelled) > 1:
            return True
        if value in unlabelled and len(unlabelled) > 1:
            return True
        return False
    if fact == "gross_assets":
        labelled = {m.value for m in all_mentions if m.label == "gross_assets"}
        return len(labelled) > 1
    return False


@dataclass
class FactCheck:
    facts: dict[str, Any] = field(default_factory=dict)
    quotes: dict[str, str] = field(default_factory=dict)
    assumed: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    unrealistic: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.missing


def _parse_fact(fact: str, quote: str) -> Any:
    if fact in ("authorized_shares", "issued_shares", "years_with_receipts"):
        return parse_shares(quote)
    if fact in ("gross_assets", "gross_receipts"):
        return parse_dollars_to_cents(quote)
    if fact == "stated_par":
        return parse_par(quote)
    raise KeyError(fact)


def check_facts(
    *,
    quotes: dict[str, str],
    incorporated_delaware: bool | None,
    foreign_owner_25: bool | None,
    texts: list[str],
) -> FactCheck:
    out = FactCheck()
    labels = {"authorized_shares": "authorized shares", "issued_shares": "issued shares",
              "gross_assets": "gross assets", "stated_par": "stated par",
              "gross_receipts": "gross receipts", "years_with_receipts": "years with receipts"}
    values: dict[str, Any] = {}
    for fact in NUMERIC:
        quote = (quotes.get(fact) or "").strip()
        if not quote:
            if fact not in OPTIONAL:
                out.missing.append(fact)
            continue
        # Problems are fixed wording per fact: the agent's quote never reaches the client.
        if not quote_found(quote, texts):
            out.missing.append(fact)
            out.problems.append(f"we couldn't find your {labels[fact]} in your email")
            continue
        try:
            value = _parse_fact(fact, quote)
        except ParseError:
            out.missing.append(fact)
            out.problems.append(f"your {labels[fact]} isn't one clear number")
            continue
        natural = Decimal(value) / (100 if fact in ("gross_assets", "gross_receipts") else 1)
        if fact in ("authorized_shares", "issued_shares", "gross_assets") and _conflict(
            fact, natural, texts
        ):
            out.missing.append(fact)
            out.problems.append(f"your email gives more than one number for {labels[fact]}")
            continue
        if fact in ("authorized_shares", "issued_shares", "gross_assets") and \
                not matches_mentions(fact, natural, texts):
            out.missing.append(fact)
            out.problems.append(f"we couldn't match your {labels[fact]} to your email")
            continue
        values[fact] = value
        out.quotes[fact] = quote

    for fact, given in (("incorporated_delaware", incorporated_delaware),
                        ("foreign_owner_25", foreign_owner_25)):
        if not isinstance(given, bool):
            out.missing.append(fact)
            continue
        quote = (quotes.get(fact) or "").strip()
        if quote:
            if not quote_found(quote, texts):
                out.missing.append(fact)
                continue  # a yes/no fact is simply re-asked; no agent text goes back
            out.quotes[fact] = quote
        values[fact] = given

    if "stated_par" not in values:
        values["stated_par"] = DEFAULT_STATED_PAR
        out.assumed.append("stated_par")

    # Engine pre-check so a bad combination is re-asked now, not discovered at compute time.
    if {"authorized_shares", "issued_shares", "gross_assets"} <= values.keys():
        try:
            compute_franchise_tax(
                authorized_shares=values["authorized_shares"],
                issued_shares=values["issued_shares"],
                gross_assets_cents=values["gross_assets"],
                stated_par=values["stated_par"],
                annual_report_fee=FeeQuote(0, "precheck"),
            )
        except ValidationError as exc:
            if exc.field not in out.missing:
                out.missing.append(exc.field)
            if exc.field == "issued_shares" and "authorized" in exc.message \
                    and "authorized_shares" not in out.missing:
                out.missing.append("authorized_shares")  # the client checks both
            out.problems.append(PLAIN_PROBLEMS.get(exc.message, PLAIN_BY_FIELD.get(
                exc.field, "one of your numbers doesn't fit with the others")))
        if values["authorized_shares"] > MAX_SHARES or values["issued_shares"] > MAX_SHARES:
            out.unrealistic.append("shares over 1 billion")
        if values["gross_assets"] > MAX_ASSETS_CENTS:
            out.unrealistic.append("gross assets over $1 billion")

    out.facts = {
        "incorporated_delaware": values.get("incorporated_delaware"),
        "authorized_shares": values.get("authorized_shares"),
        "issued_shares": values.get("issued_shares"),
        "stated_par": str(values["stated_par"]),
        "gross_assets_cents": values.get("gross_assets"),
        "foreign_owner_25": values.get("foreign_owner_25"),
    }
    if "gross_receipts" in values:
        out.facts["gross_receipts_cents"] = values["gross_receipts"]
    if "years_with_receipts" in values:
        out.facts["years_with_receipts"] = values["years_with_receipts"]
    # keep REQUIRED order, no duplicates
    seen: set[str] = set()
    out.missing = [f for f in [*REQUIRED, *OPTIONAL] if f in out.missing and not
                   (f in seen or seen.add(f))]
    return out
