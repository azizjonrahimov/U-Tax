"""D42 reference engine: federal + state tax estimates for a single-state C-corporation.

Pure and deterministic (no LLM, no I/O). Whole dollars, rounded half up per line, like the
forms. Every result carries `steps` (plain sentences the office animates and the memo quotes)
and `assumptions` (what we simplified; the memo prints them). Anything outside the model
returns status "human_review" with the reason; the gateway then blocks the case for a human.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from .rates import FEDERAL, NY_FDM, NY_FDM_TOP, STATES

REQUIRED = ("entity_type", "state", "gross_receipts", "cost_of_goods", "total_deductions")
# what Intake asks the client for (labels go into the ask email)
FACT_LABELS = {
    "entity_type": "what kind of company it is (C-corp, S-corp, LLC)",
    "state": "the state where the company is based and does business",
    "gross_receipts": "the year's total sales (gross receipts)",
    "cost_of_goods": "the cost of goods sold (0 if you sell software or services)",
    "total_deductions": "the year's total business expenses (salaries, rent, software, etc.)",
}


def dollars(x) -> int:
    return int(Decimal(str(x)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def money(n: int) -> str:
    return f"-${-n:,}" if n < 0 else f"${n:,}"


@dataclass
class TaxResult:
    jurisdiction: str          # "Federal" | "California" | ...
    tax: str                   # "corporate income tax" | "franchise tax" | ...
    form: str | None
    due: str | None
    amount: int                # whole dollars owed (estimate)
    status: str = "ok"         # ok | no_tax_due | human_review
    steps: list[str] = field(default_factory=list)
    lines: dict[str, int] = field(default_factory=dict)   # form line -> value (federal only)
    source: str = ""


@dataclass
class Returns:
    taxable_income: int
    results: list[TaxResult]
    assumptions: list[str]
    missing: list[str]

    @property
    def total(self) -> int:
        return sum(r.amount for r in self.results if r.status != "human_review")

    @property
    def needs_human(self) -> list[str]:
        return [f"{r.jurisdiction}: {r.steps[-1]}" for r in self.results if r.status == "human_review"]


def missing_facts(facts: dict) -> list[str]:
    return [k for k in REQUIRED if facts.get(k) in (None, "")]


def federal(facts: dict) -> TaxResult:
    gross = dollars(facts["gross_receipts"])
    returns_ = dollars(facts.get("returns_allowances", 0))
    cogs = dollars(facts["cost_of_goods"])
    other = dollars(facts.get("other_income", 0))
    ded = dollars(facts["total_deductions"])
    l1c = gross - returns_
    l3 = l1c - cogs
    l11 = l3 + other
    l27 = ded
    l28 = l11 - l27
    l30 = l28                      # no NOL or special deductions modeled
    tax = dollars(Decimal(max(l30, 0)) * Decimal(str(FEDERAL["rate"])))
    lines = {"1a": gross, "1b": returns_, "1c": l1c, "2": cogs, "3": l3, "10": other,
             "11": l11, "26": ded, "27": l27, "28": l28, "30": l30, "31": tax,
             "J1a": tax, "J2": tax, "J4": tax, "J7": tax, "J11a": tax, "J12": tax, "35": tax}
    steps = [f"Gross receipts {money(gross)} − returns {money(returns_)} = {money(l1c)} (line 1c)",
             f"− cost of goods sold {money(cogs)} = gross profit {money(l3)} (line 3)",
             f"+ other income {money(other)} = total income {money(l11)} (line 11)",
             f"− total deductions {money(l27)} = taxable income {money(l30)} (lines 28, 30)",
             (f"21% × {money(l30)} = {money(tax)} federal income tax (line 31)" if l30 > 0 else
              f"Taxable income is {money(l30)}: no federal income tax (a loss carries forward)")]
    return TaxResult("Federal", "corporate income tax", FEDERAL["form"], FEDERAL["due"], tax,
                     "ok" if tax else "no_tax_due", steps, lines, FEDERAL["source"])


def state(facts: dict, taxable_income: int) -> TaxResult:
    code = str(facts["state"]).strip().upper()
    s = STATES.get(code)
    if s is None:
        return TaxResult(code, "state tax", None, None, 0, "human_review",
                         [f"{code} is not covered by the engine yet: a person prepares it"])
    ti = max(taxable_income, 0)
    gross = dollars(facts["gross_receipts"])
    kind = s["kind"]
    if kind == "none":
        return TaxResult(s["name"], "corporate income tax", None, None, 0, "no_tax_due",
                         [f"{s['name']} has no corporate income tax"], source=s["source"])
    if kind == "gross_receipts":
        return TaxResult(s["name"], "gross receipts tax", None, None, 0, "human_review",
                         [f"{s['name']} taxes gross receipts, not income: a person prepares it"],
                         source=s["source"])
    if kind == "income":
        base = max(ti - s.get("exemption", 0), 0)
        tax = dollars(Decimal(base) * Decimal(str(s["rate"])))
        steps = []
        if s.get("exemption"):
            steps.append(f"{money(ti)} − {money(s['exemption'])} exemption = {money(base)}")
        steps.append(f"{s['rate'] * 100:g}% × {money(base)} = {money(tax)}")
        if s.get("minimum"):
            if facts.get("first_year_in_state"):
                steps.append(f"First taxable year in {s['name']}: no {money(s['minimum'])} minimum")
            elif tax < s["minimum"]:
                steps.append(f"Below the {money(s['minimum'])} minimum: {money(s['minimum'])} is due")
                tax = s["minimum"]
        return TaxResult(s["name"], "corporate income tax" if code != "CA" else "corporation franchise tax",
                         s["form"], s["due"], tax, "ok" if tax else "no_tax_due", steps, source=s["source"])
    if kind == "ny":
        rate = s["rate_over_5m"] if ti > 5_000_000 else s["rate"]
        bib = dollars(Decimal(ti) * Decimal(str(rate)))
        receipts = dollars(facts.get("ny_receipts", gross))
        fdm = next((amt for top, amt in NY_FDM if receipts <= top), NY_FDM_TOP)
        tax = max(bib, fdm)
        steps = [f"Business income base: {rate * 100:g}% × {money(ti)} = {money(bib)}",
                 "Capital base: 0 (small business taxpayer)",
                 f"Fixed dollar minimum for {money(receipts)} of NY receipts: {money(fdm)}",
                 f"The highest of the three is due: {money(tax)}"]
        return TaxResult(s["name"], "corporation franchise tax", s["form"], s["due"], tax, "ok",
                         steps, source=s["source"])
    if kind == "tx":
        if gross <= s["no_tax_due"]:
            return TaxResult(s["name"], "franchise (margin) tax", s["form"], s["due"], 0, "no_tax_due",
                             [f"Revenue {money(gross)} is at or under the {money(s['no_tax_due'])} "
                              "no-tax-due threshold: $0 tax; file the No Tax Due and "
                              "Public Information Reports"], source=s["source"])
        cogs = dollars(facts["cost_of_goods"])
        comp = dollars(facts.get("compensation", 0))
        margin = min(dollars(Decimal(gross) * Decimal("0.7")), gross - cogs,
                     gross - comp if comp else gross, gross - 1_000_000)
        rate = s["rate_retail"] if facts.get("retail_wholesale") else s["rate"]
        regular = dollars(Decimal(max(margin, 0)) * Decimal(str(rate)))
        steps = [f"Margin = lowest of 70% of revenue, revenue − COGS, revenue − compensation, "
                 f"revenue − $1M = {money(margin)}",
                 f"{rate * 100:g}% × {money(margin)} = {money(regular)}"]
        tax = regular
        if gross <= s["ez_limit"]:
            ez = dollars(Decimal(gross) * Decimal(str(s["ez_rate"])))
            steps.append(f"EZ computation: 0.331% × {money(gross)} = {money(ez)}")
            if ez < regular:
                tax = ez
                steps.append(f"EZ is lower: {money(tax)} is due")
        return TaxResult(s["name"], "franchise (margin) tax", s["form"], s["due"], tax, "ok",
                         steps, source=s["source"])
    raise AssertionError(kind)


def compute_returns(facts: dict) -> Returns:
    """All taxes for one client. Call only when missing_facts(facts) is empty."""
    missing = missing_facts(facts)
    if missing:
        return Returns(0, [], [], missing)
    if str(facts["entity_type"]).lower().replace("-", "_").replace(" ", "_") not in ("c_corp", "ccorp", "c_corporation"):
        r = TaxResult("Federal", "income tax", None, None, 0, "human_review",
                      [f"{facts['entity_type']} returns (1120-S, 1065, Schedule C) are prepared by a person"])
        return Returns(0, [r], [], [])
    fed = federal(facts)
    ti = fed.lines["30"]
    results = [fed, state(facts, ti)]
    assumptions = [
        "All income is earned in one state (no multi-state apportionment).",
        "State taxable income starts from federal taxable income with no state adjustments.",
        "No net operating loss carryforward, credits or estimated payments are applied.",
        "This is an estimate for review, drafted on the 2025 forms. It is not a filed return.",
    ]
    if str(facts["state"]).upper() == "TX" and facts.get("compensation"):
        assumptions.append("Texas compensation deduction is not capped per person here.")
    return Returns(ti, results, assumptions, [])
