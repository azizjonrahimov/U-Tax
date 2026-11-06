"""Client- and CEO-facing text. Every word comes from here, filled only with engine output,
projection counts and fixed labels; nothing an agent writes reaches a mail.

Contract §6d: the gateway loads every `naive/templates/*.md` (names and placeholders in
naive/README.md) with a strict renderer. A file with an unknown placeholder, a missing
required placeholder or a missing required line falls back to the built-in text (logged).
The memo is stricter (contract §7): a memo template without the disclaimer is refused.
A line holding only a placeholder whose value is empty is dropped.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from app.engine import DUE_DATE_TEXT, format_usd

log = logging.getLogger("utax.templates")

DISCLAIMER = "Educational estimate — not tax advice."
AI_SCREEN_LINE = "This screen is run by an AI agent. A human makes the final hiring decision."
SIGN_OFF = "U-Tax, an AI accounting firm run by seven agents and one human"

MEMO_SUBJECT = "Your Delaware franchise tax estimate"
QUESTIONNAIRE_SUBJECT = "U-Tax: a few questions about the gig"
DECISION_SUBJECT = "U-Tax: about the memo review gig"
PLACEHOLDERS = ("masked_name", "authorized_method", "assumed_par_method", "annual_report_fee",
                "total", "due_date", "form5472_note", "rnd_note")

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

NOT_ELIGIBLE_REASONS = ("not_incorporated", "outside_delaware", "not_a_corporation")

ACTION_LINES = {
    "approval": "Approve or Deny it in the console.",
    "blocked": "Unblock or skip it in the console.",
    "flagged": "Have a look at the case in the console.",
    "shortlisted": "Hire or decline in the console.",
    "paused": "Resume it in the console when you're ready.",
}
LEADS = {
    "approval": "An action is waiting for your OK.",
    "blocked": "A case is blocked and needs you.",
    "flagged": "QA flagged a case for you to look at.",
    "shortlisted": "A candidate is shortlisted; the hiring decision is yours.",
    "paused": "Sends of one kind are paused until you resume them.",
}

_TOKEN = re.compile(r"\{\{\s*([a-z0-9_]+)\s*\}\}")


class TemplateError(ValueError):
    pass


@dataclass(frozen=True)
class Spec:
    placeholders: tuple[str, ...]
    required: tuple[str, ...] = ()  # placeholders the file must use
    lines: tuple[str, ...] = ()  # exact lines the rendered text must contain
    builtin: str = ""


_BUILTIN_ASK = (
    "Hi {{masked_name}},\n\n"
    "Happy to check your Delaware franchise tax! To work it out we just need {{missing_list}}.\n\n"
    "{{problems_line}}\n\n"
    "Please don't send SSNs, EINs or any ID numbers. We never need them.\n\n"
    f"{SIGN_OFF}\n\n{DISCLAIMER}"
)
_BUILTIN_NOT_ELIGIBLE = {
    "not_incorporated": "it sounds like you haven't incorporated yet. Come back once you've "
                        "formed a Delaware corporation",
    "outside_delaware": "your company is incorporated outside Delaware. Come back if you ever "
                        "re-domicile in Delaware",
    "not_a_corporation": "Delaware taxes LLCs and partnerships differently (a flat annual tax), "
                         "so our corporation estimate doesn't apply. Come back if you convert to "
                         "a Delaware corporation",
}
SPECS: dict[str, Spec] = {
    "memo": Spec(PLACEHOLDERS, lines=(DISCLAIMER,), builtin=(
        "Hi {{masked_name}},\n"
        "Using the assumed-par-value method instead of the authorized-shares method, your "
        "estimated 2026 Delaware franchise tax drops from {{authorized_method}} to "
        "{{assumed_par_method}} (+{{annual_report_fee}} annual report fee = {{total}}), "
        "due {{due_date}}.\n{{form5472_note}}\n{{rnd_note}}\n" + DISCLAIMER)),
    "ask-client": Spec(("masked_name", "missing_list", "problems_line"),
                       required=("missing_list",), lines=(DISCLAIMER,), builtin=_BUILTIN_ASK),
    **{f"not-eligible-{reason}": Spec(("masked_name",), lines=(DISCLAIMER,), builtin=(
        "Hi {{masked_name}},\n\nThanks for reaching out to U-Tax! We check Delaware franchise "
        f"tax for Delaware corporations, and {why}, and we'll run it for free.\n\n"
        f"{SIGN_OFF}\n\n{DISCLAIMER}")) for reason, why in _BUILTIN_NOT_ELIGIBLE.items()},
    "hr-questionnaire": Spec((), lines=(AI_SCREEN_LINE,), builtin=(
        "Hi there,\n\nThanks for applying to U-Tax! This is a paid 1-hour gig reviewing "
        "Delaware franchise tax memos our AI agents write.\n\n" + AI_SCREEN_LINE + "\n\n"
        "Please reply to this email with:\n"
        "1. Your credential (CPA, EA or similar) and who issued it\n"
        "2. Your experience with Delaware franchise tax or startup tax work\n"
        "3. When you could do one hour this weekend\n"
        "4. Your rate for the hour\n\n"
        "Please don't send SSNs, EINs, ID documents or anything else personal.\n\n" + SIGN_OFF)),
    "hr-hired": Spec((), builtin=(
        "Hi there,\n\nGood news: U-Tax's CEO would like to book you for the paid 1-hour memo "
        "review. Reply with the hour that works best and we'll confirm.\n\n" + SIGN_OFF)),
    "hr-declined": Spec((), builtin=(
        "Hi there,\n\nThank you for applying. Our CEO has decided to go with another reviewer "
        "this time. A person made this decision, not the AI that ran the first screen.\n\n"
        + SIGN_OFF)),
    "ceo-update-email": Spec(("headline", "lead", "summary", "action_line", "console_url"),
                             required=("summary",), builtin=(
        "U-Tax needs you: {{headline}}\n\n{{lead}}\n\n{{summary}}\n\n{{action_line}}\n\n"
        "Open the CEO console: {{console_url}}\n\nU-Tax (sent by the gateway)")),
    "ceo-digest-email": Spec(
        ("period", "clients_new", "memos_sent", "calls_booked", "tax_saved", "agent_spend",
         "touches_training", "touches_operating", "touches_engineering", "pending_approvals",
         "blocked_lines", "hiring_line", "flags_lines", "console_url"), builtin=(
            "Here's what the firm did {{period}}.\n\n"
            "Clients: {{clients_new}} new, {{memos_sent}} memos sent, {{calls_booked}} review "
            "calls booked\nClients saved (estimated): {{tax_saved}}\n"
            "The firm spent an estimated {{agent_spend}} on agents and services\n"
            "Human touches: {{touches_training}} training, {{touches_operating}} operating, "
            "{{touches_engineering}} engineering\n\nWaiting on you:\n{{pending_approvals}}\n\n"
            "Blocked:\n{{blocked_lines}}\n\nHiring:\n{{hiring_line}}\n\n"
            "Flags from QA:\n{{flags_lines}}\n\nOpen the CEO console: {{console_url}}\n\n"
            "U-Tax (sent by the gateway)")),
    "login-brief": Spec(
        ("greeting", "clients_new", "memos_sent", "tax_saved", "agent_spend", "approvals_line",
         "blocked_line", "hiring_line"), builtin=(
            "{{greeting}}. Since you were last here, the firm served {{clients_new}} new "
            "clients and sent {{memos_sent}} memos. Clients saved an estimated {{tax_saved}}, "
            "and the firm spent an estimated {{agent_spend}} on agents and services.\n\n"
            "{{approvals_line}}\n\n{{blocked_line}}\n\n{{hiring_line}}\n\nThat's the brief.")),
}


def _join(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"


def _fill(template: str, values: dict[str, str]) -> str:
    kept = []
    for line in template.splitlines():
        only = _TOKEN.fullmatch(line.strip())
        if only and not values.get(only.group(1), ""):
            continue  # a line holding only an empty placeholder disappears
        kept.append(line)
    text = _TOKEN.sub(lambda m: values.get(m.group(1), ""), "\n".join(kept))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _problems(name: str, template: str, spec: Spec) -> list[str]:
    tokens = set(_TOKEN.findall(template))
    problems = [f"unknown placeholder {t}" for t in sorted(tokens - set(spec.placeholders))]
    problems += [f"missing placeholder {t}" for t in spec.required if t not in tokens]
    return problems


class Templates:
    """Loads naive/templates/<name>.md on every render (so edits apply without a restart)."""

    def __init__(self, directory: Path | None, memo_path: Path | None = None) -> None:
        self.directory = directory
        self.memo_path = memo_path

    def _path(self, name: str) -> Path | None:
        if name == "memo" and self.memo_path is not None:
            return self.memo_path
        return self.directory / f"{name}.md" if self.directory is not None else None

    def source(self, name: str) -> tuple[str, str]:
        spec = SPECS[name]
        path = self._path(name)
        if path is None or not path.exists():
            return spec.builtin, "builtin"
        try:
            return path.read_text(encoding="utf-8"), str(path)
        except (OSError, UnicodeError) as exc:  # mid-edit, or saved in another encoding
            log.warning("template %s unreadable (%s); built-in text sent", path, exc)
            return spec.builtin, "builtin"

    def render(self, name: str, values: dict[str, str]) -> str:
        spec = SPECS[name]
        template, origin = self.source(name)
        problems = _problems(name, template, spec)
        if not problems:
            text = _fill(template, values)
            problems = [f"missing line {line!r}" for line in spec.lines if line not in text]
            if not problems:
                return text
        if name == "memo":  # contract §7: a broken memo file is refused, never replaced
            raise TemplateError(f"memo template {origin}: {'; '.join(problems)}")
        log.warning("template %s (%s) not used: %s; built-in text sent", name, origin,
                    "; ".join(problems))
        return _fill(spec.builtin, values)


# ---------------------------------------------------------------- value builders


def memo_values(*, masked_name: str, result: dict, form5472: bool, rnd: bool,
                rnd_known: bool) -> dict[str, str]:
    return {
        "masked_name": masked_name,
        "authorized_method": format_usd(result["authorized_method_cents"]),
        "assumed_par_method": format_usd(result["assumed_par_method_cents"]),
        "annual_report_fee": format_usd(result["annual_report_fee_cents"]),
        "total": format_usd(result["total_cents"]),
        "due_date": DUE_DATE_TEXT,
        "form5472_note": FORM5472_NOTE if form5472 else "",
        "rnd_note": RND_NOTE_YES if rnd else ("" if rnd_known else RND_NOTE_MAYBE),
    }


def ask_values(masked_name: str, missing: list[str], problems: list[str]) -> dict[str, str]:
    labels = [FACT_LABELS.get(f, f.replace("_", " ")) for f in missing
              if f != "stated_par" or missing == ["stated_par"]]
    return {"masked_name": masked_name, "missing_list": _join(labels) or "a few numbers",
            "problems_line": ("Can you double-check: " + "; ".join(problems) + "?")
            if problems else ""}


def ceo_update_values(reason: str, headline: str, summary: str, console_url: str) -> dict:
    return {"headline": headline, "lead": LEADS.get(reason, ""), "summary": summary,
            "action_line": ACTION_LINES.get(reason, ""), "console_url": console_url}
