"""Contract v1.15 (design D35): the case's tax documents, built and checked by the gateway.

- The workpaper (.xlsx): Inputs (each fact with its verbatim quote), Authorized Shares Method
  (the 8 Del. C. §503 tier table), Assumed Par Value Capital Method (the formula, step by
  step), Comparison (the lower tax + the annual report fee = the total) and Filing (due
  date, late penalty and interest).
- The memo (.docx): summary, both methods as tables, why the lower one, the citations
  (8 Del. C. §503; the Delaware Division of Corporations' franchise tax instructions), the
  next steps and the disclaimer.
- A books entry appended to docs/U-Tax_Books.xlsx.

Every figure comes from the engine's stored result and the extracted facts, and nothing
else. `verify` re-opens each file and checks every figure against a fresh engine run on
the same facts: a mismatch is never sent.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Callable

from docx import Document
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font

from app.engine.franchise import (
    APV_MIN_CENTS, APV_RATE_CENTS, APV_STEP_DOLLARS, AUTH_STEP_CENTS, AUTH_STEP_SHARES,
    AUTH_UP_TO_5K_CENTS, AUTH_UP_TO_10K_CENTS, DEFAULT_STATED_PAR, MAX_TAX_CENTS,
    assumed_par_method_cents, assumed_par_value_capital, authorized_shares_method_cents)
from app.engine.money import format_usd

DISCLAIMER = "Educational estimate — not tax advice."
CITATIONS = ("8 Del. C. § 503 (franchise tax rates and the assumed par value capital method)",
             "Delaware Division of Corporations, Annual Franchise Tax Report instructions")
LATE_PENALTY_CENTS = 200_00
LATE_INTEREST = "1.5% per month on the unpaid tax"
MONEY = '"$"#,##0.00'
FACT_LABELS = {"incorporated_delaware": "Delaware corporation", "authorized_shares":
               "Authorized shares", "issued_shares": "Issued shares", "gross_assets_cents":
               "Total gross assets", "stated_par": "Stated par value per share",
               "foreign_owner_25": "A foreign owner holds 25% or more"}


@dataclass(frozen=True)
class Figures:
    """Every number the documents carry, from the stored result and the facts."""

    authorized: int
    issued: int
    assets_cents: int
    stated_par: Decimal
    assumed_par: Fraction
    capital: Fraction
    auth_cents: int
    apv_cents: int
    fee_cents: int
    total_cents: int
    chosen: str
    due_date: str

    @property
    def tax_cents(self) -> int:
        return min(self.auth_cents, self.apv_cents)


def figures(facts: dict, result: dict) -> Figures:
    authorized, issued = int(facts["authorized_shares"]), int(facts["issued_shares"])
    assets = int(facts["gross_assets_cents"])
    par = Decimal(str(facts.get("stated_par") or DEFAULT_STATED_PAR))
    capital = assumed_par_value_capital(authorized, issued, assets, par)
    return Figures(authorized=authorized, issued=issued, assets_cents=assets, stated_par=par,
                   assumed_par=Fraction(assets, 100 * issued), capital=capital,
                   auth_cents=int(result["authorized_method_cents"]),
                   apv_cents=int(result["assumed_par_method_cents"]),
                   fee_cents=int(result["annual_report_fee_cents"]),
                   total_cents=int(result["total_cents"]), chosen=str(result["chosen"]),
                   due_date=str(result["due_date"]))


def engine_figures(facts: dict, fee_cents: int) -> dict[str, int]:
    """What the engine says now, from the facts (and the quoted fee): the yardstick."""
    authorized, issued = int(facts["authorized_shares"]), int(facts["issued_shares"])
    assets = int(facts["gross_assets_cents"])
    par = Decimal(str(facts.get("stated_par") or DEFAULT_STATED_PAR))
    auth = authorized_shares_method_cents(authorized)
    apv = assumed_par_method_cents(authorized, issued, assets, par)
    return {"auth_cents": auth, "apv_cents": apv, "tax_cents": min(auth, apv),
            "fee_cents": fee_cents, "total_cents": min(auth, apv) + fee_cents}


def _usd(cents: int) -> float:
    return cents / 100


def _dollars_text(value: Fraction) -> str:
    return f"${math.floor(value * 100) / 100:,.2f}" if value < 1000 else f"${float(value):,.2f}"


# ---------------------------------------------------------------- the workpaper (.xlsx)


def build_workpaper(path: Path, label: str, facts: dict, quotes: dict, f: Figures,
                    step: Callable[[str], None]) -> dict[str, tuple[str, str, int]]:
    """Write the workpaper; return {check name: (sheet, cell, expected cents)}."""
    checks: dict[str, tuple[str, str, int]] = {}
    wb = Workbook()
    ws = wb.active
    ws.title = "Inputs"
    ws.append([f"U-Tax workpaper: {label}", "", ""])
    ws["A1"].font = Font(bold=True, size=13)
    ws.append(["Fact", "Value", "From the client's email (verbatim)"])
    for key in ("incorporated_delaware", "authorized_shares", "issued_shares",
                "gross_assets_cents", "stated_par", "foreign_owner_25"):
        value = facts.get(key)
        if key == "gross_assets_cents":
            shown: object = _usd(int(value))
        elif isinstance(value, bool):
            shown = "yes" if value else "no"
        else:
            shown = value if value is not None else ""
        quote_key = "gross_assets" if key == "gross_assets_cents" else key
        ws.append([FACT_LABELS[key], shown, quotes.get(quote_key, "assumed" if key ==
                                                          "stated_par" else "")])
        if key == "gross_assets_cents":
            ws.cell(ws.max_row, 2).number_format = MONEY
    step(f"Inputs: {len(FACT_LABELS)} facts, each with the client's own words")

    ws = wb.create_sheet("Authorized Shares Method")
    ws.append(["8 Del. C. § 503: authorized shares method", "", ""])
    ws.append(["Authorized shares", "Tax", ""])
    ws.append(["5,000 or fewer", _usd(AUTH_UP_TO_5K_CENTS), ""])
    ws.append(["5,001 to 10,000", _usd(AUTH_UP_TO_10K_CENTS), ""])
    ws.append([f"each additional {AUTH_STEP_SHARES:,} or part", _usd(AUTH_STEP_CENTS), ""])
    ws.append(["maximum", _usd(MAX_TAX_CENTS), ""])
    extra = max(0, math.ceil((f.authorized - 10_000) / AUTH_STEP_SHARES))
    ws.append([])
    ws.append(["This company's authorized shares", f.authorized, ""])
    ws.append(["Additional 10,000-share blocks (rounded up)", extra if f.authorized > 10_000
               else 0, ""])
    ws.append(["Tax by this method", _usd(f.auth_cents),
               "$250 + $85 × blocks (capped)" if f.authorized > 10_000 else "flat tier"])
    checks["auth"] = ("Authorized Shares Method", f"B{ws.max_row}", f.auth_cents)
    for row in ws.iter_rows(min_row=3, max_row=6, min_col=2, max_col=2):
        row[0].number_format = MONEY
    ws.cell(ws.max_row, 2).number_format = MONEY
    step(f"Authorized Shares Method: {f.authorized:,} shares → {format_usd(f.auth_cents)}")

    ws = wb.create_sheet("Assumed Par Value Capital")
    ws.append(["8 Del. C. § 503: assumed par value capital method", "", ""])
    ws.append(["Step", "Value", "How"])
    ws.append(["1. Total gross assets", _usd(f.assets_cents), "from the client's email"])
    ws.cell(ws.max_row, 2).number_format = MONEY
    ws.append(["2. Issued shares", f.issued, "from the client's email"])
    ws.append(["3. Assumed par = assets ÷ issued shares", float(f.assumed_par), "step 1 ÷ step 2"])
    ws.append(["4. Stated par", float(f.stated_par), "assumed if not stated"])
    ws.append(["5. Par used = the larger of 3 and 4", float(max(f.assumed_par,
                                                                 Fraction(f.stated_par))),
               "max(step 3, step 4)"])
    ws.append(["6. Assumed par value capital = par used × authorized shares",
               float(f.capital), "step 5 × authorized shares"])
    units = max(1, math.ceil(f.capital / APV_STEP_DOLLARS))
    ws.append([f"7. Units of ${APV_STEP_DOLLARS:,} (rounded up)", units, "step 6 ÷ 1,000,000"])
    ws.append([f"8. Tax = units × ${APV_RATE_CENTS // 100} (minimum ${APV_MIN_CENTS // 100})",
               _usd(f.apv_cents), "step 7 × $400"])
    ws.cell(ws.max_row, 2).number_format = MONEY
    checks["apv"] = ("Assumed Par Value Capital", f"B{ws.max_row}", f.apv_cents)
    step(f"Assumed Par Value Capital Method: {format_usd(f.assets_cents)} ÷ {f.issued:,} "
         f"issued × {f.authorized:,} authorized = {_dollars_text(f.capital)} → "
         f"{format_usd(f.apv_cents)}")

    ws = wb.create_sheet("Comparison")
    ws.append(["Method", "Tax", ""])
    ws.append(["Authorized shares method", _usd(f.auth_cents), ""])
    ws.append(["Assumed par value capital method", _usd(f.apv_cents), ""])
    ws.append(["Tax owed: the lower of the two", _usd(f.tax_cents), ""])
    checks["tax"] = ("Comparison", f"B{ws.max_row}", f.tax_cents)
    ws.append(["+ Annual report fee", _usd(f.fee_cents), ""])
    checks["fee"] = ("Comparison", f"B{ws.max_row}", f.fee_cents)
    ws.append(["Total due", _usd(f.total_cents), ""])
    checks["total"] = ("Comparison", f"B{ws.max_row}", f.total_cents)
    for row in ws.iter_rows(min_row=2, max_row=ws.max_row, min_col=2, max_col=2):
        row[0].number_format = MONEY
    step(f"Comparison: {format_usd(f.tax_cents)} + {format_usd(f.fee_cents)} annual report "
         f"fee = {format_usd(f.total_cents)}")

    ws = wb.create_sheet("Filing")
    ws.append(["Due date", f.due_date, "March 1 each year (annual report + franchise tax)"])
    ws.append(["Late filing penalty", _usd(LATE_PENALTY_CENTS), "if filed or paid late"])
    ws.cell(ws.max_row, 2).number_format = MONEY
    ws.append(["Interest", LATE_INTEREST, ""])
    ws.append([DISCLAIMER, "", ""])
    step(f"Filing: due {f.due_date}; late: {format_usd(LATE_PENALTY_CENTS)} + {LATE_INTEREST}")
    for sheet in wb.worksheets:
        sheet.column_dimensions["A"].width = 52
        sheet.column_dimensions["B"].width = 18
        sheet.column_dimensions["C"].width = 48
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp.xlsx")
    wb.save(tmp)
    tmp.replace(path)
    return checks


# ---------------------------------------------------------------- the memo (.docx)


def build_memo(path: Path, label: str, f: Figures,
               step: Callable[[str], None]) -> list[str]:
    """Write the memo; return the figure texts it must carry."""
    doc = Document()
    doc.add_heading("Delaware franchise tax: your estimate", level=1)
    doc.add_paragraph(f"Prepared for: {label}. Prepared by: U-Tax (Accounting).")
    better = "assumed par value capital" if f.chosen == "assumed_par" else "authorized shares"
    doc.add_paragraph(
        f"Summary: by the {better} method your 2026 Delaware franchise tax is "
        f"{format_usd(f.tax_cents)} instead of {format_usd(max(f.auth_cents, f.apv_cents))}. "
        f"With the {format_usd(f.fee_cents)} annual report fee, the total due by {f.due_date} "
        f"is {format_usd(f.total_cents)}.")
    step(f"Memo: summary ({format_usd(f.tax_cents)} instead of "
         f"{format_usd(max(f.auth_cents, f.apv_cents))})")
    doc.add_heading("Both methods", level=2)
    table = doc.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text, table.rows[0].cells[1].text = "Method", "Tax"
    for name, cents in (("Authorized shares method", f.auth_cents),
                        ("Assumed par value capital method", f.apv_cents)):
        row = table.add_row().cells
        row[0].text, row[1].text = name, format_usd(cents)
    detail = doc.add_table(rows=1, cols=2)
    detail.rows[0].cells[0].text, detail.rows[0].cells[1].text = "Assumed par value capital", "Value"
    for name, value in (("Authorized shares", f"{f.authorized:,}"),
                        ("Issued shares", f"{f.issued:,}"),
                        ("Total gross assets", format_usd(f.assets_cents)),
                        ("Assumed par value capital", _dollars_text(f.capital))):
        row = detail.add_row().cells
        row[0].text, row[1].text = name, value
    step("Memo: both methods as tables, and why the lower one")
    doc.add_heading("Why this method", level=2)
    doc.add_paragraph(
        "Delaware lets a corporation pay the lower of the two methods. The authorized shares "
        "method charges by how many shares the charter authorizes; the assumed par value "
        "capital method uses your gross assets and issued shares, which is usually far lower "
        "for a startup with many authorized shares and modest assets.")
    doc.add_heading("Sources", level=2)
    for citation in CITATIONS:
        doc.add_paragraph(citation, style="List Bullet")
    doc.add_heading("Next steps", level=2)
    for line in (f"File the annual report and pay {format_usd(f.total_cents)} by {f.due_date} "
                 "on the Delaware Division of Corporations site.",
                 "Report your gross assets and issued shares there so the assumed par value "
                 "capital method applies.",
                 f"Late filing adds a {format_usd(LATE_PENALTY_CENTS)} penalty plus interest "
                 f"at {LATE_INTEREST}."):
        doc.add_paragraph(line, style="List Number")
    doc.add_paragraph(DISCLAIMER)
    step("Memo: 8 Del. C. § 503, next steps and the disclaimer")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp.docx")
    doc.save(tmp)
    tmp.replace(path)
    return [format_usd(f.auth_cents), format_usd(f.apv_cents), format_usd(f.tax_cents),
            format_usd(f.fee_cents), format_usd(f.total_cents), DISCLAIMER, CITATIONS[0]]


# ---------------------------------------------------------------- the books


BOOKS_HEADER = ["Entry", "Date", "Client", "Service", "U-Tax fee", "Franchise tax (client)",
                "Annual report fee", "Total due (client)", "Tax saved (client)"]


def append_books(path: Path, entry: str, date: str, label: str, f: Figures) -> int:
    """One row per case (`entry` is the case id): a memo v2 updates that row in place, so
    "tax saved" never counts a case twice. Returns the row."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        wb = load_workbook(path)
        ws = wb.active
    else:
        wb = Workbook()
        ws = wb.active
        ws.title = "Books"
        ws.append(BOOKS_HEADER)
    values = [entry, date, label, "Delaware franchise tax review", 0, _usd(f.tax_cents),
              _usd(f.fee_cents), _usd(f.total_cents),
              _usd(max(f.auth_cents, f.apv_cents) - f.tax_cents)]
    row = next((r[0].row for r in ws.iter_rows(min_row=2) if r[0].value == entry), None)
    if row is None:
        ws.append(values)
        row = ws.max_row
    else:
        for col, value in enumerate(values, start=1):
            ws.cell(row, col).value = value
    for col in (5, 6, 7, 8, 9):
        ws.cell(row, col).number_format = MONEY
    tmp = path.with_suffix(".tmp.xlsx")
    wb.save(tmp)
    tmp.replace(path)
    return row


