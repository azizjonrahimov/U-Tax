// What the theater shows, as a pure fold over cursor steps. The player applies
// steps one at a time (with animation); replay and tests apply them all at once.
// Nothing here invents content: every field is set from a step's text/meta.

import type { CursorStep, Pane } from "./script";

export interface Cell {
  text: string;
  mark?: "win" | "lose";
  tick?: string;
  circle?: string;
  assumed?: boolean;
  missing?: boolean;
}

export interface MailView {
  from?: string;
  subject?: string;
  body?: string;
  /** Quoted spans to highlight in the body, by fact. */
  quotes: Record<string, string>;
  compose?: { header: string; text: string; sent?: string; pressed?: boolean };
}

export interface SheetView {
  cells: Record<string, Cell>;
  qa?: string;
  flags: string[];
}

export interface CaseView {
  name: string;
  mail: MailView;
  sheet: SheetView;
}

export interface CaseRow {
  caseId: string;
  name: string;
  status: string;
  blocked?: string;
  /** QA flags (case.flagged kinds, pretty), shown as amber chips. */
  flags?: string[];
}

export interface LogLine {
  eventId: number;
  ts: string;
  text: string;
  kind: string;
}

export interface ScreenState {
  camera: Pane | null;
  /** Case shown on the mail / sheet panes. */
  mailCase: string | null;
  sheetCase: string | null;
  cases: Record<string, CaseView>;
  /** Order cases first appeared, for the rail. */
  caseOrder: string[];
  caseRows: Record<string, CaseRow>;
  search: { query: string; results: string[]; more?: string };
  calendar: { blocks: { key: string; text: string; start?: string; minutes?: number }[] };
  social: { image?: string; caption?: string; channel?: string; posted?: string; dm?: { text: string; recipient?: string; sent: boolean } };
  phone: { dial?: string; transcript?: string; check?: { ok: boolean; text: string } };
  rail: {
    log: LogLine[];
    trust: Record<string, string>;
    pending: { approvalId: string; text: string }[];
    voice?: { text: string; speaker: string };
    brief?: { text: string; eventId: number };
    notify?: string;
    crm?: string;
    hiring: string[];
  };
  /** Last event that failed (status "failed"), shown as a red note on its pane. */
  failure?: { pane: Pane; text: string; eventId: number };
  /** Id of the last event whose steps were applied. */
  lastEventId: number;
}

export const emptyScreen = (): ScreenState => ({
  camera: null,
  mailCase: null,
  sheetCase: null,
  cases: {},
  caseOrder: [],
  caseRows: {},
  search: { query: "", results: [] },
  calendar: { blocks: [] },
  social: {},
  phone: {},
  rail: { log: [], trust: {}, pending: [], hiring: [] },
  lastEventId: 0,
});

const MAX_LOG = 12;

const str = (v: unknown): string | undefined => (typeof v === "string" ? v : undefined);

function caseView(s: ScreenState, caseId: string, name?: string): CaseView {
  return s.cases[caseId] ?? { name: name ?? caseId, mail: { quotes: {} }, sheet: { cells: {}, flags: [] } };
}

