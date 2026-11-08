"""D42 helper: print every fillable field of a PDF form with the label printed beside it.

  python map_fields.py path\\to\\form.pdf [page_numbers...]

Use it to build the FIELDS table for a new form (CA Form 100, NY CT-3, ...) the same way
form1120.FIELDS was built. Needs: pip install pdfplumber pypdf
"""
import sys

import pdfplumber
from pypdf import PdfReader


def main(path: str, pages: list[int]) -> None:
    reader, plumb = PdfReader(path), pdfplumber.open(path)
    for pi in pages or range(len(reader.pages)):
        page, H = plumb.pages[pi], float(plumb.pages[pi].height)
        words = page.extract_words(keep_blank_chars=True, use_text_flow=True)
        for a in reader.pages[pi].get("/Annots") or []:
            a = a.get_object()
            if a.get("/FT") not in ("/Tx", "/Btn") or a.get("/T") is None:
                continue
            x0, y0, x1, y1 = (float(v) for v in a["/Rect"])
            top, bot = H - y1, H - y0
            label = " ".join(w["text"] for w in sorted(
                (w for w in words if w["top"] >= top - 3 and w["bottom"] <= bot + 3 and w["x1"] < x0),
                key=lambda w: w["x0"]))
            print(f"p{pi} {a['/FT']} {a['/T']:<12} x={x0:.0f} y={y0:.0f} | {label[-100:]}")


if __name__ == "__main__":
    main(sys.argv[1], [int(p) - 1 for p in sys.argv[2:]])
