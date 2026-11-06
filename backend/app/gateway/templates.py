"""Client-facing text. Every word a client receives comes from here, filled only with
engine output and fixed labels; nothing an agent writes reaches a client mail.

The memo template is `naive/templates/memo.md` (agents lane, contract §7) with a
built-in fallback. A template without the disclaimer line is refused.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.engine import DUE_DATE_TEXT, format_usd

DISCLAIMER = "Educational estimate — not tax advice."
MEMO_SUBJECT = "Your Delaware franchise tax estimate"
PLACEHOLDERS = ("masked_name", "authorized_method", "assumed_par_method", "annual_report_fee",
                "total", "due_date", "form5472_note", "rnd_note")

BUILTIN_MEMO = (
    "Hi {{masked_name}},\n"
    "Using the assumed-par-value method instead of the authorized-shares method, your "
    "estimated 2026 Delaware franchise tax drops from {{authorized_method}} to "
    "{{assumed_par_method}} (+{{annual_report_fee}} annual report fee = {{total}}), "
    "due {{due_date}}.\n"
    "{{form5472_note}}\n"
    "{{rnd_note}}\n"
    f"{DISCLAIMER}"
)

FORM5472_NOTE = ("Heads-up: with a 25%+ foreign owner, your company likely has to file IRS "
                 "Form 5472 with its federal return; the penalty for missing it is $25,000 "
                 "per form per year.")
RND_NOTE_YES = ("You may also qualify to apply up to $500,000 a year of R&D credit against "
                "payroll taxes; it has to be elected on your original return.")
RND_NOTE_MAYBE = ("If your gross receipts are under $5M and you've had receipts for 5 years "
                  "or less, you may qualify to apply up to $500,000 a year of R&D credit "
                  "against payroll taxes (elected on your original return).")

FACT_LABELS = {
    "incorporated_delaware": "whether you're incorporated in Delaware",
    "authorized_shares": "authorized shares",
    "issued_shares": "issued shares",
    "stated_par": "the stated par value per share",
    "gross_assets": "projected Dec 31 gross assets",
    "foreign_owner_25": "whether a foreign owner holds 25%+",
    "gross_receipts": "this year's gross receipts (optional)",
    "years_with_receipts": "how many years you've had receipts (optional)",
}

_TOKEN = re.compile(r"\{\{\s*([a-z0-9_]+)\s*\}\}")


class TemplateError(ValueError):
    pass


def _join(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"


def load_memo_template(path: Path | None) -> tuple[str, str]:
    """(template text, source) where source is the file path or "builtin"."""
    if path is not None and path.exists():
        return path.read_text(encoding="utf-8"), str(path)
    return BUILTIN_MEMO, "builtin"


def render_memo(template: str, *, masked_name: str, result: dict, form5472: bool,
                rnd: bool, rnd_known: bool) -> str:
    unknown = sorted(set(_TOKEN.findall(template)) - set(PLACEHOLDERS))
    if unknown:
        raise TemplateError(f"memo template uses unknown placeholders: {unknown}")
    values = {
        "masked_name": masked_name,
        "authorized_method": format_usd(result["authorized_method_cents"]),
        "assumed_par_method": format_usd(result["assumed_par_method_cents"]),
        "annual_report_fee": format_usd(result["annual_report_fee_cents"]),
        "total": format_usd(result["total_cents"]),
        "due_date": DUE_DATE_TEXT,
        "form5472_note": FORM5472_NOTE if form5472 else "",
        "rnd_note": RND_NOTE_YES if rnd else ("" if rnd_known else RND_NOTE_MAYBE),
    }
    kept = []
    for line in template.splitlines():
        only = _TOKEN.fullmatch(line.strip())
        if only and only.group(1) in ("form5472_note", "rnd_note") and not values[only.group(1)]:
            continue  # a note line with nothing to say disappears
        kept.append(line)
    text = _TOKEN.sub(lambda m: values[m.group(1)], "\n".join(kept)).strip()
    if DISCLAIMER not in text:
        raise TemplateError(f"memo must contain the line {DISCLAIMER!r}")
    return text


def ask_body(missing: list[str], problems: list[str]) -> str:
    wanted = _join([FACT_LABELS.get(f, f.replace("_", " ")) for f in missing])
    first = "Happy to check!"
    if wanted:
        first += f" To estimate your Delaware franchise tax we need: {wanted}."
    if problems:
        first += " Can you double-check: " + "; ".join(problems) + "?"
    return f"{first} No SSNs or EINs please.\n{DISCLAIMER}"


def not_eligible_body() -> str:
    return ("Thanks for reaching out! U-Tax only checks Delaware franchise tax for Delaware "
            "corporations, so we can't estimate yours yet. Come back once you're "
            "incorporated in Delaware and we'll run it for free.\n" + DISCLAIMER)
