// F1: one rule for which of a case's files the gateway serves (the Accounting window and the
// CEO's case list both fold it from the log).

import { describe, expect, it } from "vitest";
import type { UTaxEvent } from "@/theater/events";
import { emptyCaseDocs, stepCaseDocs, type CaseDocs } from "./artifact";
import { statusWord } from "@/components/ceo/CaseList";

let id = 0;
const ev = (type: string, case_id: string | null, payload: Record<string, unknown>): UTaxEvent => ({ id: ++id, ts: "2026-11-08T10:00:00Z", type, case_id, payload }) as unknown as UTaxEvent;

const build = (cid: string, v: number, ok: { workpaper: boolean; memo: boolean; books: boolean }) => [
  ev("doc.step", cid, { doc: "workpaper", step: 1, detail: "Income" }),
  ev("doc.created", cid, { doc: "workpaper", file_name: `Workpaper_v${v}.xlsx` }),
  ev("doc.created", cid, { doc: "memo", file_name: `Memo_v${v}.docx` }),
  ev("doc.created", cid, { doc: "books", file_name: "U-Tax_Books.xlsx" }),
  ev("doc.verified", cid, { doc: "workpaper", ok: ok.workpaper }),
  ev("doc.verified", cid, { doc: "memo", ok: ok.memo }),
  ev("doc.verified", cid, { doc: "books", ok: ok.books }),
];
const fold = (events: UTaxEvent[], flagged: (c: string | null) => boolean = () => false, from: CaseDocs = emptyCaseDocs()) =>
  events.reduce((d, e) => stepCaseDocs(d, e, flagged(e.case_id)), from);

describe("F1 case documents", () => {
  it("serves a case's workpaper and memo once all three documents passed (never the books)", () => {
    const d = fold(build("case_1", 1, { workpaper: true, memo: true, books: true }));
    expect(d.served.case_1).toEqual(["Workpaper_v1.xlsx", "Memo_v1.docx"]);
  });

  it("nothing is served while a check failed; an earlier good version stays served", () => {
    expect(fold(build("case_2", 1, { workpaper: true, memo: false, books: true })).served.case_2).toBeUndefined();
    const good = fold(build("case_3", 1, { workpaper: true, memo: true, books: true }));
    const after = fold(build("case_3", 2, { workpaper: true, memo: true, books: false }), () => false, good);
    expect(after.served.case_3).toEqual(["Workpaper_v1.xlsx", "Memo_v1.docx"]);
    expect(fold(build("case_3", 3, { workpaper: true, memo: true, books: true }), () => false, after).served.case_3).toEqual(["Workpaper_v3.xlsx", "Memo_v3.docx"]);
  });

  it("a flagged case never gets a download, and events without a case change nothing", () => {
    expect(fold(build("case_4", 1, { workpaper: true, memo: true, books: true }), (c) => c === "case_4").served).toEqual({});
    const d = emptyCaseDocs();
    expect(stepCaseDocs(d, ev("doc.created", null, { doc: "memo", file_name: "Memo.docx" }), false)).toBe(d);
    expect(stepCaseDocs(d, ev("case.created", "case_5", {}), false)).toBe(d);
  });

  it("statuses read the way the CEO says them", () => {
    expect(statusWord("QA_PASSED")).toBe("QA passed");
    expect(statusWord("memo_sent")).toBe("Memo sent");
    expect(statusWord("new")).toBe("New");
  });
});
