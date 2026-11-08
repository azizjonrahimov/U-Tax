"""D42 reference: fill the official IRS Form 1120 (2025 revision) as a DRAFT, and return each
filled field's position so the office can animate a cursor typing into the real form.

The blank form is downloaded once (owner's machine has internet):
  Invoke-WebRequest https://www.irs.gov/pub/irs-pdf/f1120.pdf -OutFile backend\\assets\\forms\\f1120_2025.pdf
Field names below were mapped from that file (2025 revision, 6 pages) by matching each field's
rectangle to the line label printed beside it (map_fields.py). If the IRS posts a new revision,
re-run map_fields.py and update FIELDS; fill() refuses a file whose page-1 title is not 2025.
"""
from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject, NumberObject

# line -> (page index, field name). Amount fields are whole dollars, no "$".
FIELDS = {
    "name": (0, "f1_4[0]"), "street": (0, "f1_5[0]"), "city": (0, "f1_7[0]"),
    "state": (0, "f1_8[0]"), "country": (0, "f1_9[0]"), "zip": (0, "f1_10[0]"),
    "ein": (0, "f1_11[0]"), "date_incorporated": (0, "f1_12[0]"), "total_assets": (0, "f1_13[0]"),
    "1a": (0, "f1_14[0]"), "1b": (0, "f1_15[0]"), "1c": (0, "f1_16[0]"), "2": (0, "f1_17[0]"),
    "3": (0, "f1_18[0]"), "10": (0, "f1_25[0]"), "11": (0, "f1_26[0]"), "26": (0, "f1_41[0]"),
    "27": (0, "f1_42[0]"), "28": (0, "f1_43[0]"), "30": (0, "f1_47[0]"), "31": (0, "f1_48[0]"),
    "35": (0, "f1_52[0]"),
    "J1a": (2, "f3_1[0]"), "J2": (2, "f3_9[0]"), "J4": (2, "f3_11[0]"), "J7": (2, "f3_19[0]"),
    "J11a": (2, "f3_30[0]"), "J12": (2, "f3_33[0]"),
}
LABELS = {
    "name": "Name", "street": "Number, street", "city": "City", "state": "State",
    "country": "Country", "zip": "ZIP", "ein": "B  Employer identification number",
    "date_incorporated": "C  Date incorporated", "total_assets": "D  Total assets",
    "1a": "1a Gross receipts or sales", "1b": "1b Returns and allowances", "1c": "1c Balance",
    "2": "2 Cost of goods sold", "3": "3 Gross profit", "10": "10 Other income",
    "11": "11 Total income", "26": "26 Other deductions", "27": "27 Total deductions",
    "28": "28 Taxable income before NOL", "30": "30 Taxable income", "31": "31 Total tax",
    "35": "35 Amount owed", "J1a": "Sch. J 1a Income tax", "J2": "Sch. J 2 Total income tax",
    "J4": "Sch. J 4", "J7": "Sch. J 7", "J11a": "Sch. J 11a", "J12": "Sch. J 12 Total tax",
}
# the order the cursor fills them in (header first, then page 1 top-down, then Schedule J)
ORDER = ["name", "street", "city", "state", "zip", "ein", "1a", "1b", "1c", "2", "3", "10",
         "11", "26", "27", "28", "30", "J1a", "J2", "J4", "J7", "J11a", "J12", "31", "35"]


def _fmt(key: str, v) -> str:
    if isinstance(v, int):
        return f"{v:,}" if v >= 0 else f"({-v:,})"
    return str(v)


def fill(blank: Path, out: Path, header: dict, lines: dict) -> list[dict]:
    """Write the filled DRAFT to `out`; return the fill steps in ORDER:
    [{key, label, value, page, rect: [x0, y0, x1, y1] as fractions of the page, top-left origin}].
    `header` keys: name, street, city, state, zip, ein (masked, e.g. "XX-XXX1234").
    """
    reader = PdfReader(str(blank))
    title = (reader.pages[0].extract_text() or "")[:400]
    if "2025" not in title or "1120" not in title:
        raise ValueError("not the 2025 Form 1120: re-run map_fields.py for the new revision")
    writer = PdfWriter(clone_from=reader)
    # IRS forms also carry XFA; viewers that prefer XFA would show the blank form, so drop it
    acro = writer._root_object["/AcroForm"]
    if "/XFA" in acro:
        del acro["/XFA"]
    values = {**{k: v for k, v in header.items() if k in FIELDS}, **{k: v for k, v in lines.items() if k in FIELDS}}
    by_page: dict[int, dict[str, str]] = {}
    for key, v in values.items():
        page, name = FIELDS[key]
        by_page.setdefault(page, {})[name] = _fmt(key, v)
    for page, vals in by_page.items():
        writer.update_page_form_field_values(writer.pages[page], vals, auto_regenerate=True)
    writer.set_need_appearances_writer(True)
    # positions for the animation
    rects: dict[str, tuple[int, list[float]]] = {}
    for pi, page in enumerate(reader.pages):
        w, h = float(page.mediabox.width), float(page.mediabox.height)
        for a in page.get("/Annots") or []:
            a = a.get_object()
            t = a.get("/T")
            if t is None:
                continue
            x0, y0, x1, y1 = (float(v) for v in a["/Rect"])
            rects[(pi, str(t))] = [round(x0 / w, 4), round(1 - y1 / h, 4), round(x1 / w, 4), round(1 - y0 / h, 4)]
    with open(out, "wb") as f:
        writer.write(f)
    steps = []
    for key in ORDER:
        if key in values:
            page, name = FIELDS[key]
            steps.append({"key": key, "label": LABELS[key], "value": _fmt(key, values[key]),
                          "page": page, "rect": rects.get((page, name))})
    return steps


def render_pages(pdf: Path, out_dir: Path, pages=(0, 2), scale: float = 2.0) -> list[Path]:
    """PNG of each page (the office shows these under the animated values)."""
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(str(pdf))
    doc.init_forms()  # without this pdfium draws the blank form
    out = []
    for i in pages:
        img = doc[i].render(scale=scale, may_draw_forms=True).to_pil()
        p = out_dir / f"{pdf.stem}_p{i + 1}.png"
        img.save(p)
        out.append(p)
    return out
