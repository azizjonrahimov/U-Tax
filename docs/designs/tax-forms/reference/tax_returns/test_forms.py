import shutil
import subprocess
from pathlib import Path

import pytest
from openpyxl import load_workbook
from pypdf import PdfReader

from tax_returns.engine import compute_returns
from tax_returns.form1120 import fill, render_pages
from tax_returns.workbook import build

BLANK = Path(__file__).parent / "f1120_2025.pdf"
FACTS = {"entity_type": "C-corp", "state": "CA", "gross_receipts": 1_200_000, "cost_of_goods": 200_000,
         "total_deductions": 700_000}


@pytest.mark.skipif(not BLANK.exists(), reason="download the blank Form 1120 first")
def test_fill_1120(tmp_path):
    r = compute_returns(FACTS)
    out = tmp_path / "Form_1120_DRAFT_case_9.pdf"
    steps = fill(BLANK, out, {"name": "Team #9 (draft)", "state": "CA", "ein": "XX-XXX1234"}, r.results[0].lines)
    got = {k.rsplit(".", 1)[-1]: v.get("/V") for k, v in PdfReader(str(out)).get_fields().items()}
    assert got["f1_14[0]"] == "1,200,000" and got["f1_47[0]"] == "300,000" and got["f1_48[0]"] == "63,000"
    assert got["f3_33[0]"] == "63,000"
    assert [s["key"] for s in steps][:3] == ["name", "state", "ein"]
    assert all(s["rect"] and 0 <= s["rect"][0] < s["rect"][2] <= 1 for s in steps)
    pngs = render_pages(out, tmp_path)
    assert all(p.stat().st_size > 50_000 for p in pngs)


def test_workbook_formulas_recalculate(tmp_path):
    r = compute_returns(FACTS)
    path = tmp_path / "U-Tax_Returns_case_9.xlsx"
    steps = build(path, FACTS, r, "Team #9")
    if not shutil.which("soffice"):
        pytest.skip("LibreOffice not installed")
    subprocess.run(["soffice", "--headless", "--convert-to", "xlsx", "--outdir", str(tmp_path / "calc"), str(path)],
                   check=True, capture_output=True, timeout=120)
    wb = load_workbook(tmp_path / "calc" / path.name, data_only=True)
    for s in steps:
        if s["formula"]:
            assert wb[s["sheet"]][s["cell"]].value == s["value"], s
    assert wb["Summary"]["B6"].value == 63_000 + 26_520
