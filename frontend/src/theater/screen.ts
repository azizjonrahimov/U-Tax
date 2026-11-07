// What the theater shows, as a pure fold over cursor steps. The player applies
// steps one at a time (with animation); replay and tests apply them all at once.
// Nothing here invents content: every field is set from a step's text/meta.

import { NEW_CLIENT, type CursorStep, type Pane } from "./script";

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
  /** v1.6.4: the case was flagged possible_injection; its body is never shown again. */
  hidden?: boolean;
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
  /** Contract status (CALL_BOOKED, BLOCKED, ...) for styling; display text can change wording. */
  raw?: string;
  blocked?: string;
  /** QA flags (case.flagged kinds, pretty), shown as amber chips. */
  flags?: string[];
}

export interface LogLine {
  eventId: number;
  ts: string;
  text: string;
  kind: string;
  /** v1.8: the CEO gave this touch by voice (the rail shows a mic). */
  voice?: boolean;
}

export interface ScreenState {
  /**
   * Cases flagged possible_injection (contract §8): their mail body and subject are
   * never shown, including later emails on the same case. Kept on the screen, not
   * on a mail view that the next email would replace.
   */
  flagged: string[];
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
    crm?: string;
    /** v1.9: the gateway's own answers to Naïve tool confirms (the latest, and how many). */
    naive?: { text: string; allowed: boolean; allowedCount: number; deniedCount: number };
  };
  /** Last event that failed (status "failed"), shown as a red note on its pane. */
  failure?: { pane: Pane; text: string; eventId: number };
  /** Id of the last event whose steps were applied. */
  lastEventId: number;
}

export const emptyScreen = (): ScreenState => ({
  flagged: [],
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
  rail: { log: [], trust: {}, pending: [] },
  lastEventId: 0,
});

const MAX_LOG = 12;

const str = (v: unknown): string | undefined => (typeof v === "string" ? v : undefined);

/** Own-key lookup: ids like "constructor" or "__proto__" must never hit Object.prototype. */
const own = <T,>(o: Record<string, T>, k: string): T | undefined => (Object.hasOwn(o, k) ? o[k] : undefined);

function caseView(s: ScreenState, caseId: string, name?: string): CaseView {
  return own(s.cases, caseId) ?? { name: name ?? NEW_CLIENT, mail: { quotes: {} }, sheet: { cells: {}, flags: [] } };
}

