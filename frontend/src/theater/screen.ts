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

/** D31: one step of a CEO order, as a plain line ("Marketing posted to X"). */
export interface OrderLine {
  key: string;
  seat: string;
  text: string;
  /** The ad thumbnail (still gated by NEXT_PUBLIC_ASSET_HOSTS where it renders). */
  image?: string;
  /** The same line again in a row ("×3"), e.g. several handoffs. */
  times?: number;
}

/** D31: a CEO order (a `voice.command` with speaker ceo) and the steps that followed it. */
export interface OrderCard {
  /** The order's voice.command event id (what `order_id` points at). */
  id: number;
  /** "Order N": its place among every order this screen has seen, so numbers never shift. */
  n: number;
  ts: string;
  text: string;
  lines: OrderLine[];
}

export interface ScreenState {
  /** D31: CEO orders, oldest first (the newest few). */
  orders: OrderCard[];
  /** D31: orders seen so far (numbers the cards). */
  orderSeq: number;
  /** Time of the newest step applied (order freshness is measured in log time, not event counts). */
  lastTs: string;
  /** D31: seat → key of its latest step; a changed key re-lights the desk. */
  desks: Record<string, string>;
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
  /** `source`: where the search ran (law: the Naïve Data catalogue; HR: Monid public profiles). */
  search: { query: string; results: string[]; more?: string; source?: string };
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
  orders: [],
  orderSeq: 0,
  lastTs: "",
  desks: {},
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
/** Orders kept on screen, and lines kept per order (the card shows the newest). */
const MAX_ORDERS = 6;
const MAX_ORDER_LINES = 16;
/** Without an order_id, a step can join one of the newest few orders only. */
const OPEN_ORDERS = 4;

/**
 * Words in a CEO order that name a D31 department's work. The fallback for a D31 action step that
 * came without an order_id (the gateway stamps one on everything inside an order's tool calls).
 */
const SEAT_WORDS: Readonly<Record<string, RegExp>> = Object.freeze({
  mkt: /\b(advertis\w*|ads?|post\w*|linkedin|x|twitter|market\w*|promot\w*|social|campaign\w*)\b/i,
  hr: /\b(hir\w*|recruit\w*|candidates?|interview\w*|people|person|jobs?|applicants?|talent)\b/i,
  intake: /\b(follow\w*|customers?|clients?|remind\w*)\b/i,
});

/** Log-time windows (ms): an unanswered order stays on screen; a handoff or a department match may join it. */
const FRESH_MS = 2 * 60_000;
const HANDOFF_MS = 60_000;
const MATCH_MS = 10 * 60_000;

/**
 * How a step may find its order. "id": only by order_id (case work: tax, memo, client calls, approvals).
 * "dept": order_id, else a recent order naming its department (ad, posts, HR, follow-ups).
 * "handoff": the Chief of Staff's send_to_agent check (a Naïve check carries no order_id): only an
 * order given within a minute that no department has acted on yet.
 */
export type OrderMatch = "id" | "dept" | "handoff";

const age = (from: string, to: string) => {
  const a = Date.parse(from);
  const b = Date.parse(to);
  return Number.isFinite(a) && Number.isFinite(b) ? b - a : Infinity;
};

/** The orders worth a card: the firm acted on them, or they were given in the last two minutes of log time. */
export const visibleOrders = (s: Pick<ScreenState, "orders" | "lastTs">) => s.orders.filter((o) => o.lines.length > 0 || age(o.ts, s.lastTs) <= FRESH_MS);

/** Which order a step belongs to (an index into `orders`, or -1 for none). */
export function orderFor(orders: readonly OrderCard[], seat: string, orderId: number | null, match: OrderMatch, ts: string): number {
  if (orderId !== null) return orders.findIndex((o) => o.id === orderId);
  if (match === "handoff") {
    const i = orders.length - 1;
    const o = orders[i];
    return o && age(o.ts, ts) <= HANDOFF_MS && o.lines.every((l) => l.seat === "cos") ? i : -1;
  }
  if (match !== "dept" || !Object.hasOwn(SEAT_WORDS, seat)) return -1;
  const words = SEAT_WORDS[seat];
  for (let i = orders.length - 1; i >= Math.max(0, orders.length - OPEN_ORDERS); i--) {
    if (age(orders[i].ts, ts) <= MATCH_MS && words.test(orders[i].text)) return i;
  }
  return -1;
}

/**
 * D31 order bookkeeping for one step, and nothing else: a CEO order opens a card, a step carrying an
 * order line joins its order. `applyStep` runs it on every step; `seedOrders` runs it alone to
 * rebuild the cards a reloaded projector missed, without replaying anything on the panes.
 */
function applyOrderStep(s: ScreenState, step: CursorStep): void {
  const meta = step.meta ?? {};
  // Only the CEO's own words open a card (a customer's speech never reads as an order).
  if (step.target === "rail.voice" && meta.speaker === "ceo" && step.agent === "ceo" && step.text && !s.orders.some((o) => o.id === step.eventId)) {
    s.orderSeq += 1;
    s.orders = [...s.orders, { id: step.eventId, n: s.orderSeq, ts: step.ts, text: step.text, lines: [] }].sort((a, b) => a.id - b.id).slice(-MAX_ORDERS);
  }
  const line = str(meta.order_line);
  if (!line || !s.orders.length) return;
  const orderId = typeof meta.order_id === "number" ? meta.order_id : null;
  const match = meta.order_match === "dept" || meta.order_match === "handoff" ? meta.order_match : "id";
  const i = orderFor(s.orders, str(meta.order_seat) ?? "", orderId, match, step.ts);
  if (i < 0) return;
  const o = s.orders[i];
  if (o.lines.some((l) => l.key === step.key)) return; // already filed (a seed met the live stream)
  const last = o.lines.at(-1);
  const image = str(meta.order_image);
  const lines =
    last && last.text === line && !image
      ? [...o.lines.slice(0, -1), { ...last, times: (last.times ?? 1) + 1 }]
      : [...o.lines, { key: step.key, seat: str(meta.order_seat) ?? "", text: line, ...(image ? { image } : {}) }].slice(-MAX_ORDER_LINES);
  s.orders = s.orders.map((x, j) => (j === i ? { ...o, lines } : x));
}

/** Rebuild order cards from steps that already happened (a reload): orders only, no pane changes. */
export function seedOrders(base: ScreenState, steps: readonly CursorStep[]): ScreenState {
  const s: ScreenState = { ...base };
  for (const step of steps) {
    applyOrderStep(s, step);
    if (step.ts > s.lastTs) s.lastTs = step.ts;
  }
  return s;
}

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

  // D31: a desk lights up whenever its agent acts (a Naïve check names the seat in meta).
  const seat = step.agent ?? (step.target === "rail.naive" ? str(meta.agent) : undefined);
  if (seat && seat !== "ceo" && step.action !== "focus" && step.action !== "switch") s.desks = { ...s.desks, [seat]: step.key };

  // D31: a CEO order opens a card; later steps carrying an order line fill it.
  if (step.ts > s.lastTs) s.lastTs = step.ts;
  applyOrderStep(s, step);

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
    if (sub === "query") s.search = { query: text, results: [], ...(str(meta.source) ? { source: str(meta.source) } : {}) };
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
        // The post's own picture, or none: a text post never shows under the previous ad's image.
        s.social = { image: Object.hasOwn(meta, "image_url") ? str(meta.image_url) : s.social.image, caption: text, channel: str(meta.channel), dm: s.social.dm };
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
