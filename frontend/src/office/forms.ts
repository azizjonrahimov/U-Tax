// D42 T3 (runbook §T3, contract v1.21 §11): the draft Form 1120 and the returns workbook as the
// agents fill them, folded from form.step and sheet.cell (pure; tests pin it). Admin and session
// readers get each field's rect and the page image; theater readers only {doc, n, of, label}, so the
// form itself is drawn only when rect and image are there.

export type Rect = [number, number, number, number];
export interface FormField {
  n: number;
  key: string;
  label: string;
  value: string;
  page: number;
  rect: Rect | null;
}
export interface FormDoc {
  caseId: string | null;
  doc: string;
  of: number;
  fields: FormField[];
  /** page index → image path (gateway /assets/forms/… or a local /fixtures/forms/…). */
  images: Record<number, string>;
}
export interface SheetCell {
  n: number;
  sheet: string;
  cell: string;
  label: string;
  value: string | number | null;
  formula: string | null;
}
export interface Book {
  caseId: string | null;
  book: string;
  of: number;
  cells: SheetCell[];
  sheets: string[];
}
export interface Docs {
  form: FormDoc | null;
  book: Book | null;
}
export interface DocEvent {
  id: number;
  type: string;
  case_id?: string | null;
  payload?: unknown;
}

export const EMPTY_DOCS: Docs = { form: null, book: null };

type P = Record<string, unknown>;
const obj = (v: unknown): P => (typeof v === "object" && v !== null && !Array.isArray(v) ? (v as P) : {});
const str = (v: unknown, max = 120) => (typeof v === "string" ? v.slice(0, max) : typeof v === "number" && Number.isFinite(v) ? String(v) : "");
const int = (v: unknown) => (typeof v === "number" && Number.isSafeInteger(v) && v >= 0 ? v : null);
const frac = (v: unknown) => typeof v === "number" && Number.isFinite(v) && v >= 0 && v <= 1;
/** Only the firm's own form pages: the gateway's /assets/forms/ route, or a local fixture. */
const IMAGE = /^\/(assets|fixtures)\/forms\/[\w./-]{1,120}\.png$/;
const CELL = /^[A-Z]{1,2}[1-9]\d{0,3}$/;

function rectOf(v: unknown): Rect | null {
  if (!Array.isArray(v) || v.length !== 4 || !v.every(frac)) return null;
  const [x0, y0, x1, y1] = v as number[];
  return x0 < x1 && y0 < y1 ? [x0, y0, x1, y1] : null;
}

/** Fold events into the newest form and the newest workbook (a new case or document starts over). Idempotent. */
export function foldDocs(prev: Docs, events: readonly DocEvent[]): Docs {
  let { form, book } = prev;
  for (const ev of events) {
    const p = obj(ev.payload);
    const caseId = typeof ev.case_id === "string" ? ev.case_id : null;
    const n = int(p.n);
    const of = int(p.of);
    if (n === null || of === null || n < 1 || n > 500 || of > 500) continue;
    if (ev.type === "form.step") {
      const doc = str(p.doc, 24) || "f1120";
      if (!form || form.caseId !== caseId || form.doc !== doc) form = { caseId, doc, of, fields: [], images: {} };
      else form = { ...form, of, fields: [...form.fields], images: { ...form.images } };
      const page = int(p.page) ?? 0;
      const image = str(p.image, 160);
      if (IMAGE.test(image) && page < 20) form.images[page] = image;
      const field: FormField = { n, key: str(p.key, 24), label: str(p.label, 80), value: str(p.value, 80), page, rect: rectOf(p.rect) };
      form.fields = [...form.fields.filter((f) => f.n !== n), field].sort((a, b) => a.n - b.n);
    } else if (ev.type === "sheet.cell") {
      const name = str(p.book, 80) || "workbook.xlsx";
      const sheet = str(p.sheet, 31);
      const cell = str(p.cell, 6);
      if (!sheet || !CELL.test(cell)) continue;
      if (!book || book.caseId !== caseId || book.book !== name) book = { caseId, book: name, of, cells: [], sheets: [] };
      else book = { ...book, of, cells: [...book.cells], sheets: [...book.sheets] };
      const v = p.value;
      const value = typeof v === "number" && Number.isFinite(v) ? v : typeof v === "string" ? v.slice(0, 120) : null;
      const formula = typeof p.formula === "string" && p.formula.startsWith("=") ? p.formula.slice(0, 120) : null;
      book.cells = [...book.cells.filter((c) => c.n !== n), { n, sheet, cell, label: str(p.label, 80), value, formula }].sort((a, b) => a.n - b.n);
      if (!book.sheets.includes(sheet)) book.sheets = [...book.sheets, sheet];
    }
  }
  return form === prev.form && book === prev.book ? prev : { form, book };
}

/** "$63,000" for a number (a workbook value), the text as is otherwise. */
export const cellText = (v: string | number | null) => (typeof v === "number" ? `${v < 0 ? "-" : ""}$${Math.abs(Math.round(v)).toLocaleString("en-US")}` : v ?? "");

/** "Line 31 Total tax · $63,000" for a numbered line; a header field ("Name") as is. */
export function lineText(f: FormField): { label: string; value: string } {
  const numbered = /^(\d+[a-z]?|J\d+[a-z]?)\s/.test(f.label) || /^\d/.test(f.key);
  const money = numbered && /^\(?[\d,]+\)?$/.test(f.value);
  return { label: numbered ? `Line ${f.label}` : f.label, value: money ? (f.value.startsWith("(") ? `-$${f.value.slice(1, -1)}` : `$${f.value}`) : f.value };
}

/** Column index (A=0) and row (1-based) of "B12". */
export function cellPos(cell: string): { col: number; row: number } {
  const m = /^([A-Z]{1,2})(\d+)$/.exec(cell);
  if (!m) return { col: 0, row: 1 };
  const col = m[1].split("").reduce((n, ch) => n * 26 + ch.charCodeAt(0) - 64, 0) - 1;
  return { col, row: Number(m[2]) };
}
