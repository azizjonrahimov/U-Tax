// D42 T3: the draft Form 1120 and the workbook, folded from the log (fixtures from the reference code).

import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { cellPos, cellText, EMPTY_DOCS, foldDocs, lineText, type DocEvent } from "./forms";

const FIXTURE: DocEvent[] = readFileSync("public/fixtures/forms.sample.jsonl", "utf8").split("\n").filter(Boolean).map((l) => JSON.parse(l));

describe("D42 T3 forms and sheets", () => {
  it("the case_9 fixture: 22 form fields with rects and two page images, 41 cells on 4 sheets", () => {
    const d = foldDocs(EMPTY_DOCS, FIXTURE);
    expect(d.form?.fields).toHaveLength(22);
    expect(d.form?.fields.every((f) => f.rect !== null)).toBe(true);
    expect(Object.keys(d.form!.images).sort()).toEqual(["0", "2"]);
    const line31 = d.form!.fields.find((f) => f.key === "31")!;
    expect(lineText(line31)).toEqual({ label: "Line 31 Total tax", value: "$63,000" });
    expect(d.book?.cells).toHaveLength(41);
    expect(d.book?.sheets).toEqual(["Inputs", "Federal 1120", "State CA", "Summary"]);
    const total = d.book!.cells.find((c) => c.sheet === "Summary" && c.cell === "B6")!;
    expect(cellText(total.value)).toBe("$89,520");
    expect(total.formula).toBe("=SUM(B3:B4)");
  });

  it("is idempotent, the same at once or one by one; a new case starts over", () => {
    const once = foldDocs(EMPTY_DOCS, FIXTURE);
    const each = FIXTURE.reduce((s, e) => foldDocs(s, [e]), EMPTY_DOCS);
    expect(each).toEqual(once);
    expect(foldDocs(once, FIXTURE)).toEqual(once);
    const other = foldDocs(once, [{ id: 1, type: "form.step", case_id: "case_10", payload: { doc: "f1120", n: 1, of: 22, label: "Name" } }]);
    expect(other.form?.caseId).toBe("case_10");
    expect(other.form?.fields).toHaveLength(1);
  });

  it("a theater reader's form.step (no rect, no image) and bad paths never draw", () => {
    const d = foldDocs(EMPTY_DOCS, [
      { id: 1, type: "form.step", case_id: "case_9", payload: { doc: "f1120", n: 1, of: 22, label: "Name" } },
      { id: 2, type: "form.step", case_id: "case_9", payload: { doc: "f1120", n: 2, of: 22, label: "x", rect: [0.5, 0.1, 0.2, 0.2], image: "https://evil.example/x.png" } },
      { id: 3, type: "sheet.cell", case_id: "case_9", payload: { book: "b.xlsx", n: 1, of: 2, sheet: "S", cell: "javascript:", value: 1 } },
    ]);
    expect(d.form?.fields.map((f) => f.rect)).toEqual([null, null]);
    expect(d.form?.images).toEqual({});
    expect(d.book).toBeNull();
    expect(cellPos("C12")).toEqual({ col: 2, row: 12 });
  });
});