def shape(path: Path) -> int:
    """doc.created's pages_or_sheets: a workbook's sheets, a memo's pages (estimated)."""
    if path.suffix == ".xlsx":
        wb = load_workbook(path, read_only=True)  # holds the file open until closed (Windows)
        try:
            return len(wb.sheetnames)
        finally:
            wb.close()
    words = sum(len(p.text.split()) for p in Document(path).paragraphs)
    return max(1, math.ceil(words / 450))


# ---------------------------------------------------------------- verification


def verify_workpaper(path: Path, checks: dict[str, tuple[str, str, int]],
                     truth: dict[str, int]) -> list[str]:
    """Re-open the file: each checked cell must equal the engine's figure (in cents)."""
    want = {"auth": truth["auth_cents"], "apv": truth["apv_cents"], "tax": truth["tax_cents"],
            "fee": truth["fee_cents"], "total": truth["total_cents"]}
    wb = load_workbook(path, data_only=True)
    failed = []
    for name, (sheet, cell, written) in checks.items():
        value = wb[sheet][cell].value
        got = round(Decimal(str(value)) * 100) if isinstance(value, (int, float)) else None
        expected = want[name]
        if got != expected or written != expected:
            failed.append(name)
    return failed


def verify_memo(path: Path, must_carry: list[str], truth: dict[str, int]) -> list[str]:
    doc = Document(path)
    text = "\n".join(p.text for p in doc.paragraphs)
    text += "\n" + "\n".join(c.text for t in doc.tables for r in t.rows for c in r.cells)
    failed = [item for item in must_carry if item not in text]
    for name in ("auth_cents", "apv_cents", "tax_cents", "total_cents"):
        if format_usd(truth[name]) not in text:
            failed.append(name)
    return failed


def verify_books(path: Path, row: int, f: Figures, truth: dict[str, int]) -> list[str]:
    ws = load_workbook(path, data_only=True).active
    tax, total = ws.cell(row, 6).value, ws.cell(row, 8).value
    failed = []
    if not isinstance(tax, (int, float)) or round(Decimal(str(tax)) * 100) != truth["tax_cents"]:
        failed.append("books_tax")
    if not isinstance(total, (int, float)) or             round(Decimal(str(total)) * 100) != truth["total_cents"]:
        failed.append("books_total")
    return failed