/** Apply one step. Returns a new state; never mutates the input. */
export function applyStep(prev: ScreenState, step: CursorStep): ScreenState {
  const s: ScreenState = { ...prev, lastEventId: Math.max(prev.lastEventId, step.eventId) };
  // A failure note stays until the next real step on its pane.
  if (s.failure && step.action !== "fail" && step.action !== "focus" && step.pane === s.failure.pane) s.failure = undefined;
  const caseId = step.caseId;
  const text = step.text ?? "";
  const meta = step.meta ?? {};

  const withCase = (fn: (v: CaseView) => CaseView) => {
    if (caseId === null) return;
    const name = own(s.caseRows, caseId)?.name;
    s.cases = { ...s.cases, [caseId]: fn(caseView(s, caseId, name)) };
  };
  const mail = (fn: (m: MailView) => MailView) => withCase((v) => ({ ...v, mail: fn(v.mail) }));
  const sheet = (fn: (sh: SheetView) => SheetView) => withCase((v) => ({ ...v, sheet: fn(v.sheet) }));
  const cell = (key: string, fn: (c: Cell) => Cell) =>
    sheet((sh) => ({ ...sh, cells: { ...sh.cells, [key]: fn(own(sh.cells, key) ?? { text: "" }) } }));
  const log = (kind: string, voice = false) => {
    const line: LogLine = voice ? { eventId: step.eventId, ts: step.ts, text, kind, voice } : { eventId: step.eventId, ts: step.ts, text, kind };
    s.rail = { ...s.rail, log: [...s.rail.log, line].slice(-MAX_LOG) };
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
            mail: { quotes: {}, from: str(meta.from), subject: str(meta.subject), body: text, hidden: s.flagged.includes(caseId!) || undefined },
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
      const row = own(s.caseRows, caseId) ?? { caseId, name: NEW_CLIENT, status: "new", raw: "NEW" };
      let next: CaseRow = row;
      if (step.action === "appear") next = { ...row, name: text };
      else if (step.action === "status") {
        const raw = str(meta.to) ?? (text === "not eligible" ? "NOT_ELIGIBLE" : undefined);
        next = { ...row, status: text, raw, blocked: raw === "BLOCKED" ? row.blocked : undefined };
      } else if (step.action === "block") next = { ...row, status: "blocked", raw: "BLOCKED", blocked: text };
      else if (step.action === "flag") {
        next = { ...row, flags: row.flags?.includes(text) ? row.flags : [...(row.flags ?? []), text] };
        if (meta.kind === "possible_injection") {
          if (!s.flagged.includes(caseId)) s.flagged = [...s.flagged, caseId];
          mail((m) => ({ ...m, hidden: true }));
        }
      }
      s.caseRows = { ...s.caseRows, [caseId]: next };
      if (!s.caseOrder.includes(caseId)) s.caseOrder = [...s.caseOrder, caseId];
      break;
    }
    case "touches":
      if (step.action === "appear") {
        const id = str(meta.approval_id) ?? step.key;
        const others = s.rail.pending.filter((p) => p.approvalId !== id); // a repeated request replaces its row
        s.rail = { ...s.rail, pending: [...others, { approvalId: id, text }] };
      } else {
        const done = str(meta.approval_id);
        s.rail = { ...s.rail, pending: done ? s.rail.pending.filter((p) => p.approvalId !== done) : s.rail.pending };
        log(str(meta.kind) ?? "touch", meta.channel === "voice");
      }
      break;
    case "trust":
      s.rail = { ...s.rail, trust: { ...s.rail.trust, [str(meta.action_type) ?? text]: str(meta.to) ?? text } };
      break;
    case "crm":
      s.rail = { ...s.rail, crm: text };
      break;
    case "naive": {
      const allowed = meta.decision === "allow";
      const prev = s.rail.naive;
      s.rail = {
        ...s.rail,
        naive: {
          text,
          allowed,
          allowedCount: (prev?.allowedCount ?? 0) + (allowed ? 1 : 0),
          deniedCount: (prev?.deniedCount ?? 0) + (allowed ? 0 : 1),
        },
      };
      break;
    }
    // voice / brief / notify are captions (spoken words), drawn by the player from the step;
    // hiring numbers come from the rail projection (counts only, never names).
  }
  return s;
}

export interface SeedCase {
  caseId: string;
  name: string;
  /** Contract status, e.g. CALL_BOOKED. */
  status?: string;
  flagged?: boolean;
}

/**
 * A screen that starts from known facts instead of empty: live mode seeds the case
 * rows from /api/state (so the rail's cards match its numbers after a reload), and
 * replay pre-marks cases the log flags later, so their mail never shows at all.
 */
export function seedScreen(base: ScreenState, cases: readonly SeedCase[], flagged: readonly string[] = []): ScreenState {
  const s: ScreenState = { ...base, caseRows: { ...base.caseRows }, caseOrder: [...base.caseOrder], flagged: [...base.flagged] };
  for (const c of cases) {
    if (!Object.hasOwn(s.caseRows, c.caseId)) {
      const status = c.status ? c.status.toLowerCase().replace(/_/g, " ") : "new";
      s.caseRows[c.caseId] = { caseId: c.caseId, name: c.name, status, raw: c.status };
      s.caseOrder.push(c.caseId);
    }
    if (c.flagged && !s.flagged.includes(c.caseId)) s.flagged.push(c.caseId);
  }
  for (const id of flagged) if (!s.flagged.includes(id)) s.flagged.push(id);
  // Mail already on screen for a case we now know is flagged comes down right away.
  for (const id of s.flagged) {
    const v = own(s.cases, id);
    if (v && !v.mail.hidden) {
      if (s.cases === base.cases) s.cases = { ...base.cases };
      s.cases[id] = { ...v, mail: { ...v.mail, hidden: true } };
    }
  }
  return s;
}

/** Case ids a log flags possible_injection anywhere (replay knows the whole night up front). */
export function flaggedInLog(events: readonly { type: string; case_id: string | null; payload: Record<string, unknown> }[]): string[] {
  const out = new Set<string>();
  for (const e of events) {
    // /api/events items are unvalidated here: a null or primitive entry must not kill the replay.
    if (typeof e !== "object" || e === null) continue;
    if (e.type === "case.flagged" && typeof e.case_id === "string" && e.payload?.kind === "possible_injection") out.add(e.case_id);
  }
  return [...out];
}

/** Apply many steps at once (replay jumps, tests). */
export const applySteps = (state: ScreenState, steps: readonly CursorStep[]) => steps.reduce(applyStep, state);
