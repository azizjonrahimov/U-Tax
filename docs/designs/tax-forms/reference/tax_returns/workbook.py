"""D42 reference: the client's tax workbook, built cell by cell with real Excel formulas.

build(path, facts, returns) writes the .xlsx and returns the cell steps the office animates:
[{sheet, cell, label, value, formula}] in the order a person would type them. `value` is the
engine's own number (the office shows it as the formula's result); a test recalculates the file
in LibreOffice and checks every formula gives that same number.
"""
from __future__ import annotations

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

LIME = PatternFill("solid", fgColor="BEF243")
HEAD = Font(bold=True)
MONEY = '"$"#,##0;[Red]-"$"#,##0'


def build(path, facts: dict, returns, client: str) -> list[dict]:
    wb = Workbook()
    steps: list[dict] = []

    def put(ws, cell, value, label="", formula=None, result=None, money=True, bold=False):
        ws[cell] = formula if formula else value
        if money and (formula or isinstance(value, (int, float))):
            ws[cell].number_format = MONEY
        if bold:
            ws[cell].font = HEAD
        steps.append({"sheet": ws.title, "cell": cell, "label": label,
                      "value": result if formula else value, "formula": formula})

    # Inputs
    ws = wb.active
    ws.title = "Inputs"
    ws.column_dimensions["A"].width = 34
    ws.column_dimensions["B"].width = 18
    put(ws, "A1", f"U-Tax · {client} · tax estimate (draft)", money=False, bold=True)
    rows = [("Entity type", facts["entity_type"], False), ("State", str(facts["state"]).upper(), False),
            ("Gross receipts", int(facts["gross_receipts"]), True),
            ("Returns and allowances", int(facts.get("returns_allowances", 0)), True),
            ("Cost of goods sold", int(facts["cost_of_goods"]), True),
            ("Other income", int(facts.get("other_income", 0)), True),
            ("Total deductions", int(facts["total_deductions"]), True)]
    for i, (label, v, m) in enumerate(rows, start=3):
        put(ws, f"A{i}", label, money=False)
        put(ws, f"B{i}", v, label=label, money=m)
    # Inputs!B5 gross, B6 returns, B7 cogs, B8 other, B9 deductions

    fed = returns.results[0]
    L = fed.lines
    f = wb.create_sheet("Federal 1120")
    f.column_dimensions["A"].width = 40
    f.column_dimensions["B"].width = 18
    put(f, "A1", "Form 1120 (2025 form, draft)", money=False, bold=True)
    fed_rows = [
        ("1c Balance", "=Inputs!B5-Inputs!B6", L["1c"]),
        ("3 Gross profit", "=B3-Inputs!B7", L["3"]),
        ("11 Total income", "=B4+Inputs!B8", L["11"]),
        ("27 Total deductions", "=Inputs!B9", L["27"]),
        ("30 Taxable income", "=B5-B6", L["30"]),
        ("31 Federal tax (21%)", "=ROUND(MAX(B7,0)*0.21,0)", L["31"]),
    ]
    for i, (label, formula, result) in enumerate(fed_rows, start=3):
        put(f, f"A{i}", label, money=False)
        put(f, f"B{i}", None, label=label, formula=formula, result=result, bold=label.startswith("31"))
    f["B8"].fill = LIME

    st = returns.results[1]
    s = wb.create_sheet(f"State {str(facts['state']).upper()}")
    s.column_dimensions["A"].width = 60
    s.column_dimensions["B"].width = 18
    put(s, "A1", f"{st.jurisdiction} · {st.tax} · {st.form or 'no return'}", money=False, bold=True)
    for i, line in enumerate(st.steps, start=3):
        put(s, f"A{i}", line, money=False)
    tot_row = 3 + len(st.steps) + 1
    put(s, f"A{tot_row}", f"{st.jurisdiction} amount due", money=False, bold=True)
    put(s, f"B{tot_row}", st.amount, label=f"{st.jurisdiction} amount due", bold=True)
    s[f"B{tot_row}"].fill = LIME

    sm = wb.create_sheet("Summary")
    sm.column_dimensions["A"].width = 44
    sm.column_dimensions["B"].width = 18
    sm.column_dimensions["C"].width = 22
    put(sm, "A1", "What is due", money=False, bold=True)
    put(sm, "A3", "Federal income tax (Form 1120)", money=False)
    put(sm, "B3", None, label="Federal", formula="='Federal 1120'!B8", result=fed.amount)
    put(sm, "C3", fed.due or "", money=False)
    put(sm, "A4", f"{st.jurisdiction} {st.tax}", money=False)
    put(sm, "B4", None, label=st.jurisdiction, formula=f"='{s.title}'!B{tot_row}", result=st.amount)
    put(sm, "C4", st.due or "", money=False)
    put(sm, "A6", "Total estimated tax", money=False, bold=True)
    put(sm, "B6", None, label="Total", formula="=SUM(B3:B4)", result=fed.amount + st.amount, bold=True)
    sm["B6"].fill = LIME
    for i, a in enumerate(returns.assumptions, start=8):
        sm[f"A{i}"] = a
        sm[f"A{i}"].alignment = Alignment(wrap_text=False)
    wb.save(path)
    return steps
