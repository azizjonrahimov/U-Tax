"use client";

// D42 T3 (runbook §T3): the Accounting desk's "Excel" view. The returns workbook builds cell by cell
// as sheet.cell events arrive (350 ms apart): the cell is selected (lime border), its formula shows
// in the bar, the result types in. Money in $, totals filled lime (as the workbook does), the sheet
// tab follows the cell being written.

import { useState, type CSSProperties } from "react";
import { cellPos, cellText, type Book, type SheetCell } from "@/office/forms";
import { TIMING, usePlayback } from "./DocPlayback";

const COLS = ["A", "B", "C", "D"];
/** The cells the workbook fills lime: the federal tax (line 31), the state amount due, the SUM total. */
const isTotal = (c: SheetCell) => typeof c.value === "number" && (c.label === "Total" || /amount due/i.test(c.label) || /^31 /.test(c.label) || (c.formula ?? "").startsWith("=SUM"));

export function ExcelView({ book, base }: { book: Book; base: number }) {
  const { shown, playing } = usePlayback(book.cells.length, TIMING.sheetCellMs, base);
  const [picked, setPicked] = useState<string | null>(null);
  const on = book.cells.slice(0, shown);
  const active = playing !== null ? book.cells[playing - 1] : undefined;
  // While cells play, the tab follows the cell being written; otherwise the one the viewer picked.
  const sheet = active?.sheet ?? (picked && book.sheets.includes(picked) ? picked : (on.at(-1)?.sheet ?? book.sheets[0]));
  const here = on.filter((c) => c.sheet === sheet);
  const byCell = new Map(here.map((c) => [c.cell, c]));
  const rows = Math.max(12, ...here.map((c) => cellPos(c.cell).row));
  const sel = active && active.sheet === sheet ? active : undefined;

  return (
    <section className="win xv" aria-label={`${book.book} (draft)`}>
      <div className="win-bar">
        <span className="lights" aria-hidden>
          <i />
          <i />
          <i />
        </span>
        <div className="tabs" aria-hidden>
          <span className="tab on">Excel</span>
        </div>
      </div>
      <div className="win-addr">
        <span className="lock" aria-hidden>
          ⌁
        </span>
        <span className="mono addr">{book.book}</span>
        <span className="badge">
          {shown} of {book.of} cells
        </span>
      </div>
      <div className="xv-fx" aria-label="Formula bar">
        <span className="xv-name mono">{sel?.cell ?? ""}</span>
        <span className="xv-fxl" aria-hidden>
          fx
        </span>
        <span className="xv-formula mono">{sel ? (sel.formula ?? cellText(sel.value)) : ""}</span>
      </div>
      <div className="xv-grid-wrap">
        <table className="xv-grid">
          <thead>
            <tr>
              <th aria-hidden />
              {COLS.map((c) => (
                <th key={c} scope="col">
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {Array.from({ length: rows }, (_, i) => i + 1).map((r) => (
              <tr key={r}>
                <th scope="row">{r}</th>
                {COLS.map((col) => {
                  const c = byCell.get(`${col}${r}`);
                  const now = c && sel?.n === c.n;
                  const txt = c ? cellText(c.value) : "";
                  return (
                    <td key={col} className={[c && typeof c.value === "number" ? "num" : "", c && isTotal(c) ? "total" : "", now ? "sel" : ""].join(" ").trim() || undefined} title={c?.formula ?? undefined}>
                      {c && <span className={now ? "xv-text typing" : "xv-text"} style={{ "--n": Math.max(1, txt.length) } as CSSProperties}>{txt}</span>}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="xv-tabs" role="tablist" aria-label="Sheets">
        {book.sheets.map((s) => (
          <button key={s} role="tab" aria-selected={s === sheet} className={s === sheet ? "on" : undefined} onClick={() => setPicked(s)}>
            {s}
          </button>
        ))}
      </div>
    </section>
  );
}