/** Apply one step. Returns a new state; never mutates the input. */
export function applyStep(prev: ScreenState, step: CursorStep): ScreenState {
  const s: ScreenState = { ...prev, lastEventId: Math.max(prev.lastEventId, step.eventId) };
  const caseId = step.caseId;
  const text = step.text ?? "";
  const meta = step.meta ?? {};

  const withCase = (fn: (v: CaseView) => CaseView) => {
    if (caseId === null) return;
    const name = s.caseRows[caseId]?.name;
    s.cases = { ...s.cases, [caseId]: fn(caseView(s, caseId, name)) };
  };
  const mail = (fn: (m: MailView) => MailView) => withCase((v) => ({ ...v, mail: fn(v.mail) }));
  const sheet = (fn: (sh: SheetView) => SheetView) => withCase((v) => ({ ...v, sheet: fn(v.sheet) }));
  const cell = (key: string, fn: (c: Cell) => Cell) =>
    sheet((sh) => ({ ...sh, cells: { ...sh.cells, [key]: fn(sh.cells[key] ?? { text: "" }) } }));
  const log = (kind: string) => {
    s.rail = { ...s.rail, log: [...s.rail.log, { eventId: step.eventId, ts: step.ts, text, kind }].slice(-MAX_LOG) };
  };

  const [area, sub, ...rest] = step.target.split(".");
  const key = rest.join(".");

  switch (step.action) {
    case "focus":
      s.camera = step.pane;
      return s;
    case "switch":
      if (step.pane === "mail") s.mailCase = caseId;
      if (step.pane === "sheet") s.sheetCase = caseId;
      if (caseId !== null) withCase((v) => ({ ...v, name: text || v.name }));
      return s;
    case "fail":
      s.failure = { pane: step.pane, text: `${text} failed`, eventId: step.eventId };
      return s;
  }

  if (area === "mail") {
    if (caseId !== null) s.mailCase = caseId;
    switch (sub) {
      case "message":
        if (step.action === "open") {
          withCase((v) => ({
            ...v,
            name: str(meta.masked_name) ?? v.name,
            mail: { quotes: {}, from: str(meta.from), subject: str(meta.subject), body: text },
          }));
        }
        break;
      case "quote":
        mail((m) => ({ ...m, quotes: { ...m.quotes, [key]: text } }));
        break;
      case "compose":
        mail((m) => ({ ...m, compose: { header: text, text: "" } }));
        break;
      case "memo":
        mail((m) => ({ ...m, compose: { header: m.compose?.header ?? "", ...m.compose, text } }));
        break;
      case "send":
        mail((m) => (m.compose ? { ...m, compose: { ...m.compose, pressed: true } } : m));
        break;
      case "sent":
        mail((m) => (m.compose ? { ...m, compose: { ...m.compose, sent: text } } : m));
        break;
    }
    return s;
  }

  if (area === "sheet") {
    if (caseId !== null) s.sheetCase = caseId;
    if (sub === "cell") {
      switch (step.action) {
        case "fill":
        case "drag":
          cell(key, (c) => ({ ...c, text, assumed: meta.assumed === true, missing: meta.missing === true }));
          break;
        case "mark":
          cell(key, (c) => ({ ...c, mark: text === "win" ? "win" : "lose" }));
          break;
        case "tick":
          cell(key, (c) => ({ ...c, tick: text, circle: undefined }));
          break;
        case "circle":
          cell(key, (c) => ({ ...c, circle: text, tick: undefined }));
          break;
      }
    } else if (sub === "qa") {
      sheet((sh) => ({ ...sh, qa: text }));
    } else if (sub === "flag") {
      sheet((sh) => (sh.flags.includes(text) ? sh : { ...sh, flags: [...sh.flags, text] }));
    }
    return s;
  }

  if (area === "search") {
    if (sub === "query") s.search = { query: text, results: [] };
    else if (sub === "result") s.search = { ...s.search, results: [...s.search.results, text] };
    else if (sub === "more") s.search = { ...s.search, more: text };
    return s;
  }

  if (area === "calendar") {
    s.calendar = {
      blocks: [
        ...s.calendar.blocks,
        { key: step.key, text, start: str(meta.start), minutes: typeof meta.minutes === "number" ? meta.minutes : undefined },
      ].slice(-4),
    };
    return s;
  }

  if (area === "social") {
    switch (sub) {
      case "image":
        s.social = { ...s.social, image: str(meta.image_url) };
        break;
      case "caption":
        s.social = { image: s.social.image, caption: text, channel: str(meta.channel), dm: s.social.dm };
        break;
      case "posted":
        s.social = { ...s.social, posted: text };
        break;
      case "dm":
        s.social = { ...s.social, dm: { text, recipient: str(meta.recipient), sent: false } };
        break;
      case "dm_send":
        s.social = { ...s.social, dm: s.social.dm ? { ...s.social.dm, sent: true } : { text: "", recipient: str(meta.recipient), sent: true } };
        break;
    }
    return s;
  }

  if (area === "phone") {
    if (sub === "dial") s.phone = { dial: text };
    else if (sub === "transcript") s.phone = { ...s.phone, transcript: text };
    else if (sub === "check") s.phone = { ...s.phone, check: { ok: step.action === "tick", text } };
    return s;
  }

  // Rail
  switch (sub) {
    case "cases": {
      if (caseId === null) break;
      const row = s.caseRows[caseId] ?? { caseId, name: caseId, status: "new" };
      let next: CaseRow = row;
      if (step.action === "appear") next = { ...row, name: text };
      else if (step.action === "status") next = { ...row, status: text, blocked: row.blocked && text === "blocked" ? row.blocked : undefined };
      else if (step.action === "block") next = { ...row, status: "blocked", blocked: text };
      else if (step.action === "flag") next = { ...row, flags: row.flags?.includes(text) ? row.flags : [...(row.flags ?? []), text] };
      s.caseRows = { ...s.caseRows, [caseId]: next };
      if (!s.caseOrder.includes(caseId)) s.caseOrder = [...s.caseOrder, caseId];
      break;
    }
    case "touches":
      if (step.action === "appear") {
        s.rail = { ...s.rail, pending: [...s.rail.pending, { approvalId: str(meta.approval_id) ?? step.key, text }] };
      } else {
        const done = str(meta.approval_id);
        s.rail = { ...s.rail, pending: done ? s.rail.pending.filter((p) => p.approvalId !== done) : s.rail.pending };
        log(str(meta.kind) ?? "touch");
      }
      break;
    case "trust":
      s.rail = { ...s.rail, trust: { ...s.rail.trust, [str(meta.action_type) ?? text]: str(meta.to) ?? text } };
      break;
    case "voice":
      s.rail = { ...s.rail, voice: { text, speaker: str(meta.speaker) ?? "ceo" } };
      break;
    case "brief":
      s.rail = { ...s.rail, brief: { text, eventId: step.eventId } };
      break;
    case "notify":
      s.rail = { ...s.rail, notify: text };
      break;
    case "crm":
      s.rail = { ...s.rail, crm: text };
      break;
    case "hiring":
      s.rail = { ...s.rail, hiring: [...s.rail.hiring, text].slice(-3) };
      break;
  }
  return s;
}

/** Apply many steps at once (replay jumps, tests). */
export const applySteps = (state: ScreenState, steps: readonly CursorStep[]) => steps.reduce(applyStep, state);
