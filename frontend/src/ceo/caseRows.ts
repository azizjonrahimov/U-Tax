// F1: which cases the CEO console lists and which of their files it offers. Pure, so it is
// tested on its own (the component only renders these rows).

import type { CaseRow } from "./ceo";
import { docName } from "@/office/docs";

/** Recent cases always listed; an older case stays listed while it has documents to download. */
export const RECENT_CASES = 12;

export interface CaseLine extends CaseRow {
  /** The verified files to offer: none for a flagged case, only names the gateway can serve. */
  files: string[];
}

const caseNumber = (id: string) => Number(id.replace(/\D/g, "")) || 0;

/** Newest first (case ids grow): the recent ones, plus any older one whose documents are ready. */
export function caseRows(cases: readonly CaseRow[], served: Readonly<Record<string, readonly string[]>>, recent = RECENT_CASES): CaseLine[] {
  return [...cases]
    .sort((a, b) => caseNumber(b.caseId) - caseNumber(a.caseId))
    .map((c) => ({ ...c, files: c.flagged ? [] : (served[c.caseId] ?? []).filter((f) => docName(c.caseId, f)) }))
    .filter((r, i) => i < recent || r.files.length > 0);
}

/** A status the way the CEO says it ("QA_PASSED" → "QA passed"). */
export function statusWord(status: string): string {
  const s = status.replace(/_/g, " ").toLowerCase();
  return s.replace(/\bqa\b/g, "QA").replace(/^./, (c) => c.toUpperCase());
}

/** What kind of file it is, from the gateway's own names. */
export const fileKind = (file: string) => (/Workpaper/i.test(file) ? "Workpaper (Excel)" : /Memo/i.test(file) ? "Memo (Word)" : file.endsWith(".xlsx") ? "Excel file" : "Word file");
