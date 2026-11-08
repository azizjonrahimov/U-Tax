// Event log → cursor script. Pure and deterministic: no clock, no randomness.
//
// Premise 7 (docs/designs/u-tax.md): every animation comes from a real logged
// event. Each step carries the id of the event that caused it. An event whose
// type is unknown, or whose envelope or payload does not match contracts/api.md
// §2–3, produces no steps and is reported in `skipped` (and logged): we never
// animate a value that was not in the log.
//
// Two entry points:
// - buildScript(events): the whole log at once (fixture, replay). Sorts by id.
// - appendEvents(cursor, events): live mode. Applies events strictly in id
//   order; ids at or below the cursor's lastId (SSE replays after a reconnect)
//   are dropped. For in-order input the two produce identical steps (prefix
//   property), and step keys never change once emitted.

import { ACTORS, EVENT_STATUSES, type Agent, type FactName, type UTaxEvent } from "./events";
import { formatCents, formatInt, formatUsd, normalizeDecimal, splitDecimal } from "./format";

export type Pane = "mail" | "sheet" | "search" | "calendar" | "social" | "phone" | "market" | "rail";

export type Action =
  | "focus" // camera moves to the pane (no cursor)
  | "switch" // a case-scoped pane (mail, sheet) switches to another case
  | "open" // open a message
  | "highlight" // mark a span of text
  | "drag" // carry a value from `from` to `target`
  | "fill" // write a value into a cell
  | "type" // type `text` character by character
  | "click"
  | "appear" // an element appears (result card, chip, compose box)
  | "mark" // style a cell: text is "win" | "lose"
  | "tick" // QA check passed on target
  | "circle" // QA check failed on target
  | "status" // a status chip changes to `text`
  | "block" // red BLOCKED card (flag NEXT_PUBLIC_SHOW_BLOCKED)
  | "flag" // amber "flagged" chip on a case card (case.flagged, contract v1.4)
  | "count" // a rail counter ticks
  | "say" // spoken words (voice command, brief)
  | "dial" // phone call starts
  | "fail"; // the event itself has status "failed"

export type Meta = Record<string, string | number | boolean | null>;

export interface CursorStep {
  /**
   * Stable key. Content steps: `<eventId>:<n>`; camera steps:
   * `<eventId>:focus:<pane>` / `<eventId>:switch:<pane>`, so content keys never
   * depend on where the camera was before.
   */
  key: string;
  eventId: number;
  ts: string;
  caseId: string | null;
  pane: Pane;
  /** Stable element key, e.g. "sheet.cell.total" or "mail.quote.authorized_shares". */
  target: string;
  /** Cursor owner; null = no cursor (system updates, camera moves, customer speech). */
  agent: Agent | null;
  action: Action;
  text?: string;
  /** Drag source element key. */
  from?: string;
  meta?: Meta;
}

export type SkipReason = "unknown_type" | "status_skipped" | "bad_event" | "bad_payload";

/** Ids that were jumped over. Info only: contract §2 (v1.3.2) allows gaps (e.g. Postgres rollbacks). */
export interface IdGap {
  from: number;
  to: number;
}

export interface SkippedEvent {
  eventId: number;
  type: string;
  reason: SkipReason;
  detail?: string;
}

/** Case-scoped panes show one case at a time. */
type CasePane = "mail" | "sheet";

export interface ScriptState {
  /** Pane the camera is on (rail steps don't move the camera). */
  camera: Pane | null;
  /** case_id → masked name, learned from email.received (or seeded from /api/state). */
  caseNames: Readonly<Record<string, string>>;
  /** Which case each case-scoped pane is showing. */
  onScreen: Readonly<Partial<Record<CasePane, string>>>;
  /** approval_id → action_type, so a touch can be labelled without its free-text note. */
  approvalTypes: Readonly<Record<string, string>>;
}

export interface ScriptCursor {
  state: ScriptState;
  /** Highest event id applied so far (0 = none). */
  lastId: number;
}

export interface Script {
  steps: CursorStep[];
  skipped: SkippedEvent[];
  /** Events the script applied, once each in id order (rail numbers project from these only). */
  accepted: UTaxEvent[];
}

export interface ScriptOptions {
  /** Called once per skipped event. Default: console.warn. */
  log?: (skipped: SkippedEvent) => void;
  /** Called once per id gap (info only). Default: console.info. */
  onGap?: (gap: IdGap) => void;
}

const defaultLog = (s: SkippedEvent) =>
  console.warn(`[theater] skipped event ${s.eventId} (${s.type}): ${s.reason}${s.detail ? ` (${s.detail})` : ""}`);

/**
 * A fresh cursor. Live mode seeds `caseNames` from `/api/state` cases so a
 * stream resumed mid-night still shows masked names, and `lastId` from
 * `last_event_id` when it does not want to replay history.
 */
export function startScript(seed: { caseNames?: Record<string, string>; lastId?: number } = {}): ScriptCursor {
  const caseNames: Record<string, string> = Object.create(null);
  for (const [id, name] of Object.entries(seed.caseNames ?? {})) caseNames[id] = name;
  // A seed that isn't a non-negative integer starts from 0 and says so (it would replay history).
  const valid = seed.lastId !== undefined && Number.isSafeInteger(seed.lastId) && seed.lastId >= 0;
  if (seed.lastId !== undefined && !valid) console.warn(`[theater] ignoring invalid lastId seed ${JSON.stringify(seed.lastId)}; starting from 0`);
  const lastId = valid ? (seed.lastId as number) : 0;
  return { state: { camera: null, caseNames, onScreen: {}, approvalTypes: Object.create(null) }, lastId };
}

/** Sheet rows for extracted facts, in display order. */
export const FACT_ROWS: readonly FactName[] = [
  "incorporated_delaware",
  "authorized_shares",
  "issued_shares",
  "gross_assets",
  "stated_par",
  "foreign_owner_25",
  "gross_receipts",
  "years_with_receipts",
];

/** Main pane for each known type: used for camera moves and "fail" steps. */
const PANE_OF: ReadonlyMap<string, Pane> = new Map<string, Pane>([
  ["email.received", "mail"],
  ["client.asked", "mail"],
  ["memo.sent", "mail"],
  ["facts.extracted", "sheet"],
  ["facts.missing", "sheet"],
  ["tax.computed", "sheet"],
  ["qa.passed", "sheet"],
  ["qa.failed", "sheet"],
  ["law.searched", "search"],
  ["call.booked", "calendar"],
  // v1.11.1 (D31, the live CEO orders)
  ["ad.created", "social"],
  ["hr.sourced", "search"],
  ["hr.outreach", "rail"],
  ["client.followed_up", "mail"],
  ["post.published", "social"],
  ["dm.drafted", "social"],
  ["dm.sent_by_human", "social"],
  ["phone.checked", "phone"],
  ["screen.call", "phone"],
  ["case.status", "rail"],
  ["case.blocked", "rail"],
  ["case.not_eligible", "rail"],
  ["case.flagged", "rail"],
  ["crm.sync_requested", "rail"],
  ["crm.synced", "rail"],
  ["hr.applicant", "rail"],
  ["hr.screened", "rail"],
  ["hr.shortlisted", "rail"],
  ["hr.hired", "rail"],
  ["hr.declined", "rail"],
  ["hr.questionnaire_sent", "rail"],
  ["hr.decision_mailed", "rail"],
  ["voice.command", "rail"],
  ["ceo.notified", "rail"],
  ["approval.requested", "rail"],
  ["touch.training", "rail"],
  ["touch.operating", "rail"],
  ["touch.engineering", "rail"],
  ["trust.changed", "rail"],
  ["cost.booked", "rail"],
  ["tool.bought", "rail"],
  ["brief.generated", "rail"],
  // v1.8 voice proposals: the touch that follows a confirm is what the rail shows; these draw nothing.
  ["ceo.proposed", "rail"],
  ["ceo.confirmed", "rail"],
  ["ceo.cancelled", "rail"],
  // v1.9 §6b-bis: the gateway answered a Naïve tool confirm by itself (no human click).
  ["naive.confirm", "rail"],
  // v1.12 §5a: every MCP tool call (no args, no data). The office workspaces animate it; the
  // theater only lights the seat's desk (no cursor, so a busy agent never slows the projector).
  ["tool.called", "rail"],
  // v1.19 §9.1 (D38, the agent market): Marketing hires an outside contractor agent, our code checks
  // its work, the gateway pays it (testnet tokens). Every step comes from one of these events.
  ["job.posted", "market"],
  ["job.claimed", "market"],
  ["job.submitted", "market"],
  ["job.verified", "market"],
  ["job.failed", "market"],
  ["payment.sent", "market"],
  ["job.delivered", "market"],
  ["agent.sale", "rail"],
]);

/** Seat → the name the projector uses (Theater's cursor labels). */
const SEAT_NAME: Readonly<Record<string, string>> = Object.freeze({
  cos: "Chief of Staff",
  intake: "Intake",
  tax: "Tax",
  qa: "QA",
  acct: "Accounting",
  mkt: "Marketing",
  hr: "HR",
});

/** A seat's projector name ("an agent" for anything else). */
const seatName = (seat: string) => (Object.hasOwn(SEAT_NAME, seat) ? SEAT_NAME[seat] : "an agent");

/** v1.11.1: a candidate's gateway letter ("Candidate A"); anything else is never shown. */
const candidateLabel = (v: string) => (/^Candidate [A-Z]{1,2}$/.test(v) ? v : "a candidate");

/** "x" → "X", "linkedin" → "LinkedIn": the channel as people say it. */
const CHANNEL_NAME: Readonly<Record<string, string>> = Object.freeze({ x: "X", twitter: "X", linkedin: "LinkedIn", instagram: "Instagram", facebook: "Facebook" });
const channelName = (c: string) => (Object.hasOwn(CHANNEL_NAME, c.toLowerCase()) ? CHANNEL_NAME[c.toLowerCase()] : clip(c, 24));

/** An approval's badge, by action type (email is shared by several departments: the Chief of Staff's badge). */
const APPROVAL_SEAT: Readonly<Record<string, string>> = Object.freeze({ social_post: "mkt", voice_screen: "hr", calendar_invite: "intake", contractor_pay: "mkt" });
const approvalSeat = (t: string) => (Object.hasOwn(APPROVAL_SEAT, t) ? APPROVAL_SEAT[t] : "cos");

const FOLLOW_UP_WORDS: Readonly<Record<string, string>> = Object.freeze({
  memo_check: "memo check",
  missing_facts: "asked for the missing facts",
  call_reminder: "call reminder",
});

/** v1.11.1: `order_id` is "order_<voice.command id>"; anything else is ignored (the theater matches by department). */
function orderRef(p: Obj): number | null {
  const v = has(p, "order_id") ? p.order_id : null;
  if (typeof v !== "string") return null;
  const m = /^order_([0-9]{1,15})$/.exec(v);
  const id = m ? Number(m[1]) : NaN;
  return Number.isSafeInteger(id) && id >= 1 ? id : null;
}

/** A Naïve tool name as a short label: tool names come from the agent's request, so keep only safe characters. */
const toolLabel = (t: string) => Array.from(t.replace(/[^\w.:-]/g, "")).slice(0, 40).join("") || "a tool";

export const KNOWN_TYPES: readonly string[] = [...PANE_OF.keys()];

/** Label for a case with no masked name on the log yet (e.g. a /talk fast-path case). */
export const NEW_CLIENT = "New client";
/** Public /talk text is unmoderated: the theater shows at most this many characters of a customer turn. */
export const MAX_CUSTOMER_TEXT = 240;
/** Any spoken words shown as a caption (CEO orders included) stay short enough to read. */
export const MAX_SPOKEN_TEXT = MAX_CUSTOMER_TEXT;

/** Clip to at most `max` characters (code points, so an emoji is never split), ending in "…". */
export const clip = (text: string, max: number) => {
  const chars = Array.from(text);
  return chars.length > max ? `${chars.slice(0, max - 1).join("")}…` : text;
};

/** Email text is public input too: keep a single message to a readable length on the projector. */
export const MAX_EMAIL_BODY = 2400;
export const MAX_EMAIL_SUBJECT = 160;

/** "social_post" → "social post". */
const words = (s: string) => s.replace(/_/g, " ");

// ---------------------------------------------------------------------------
// Payload checks. Each throws PayloadError, which becomes a bad_payload skip.

class PayloadError extends Error {}

type Obj = Record<string, unknown>;
const has = (o: Obj, k: string) => Object.hasOwn(o, k) && o[k] !== undefined;

function obj(v: unknown, what: string): Obj {
  if (typeof v !== "object" || v === null || Array.isArray(v)) throw new PayloadError(`${what}: not an object`);
  return v as Obj;
}
function str(o: Obj, k: string): string {
  const v = has(o, k) ? o[k] : undefined;
  if (typeof v !== "string") throw new PayloadError(`${k}: expected string`);
  return v;
}
function strOrNull(o: Obj, k: string): string | null {
  const v = has(o, k) ? o[k] : null;
  if (v !== null && typeof v !== "string") throw new PayloadError(`${k}: expected string or null`);
  return v;
}
function int(o: Obj, k: string): number {
  const v = has(o, k) ? o[k] : undefined;
  if (typeof v !== "number" || !Number.isSafeInteger(v)) throw new PayloadError(`${k}: expected integer`);
  return v;
}
/** Money and counts are never negative on the wire. */
function nonNeg(o: Obj, k: string): number {
  const v = int(o, k);
  if (v < 0) throw new PayloadError(`${k}: negative`);
  return v;
}
/** Strict: only true/false. A string "false" from an agent is malformed, never truthy. */
function bool(o: Obj, k: string): boolean {
  const v = has(o, k) ? o[k] : undefined;
  if (v !== true && v !== false) throw new PayloadError(`${k}: expected boolean`);
  return v;
}
function oneOf<T extends string>(o: Obj, k: string, allowed: readonly T[]): T {
  const v = str(o, k);
  if (!(allowed as readonly string[]).includes(v)) throw new PayloadError(`${k}: unexpected "${v}"`);
  return v as T;
}
function arr(o: Obj, k: string): unknown[] {
  const v = has(o, k) ? o[k] : undefined;
  if (!Array.isArray(v)) throw new PayloadError(`${k}: expected array`);
  return v;
}
function factList(o: Obj, k: string): FactName[] {
  return arr(o, k).map((f) => {
    if (typeof f !== "string" || !(FACT_ROWS as readonly string[]).includes(f)) throw new PayloadError(`${k}: unknown fact`);
    return f as FactName;
  });
}
function decimal(o: Obj, k: string): string {
  const v = str(o, k);
  try {
    splitDecimal(v);
  } catch {
    throw new PayloadError(`${k}: expected decimal string`);
  }
  return v;
}

/** v1.19: an amount in testnet tokens (number or decimal string on the wire) → "0.50". */
function tokens(o: Obj, k: string): string {
  const v = has(o, k) ? o[k] : undefined;
  if (typeof v === "number" && Number.isFinite(v) && v >= 0 && v < 1e9) return v.toFixed(2);
  if (typeof v === "string" && /^\d{1,9}(\.\d{1,18})?$/.test(v)) return Number(v).toFixed(2);
  throw new PayloadError(`${k}: expected a token amount`);
}
/** v1.19: a job id is the gateway's own short id: never free text on the projector. */
function jobId(o: Obj): string {
  const v = str(o, "job_id");
  if (!/^[A-Za-z0-9_-]{1,40}$/.test(v)) throw new PayloadError("job_id: unexpected");
  return v;
}
/** v1.19: the code checks on a contractor's work, `[{name, ok}]` (at most 8 shown). */
function checkList(o: Obj): { name: string; ok: boolean }[] {
  return arr(o, "checks")
    .slice(0, 8)
    .map((c) => {
      const x = obj(c, "check");
      return { name: clip(str(x, "name"), 40), ok: bool(x, "ok") };
    });
}
/** A transaction hash shortened for the screen ("0x3b60…2277"); anything else shows nothing. */
const shortTx = (tx: string) => (/^0x[0-9a-fA-F]{8,}$/.test(tx) ? `${tx.slice(0, 6)}…${tx.slice(-4)}` : "");
/** v1.19: a delivered image is the gateway's own asset path ("/assets/ad_3.png"); other values never render. */
const ASSET_PATH = /^\/assets\/[A-Za-z0-9_.-]{1,80}$/;

const LOCAL_URL = /^local:\/\/[A-Za-z0-9_\-/.]+$/;
const LOOPBACK = new Set(["localhost", "127.0.0.1", "[::1]"]);

/**
 * URLs from agents are untrusted: only https: (no credentials), loopback http (a gateway on this
 * machine; the renderer then shows it only from the gateway's own origin) and the stand-in local://
 * asset scheme reach the renderer. A rejected URL is reported in meta.
 */
function safeUrl(v: string | null): { url: string | null; rejected: boolean } {
  if (v === null) return { url: null, rejected: false };
  try {
    const u = new URL(v);
    if (u.protocol === "https:" && !u.username && !u.password) return { url: u.href, rejected: false };
    // A gateway on this machine (a rehearsal on localhost): loopback http only, still host-gated where it renders.
    if (u.protocol === "http:" && !u.username && !u.password && LOOPBACK.has(u.hostname)) return { url: u.href, rejected: false };
    if (LOCAL_URL.test(v) && !v.includes("..")) return { url: v, rejected: false };
  } catch {
    // not a URL
  }
  return { url: null, rejected: true };
}

/** Result cards shown per search; the rest collapse into one "+N more" chip. */
const MAX_RESULTS = 5;

const yesNo = (b: boolean) => (b ? "Yes" : "No");
const prettyStatus = (s: string) => s.toLowerCase().replace(/_/g, " ");
/** "hr.shortlisted" → "shortlisted"; "touch.training" → "training". */
const suffix = (type: string) => type.slice(type.indexOf(".") + 1);

/** Formatted sheet value for each fact present in the payload. */
function factValues(facts: Obj): Partial<Record<FactName, string>> {
  const out: Partial<Record<FactName, string>> = {};
  if (has(facts, "incorporated_delaware")) out.incorporated_delaware = yesNo(bool(facts, "incorporated_delaware"));
  if (has(facts, "foreign_owner_25")) out.foreign_owner_25 = yesNo(bool(facts, "foreign_owner_25"));
  if (has(facts, "authorized_shares")) out.authorized_shares = formatInt(int(facts, "authorized_shares"));
  if (has(facts, "issued_shares")) out.issued_shares = formatInt(int(facts, "issued_shares"));
  if (has(facts, "gross_assets_cents")) out.gross_assets = formatCents(int(facts, "gross_assets_cents"));
  // Optional R&D facts (contract v1.1): null means "not given", same as absent.
  if (has(facts, "gross_receipts_cents") && facts.gross_receipts_cents !== null) out.gross_receipts = formatCents(int(facts, "gross_receipts_cents"));
  if (has(facts, "years_with_receipts") && facts.years_with_receipts !== null) out.years_with_receipts = formatInt(int(facts, "years_with_receipts"));
  if (has(facts, "stated_par")) {
    const [sign, whole, frac] = splitDecimal(decimal(facts, "stated_par"));
    if (sign) throw new PayloadError("stated_par: negative");
    out.stated_par = `$${whole}${frac ? `.${frac}` : ""}`;
  }
  return out;
}

/** The amount for cost.booked / tool.bought lives in the envelope (contract §3); canonical fixed-point. */
function envelopeCost(ev: UTaxEvent): string {
  if (ev.cost_usd === null || ev.cost_usd === undefined) throw new PayloadError("cost_usd: required");
  try {
    return normalizeDecimal(ev.cost_usd);
  } catch {
    throw new PayloadError("cost_usd: expected decimal string");
  }
}

// ---------------------------------------------------------------------------

function checkEnvelope(ev: unknown): string | null {
  if (typeof ev !== "object" || ev === null) return "not an object";
  const e = ev as Obj;
  if (typeof e.id !== "number" || !Number.isSafeInteger(e.id) || e.id < 1) return "id";
  if (typeof e.type !== "string") return "type";
  if (typeof e.ts !== "string") return "ts";
  if (!(ACTORS as readonly unknown[]).includes(e.actor)) return "actor";
  if (!(EVENT_STATUSES as readonly unknown[]).includes(e.status)) return "status";
  if (e.case_id !== null && typeof e.case_id !== "string") return "case_id";
  if (e.cost_usd !== null && e.cost_usd !== undefined && typeof e.cost_usd !== "string") return "cost_usd";
  return null;
}

type StepInput = Omit<CursorStep, "key" | "eventId" | "ts" | "caseId" | "pane"> & { pane?: Pane };

/**
 * Map one event to its steps. Returns the next state; never mutates `state`.
 * Malformed events come back as `skipped` with no steps.
 */
export function stepsForEvent(
  state: ScriptState,
  ev: UTaxEvent,
): { state: ScriptState; steps: CursorStep[]; skipped?: SkippedEvent } {
  const bad = checkEnvelope(ev);
  if (bad) {
    const id = typeof (ev as Partial<UTaxEvent>)?.id === "number" ? (ev as UTaxEvent).id : NaN;
    const type = typeof (ev as Partial<UTaxEvent>)?.type === "string" ? (ev as UTaxEvent).type : "?";
    return { state, steps: [], skipped: { eventId: id, type, reason: "bad_event", detail: bad } };
  }
  const pane = PANE_OF.get(ev.type);
  if (!pane) return { state, steps: [], skipped: { eventId: ev.id, type: ev.type, reason: "unknown_type" } };
  if (ev.status === "skipped") {
    return { state, steps: [], skipped: { eventId: ev.id, type: ev.type, reason: "status_skipped" } };
  }
  if ((pane === "mail" || pane === "sheet") && ev.case_id === null && ev.status === "ok") {
    // Without a case we can't know whose email or sheet this belongs on.
    return { state, steps: [], skipped: { eventId: ev.id, type: ev.type, reason: "bad_event", detail: "case_id required" } };
  }
  try {
    return mapEvent(state, ev, pane);
  } catch (e) {
    if (!(e instanceof PayloadError || e instanceof RangeError)) throw e;
    return { state, steps: [], skipped: { eventId: ev.id, type: ev.type, reason: "bad_payload", detail: e.message } };
  }
}

function mapEvent(state: ScriptState, ev: UTaxEvent, pane: Pane): { state: ScriptState; steps: CursorStep[] } {
  let camera = state.camera;
  let caseNames = state.caseNames; // copy-on-write: only email.received changes it
  let onScreen = state.onScreen;
  let approvalTypes = state.approvalTypes; // copy-on-write: only approval.requested changes it
  const out: CursorStep[] = [];
  let n = 0;
  // Never show a raw case id as a name: a /talk case has no masked name yet.
  const caseName = () => (ev.case_id !== null && Object.hasOwn(caseNames, ev.case_id) ? caseNames[ev.case_id] : NEW_CLIENT);
  const base = (p: Pane) => ({ eventId: ev.id, ts: ev.ts, caseId: ev.case_id, pane: p });

  const push = (s: StepInput) => {
    const p = s.pane ?? pane;
    if (p !== "rail" && camera !== p) {
      camera = p;
      out.push({ ...base(p), key: `${ev.id}:focus:${p}`, target: p, agent: null, action: "focus" });
    }
    if ((p === "mail" || p === "sheet") && ev.case_id !== null && onScreen[p] !== ev.case_id) {
      onScreen = { ...onScreen, [p]: ev.case_id };
      // Opening a new email shows its case by itself; anything else switches explicitly.
      if (s.action !== "open") {
        out.push({ ...base(p), key: `${ev.id}:switch:${p}`, target: p, agent: null, action: "switch", text: caseName(), meta: { case_id: ev.case_id } });
      }
    }
    out.push({ ...s, ...base(p), key: `${ev.id}:${n++}` });
  };
  const done = () => ({ state: { camera, caseNames, onScreen, approvalTypes }, steps: out });
  /**
   * D31: this event is a step of a CEO order. The line rides on the event's last step (no extra
   * hold time); the screen files it under its order: `order_id` first, else by department.
   * `match` (screen.ts OrderMatch): "id" files only by order_id (case work), "dept" may also match a
   * recent order naming its department (D31 actions), "handoff" is the Chief of Staff's first step.
   */
  const orderLine = (text: string, seat: string, match: "id" | "dept" | "handoff", extra: Meta = {}) => {
    const last = out.at(-1);
    if (!last) return;
    const order = orderRef(p);
    last.meta = { ...last.meta, ...extra, order_line: text, order_seat: seat, order_match: match, ...(order !== null ? { order_id: order } : {}) };
  };

  // The cursor shown for this event; a customer's own words get no agent cursor.
  const agent: Agent | null = ev.actor === "system" ? null : ev.actor;

  if (ev.status === "failed") {
    push({ target: pane, agent, action: "fail", text: ev.type });
    return done();
  }

  const p = obj(ev.payload, "payload");
  switch (ev.type) {
    case "email.received": {
      const masked = str(p, "masked_name");
      const from = str(p, "from_masked");
      const subject = clip(str(p, "subject"), MAX_EMAIL_SUBJECT);
      const body = clip(str(p, "body"), MAX_EMAIL_BODY);
      if (ev.case_id !== null) {
        const next: Record<string, string> = Object.create(null);
        Object.assign(next, caseNames, { [ev.case_id]: masked });
        caseNames = next;
      }
      push({ pane: "rail", target: "rail.cases", agent: null, action: "appear", text: masked });
      push({ target: "mail.message", agent, action: "open", text: body, meta: { from, subject, masked_name: masked } });
      break;
    }
    case "facts.extracted": {
      const values = factValues(obj(p.facts, "facts"));
      const quotes = obj(p.quotes, "quotes");
      const assumed = factList(p, "assumed");
      const quoted = FACT_ROWS.filter((f) => has(quotes, f));
      for (const f of quoted) push({ pane: "mail", target: `mail.quote.${f}`, agent, action: "highlight", text: str(quotes, f) });
      for (const f of FACT_ROWS) {
        const value = values[f];
        if (value === undefined) continue;
        const isAssumed = assumed.includes(f);
        if (quoted.includes(f)) {
          push({ pane: "sheet", target: `sheet.cell.${f}`, from: `mail.quote.${f}`, agent, action: "drag", text: value, ...(isAssumed ? { meta: { assumed: true } } : {}) });
        } else {
          push({ pane: "sheet", target: `sheet.cell.${f}`, agent, action: "fill", text: value, meta: { assumed: isAssumed } });
        }
      }
      break;
    }
    case "facts.missing": {
      const missing = factList(p, "missing");
      int(p, "asks_so_far");
      for (const f of FACT_ROWS.filter((x) => missing.includes(x))) {
        push({ target: `sheet.cell.${f}`, agent, action: "fill", text: "?", meta: { missing: true } });
      }
      break;
    }
    case "client.asked": {
      const missing = factList(p, "missing");
      const body = str(p, "body");
      push({ target: "mail.compose", agent, action: "appear", text: `Reply · asking for ${missing.length} facts` });
      push({ target: "mail.memo", agent, action: "type", text: body });
      push({ target: "mail.send", agent, action: "click" });
      push({ target: "mail.sent", agent: null, action: "appear", text: `✓ Sent · asked for ${missing.length} facts` });
      break;
    }
    case "memo.sent": {
      const to = str(p, "to_masked");
      const subject = str(p, "subject");
      const body = str(p, "body");
      const secs = int(p, "seconds_since_last_fact");
      push({ target: "mail.compose", agent, action: "appear", text: `To: ${to} · Subject: ${subject}` });
      push({ target: "mail.memo", agent, action: "type", text: body });
      push({ target: "mail.send", agent, action: "click" });
      push({ target: "mail.sent", agent: null, action: "appear", text: `✓ Sent · ${secs} s after last fact`, meta: { message_id: str(p, "message_id"), memo_ref: strOrNull(p, "memo_ref") } });
      orderLine(`${seatName(ev.actor)} sent the memo to ${caseName()}`, ev.actor, "id");
      break;
    }
    case "law.searched": {
      const query = str(p, "query");
      const results = arr(p, "results").map((r) => {
        const o = obj(r, "result");
        return `${str(o, "cite")} · ${str(o, "snippet")}`;
      });
      push({ target: "search.query", agent, action: "type", text: query });
      results.slice(0, MAX_RESULTS).forEach((text, i) => push({ target: `search.result.${i}`, agent: null, action: "appear", text }));
      if (results.length > MAX_RESULTS) {
        push({ target: "search.more", agent: null, action: "appear", text: `+${results.length - MAX_RESULTS} more results` });
      }
      break;
    }
    case "tax.computed": {
      const authorized = formatCents(nonNeg(p, "authorized_method_cents"));
      const assumedPar = formatCents(nonNeg(p, "assumed_par_method_cents"));
      const chosen = oneOf(p, "chosen", ["assumed_par", "authorized"] as const);
      const fee = formatCents(nonNeg(p, "annual_report_fee_cents"));
      const total = formatCents(nonNeg(p, "total_cents"));
      const due = str(p, "due_date");
      const f5472 = bool(p, "form5472_flag");
      const rnd = bool(p, "rnd_flag");
      push({ target: "sheet.cell.authorized_method", agent, action: "fill", text: authorized });
      push({ target: "sheet.cell.assumed_par_method", agent, action: "fill", text: assumedPar });
      const [win, lose] = chosen === "assumed_par" ? ["assumed_par_method", "authorized_method"] : ["authorized_method", "assumed_par_method"];
      push({ target: `sheet.cell.${win}`, agent: null, action: "mark", text: "win" });
      push({ target: `sheet.cell.${lose}`, agent: null, action: "mark", text: "lose" });
      push({ target: "sheet.cell.annual_report_fee", agent, action: "fill", text: fee });
      push({ target: "sheet.cell.total", agent, action: "fill", text: total, meta: { due_date: due } });
      push({ target: "sheet.cell.total", agent: null, action: "mark", text: "win" });
      if (f5472) push({ target: "sheet.flag.form5472", agent, action: "appear", text: "Form 5472 may be required" });
      if (rnd) push({ target: "sheet.flag.rnd", agent, action: "appear", text: "May qualify for R&D payroll credit" });
      orderLine(`${seatName(ev.actor)} estimated ${total} for ${caseName()}`, ev.actor, "id");
      break;
    }
    case "qa.passed":
    case "qa.failed": {
      const checks = arr(p, "checks").map((c) => {
        const o = obj(c, "check");
        return { field: str(o, "field"), ok: bool(o, "ok"), note: str(o, "note") };
      });
      const allOk = checks.every((c) => c.ok);
      if (ev.type === "qa.passed" ? !allOk : allOk) throw new PayloadError("checks contradict the verdict");
      for (const c of checks) {
        push({ target: `sheet.cell.${c.field}`, agent, action: c.ok ? "tick" : "circle", text: c.note });
      }
      // The BLOCKED card comes only from case.blocked: a failed QA can also send
      // the case back to intake (re-ask), and the card must match the log.
      push({ target: "sheet.qa", agent: null, action: "appear", text: ev.type === "qa.passed" ? "✓ QA" : "✗ QA" });
      break;
    }
    case "call.booked": {
      const start = str(p, "start");
      const minutes = int(p, "minutes");
      const invitee = str(p, "invitee_masked");
      // An unknown kind reads as a client call (a new value must not drop the booking).
      const kind = p.kind === "candidate" ? "candidate" : "client";
      if (kind === "candidate") {
        const who = candidateLabel(invitee);
        push({ target: "calendar.block", agent, action: "drag", text: `${who} · ${minutes}-min call`, meta: { start, minutes, kind } });
        orderLine(`${seatName(ev.actor)} booked a ${minutes}-min call with ${who}`, ev.actor, "dept");
      } else {
        push({ target: "calendar.block", agent, action: "drag", text: `${caseName()} · ${minutes}-min review`, meta: { start, minutes, invitee } });
        orderLine(`${seatName(ev.actor)} booked a ${minutes}-min review with ${caseName()}`, ev.actor, "id");
      }
      break;
    }
    case "ad.created": {
      str(p, "kind"); // client | hiring; another value still shows as an ad
      const hiring = p.kind === "hiring";
      const headline = clip(str(p, "headline"), 80);
      const image = safeUrl(str(p, "image_url"));
      if (image.url) push({ target: "social.image", agent, action: "drag", meta: { image_url: image.url } });
      push({ target: "social.caption", agent, action: "type", text: headline, meta: { channel: hiring ? "hiring ad" : "ad", image_url: image.url, image_rejected: image.rejected } });
      orderLine(`${seatName(ev.actor)} made ${hiring ? "a hiring ad image" : "an ad image"}`, ev.actor, "dept", image.url ? { order_image: image.url } : {});
      break;
    }
    case "hr.sourced": {
      const count = nonNeg(p, "count");
      str(p, "source");
      // Counts only: no lead's label or profile reaches the projector (v1.11.1).
      push({ target: "search.query", agent, action: "type", text: "Public profiles for the open gig", meta: { source: "Monid public profiles" } });
      push({
        target: "search.result.0",
        agent: null,
        action: "appear",
        text: count ? `${count} public profile${count === 1 ? "" : "s"} found · names stay off screen` : "No public profiles found",
      });
      orderLine(count ? `${seatName(ev.actor)} found ${count} public profile${count === 1 ? "" : "s"}` : `${seatName(ev.actor)} searched public profiles: none found`, ev.actor, "dept");
      break;
    }
    case "hr.outreach": {
      str(p, "candidate_id");
      const who = candidateLabel(str(p, "label"));
      str(p, "slot");
      push({ target: "rail.hiring", agent, action: "count", text: "contacted", meta: { stage: "contacted" } });
      orderLine(`${seatName(ev.actor)} emailed ${who} with a call invite`, ev.actor, "dept");
      break;
    }
    case "client.followed_up": {
      const kind = str(p, "kind"); // memo_check | missing_facts | call_reminder; another value is a plain follow-up
      const what = Object.hasOwn(FOLLOW_UP_WORDS, kind) ? FOLLOW_UP_WORDS[kind] : "follow-up";
      push({ target: "mail.compose", agent, action: "appear", text: `Follow-up to ${caseName()} · ${what}` });
      push({ target: "mail.send", agent, action: "click" });
      push({ target: "mail.sent", agent: null, action: "appear", text: "✓ Follow-up sent" });
      orderLine(`${seatName(ev.actor)} followed up with ${caseName()} (${what})`, ev.actor, "dept");
      break;
    }
    case "post.published": {
      const channel = str(p, "channel");
      const text = str(p, "text");
      const url = safeUrl(strOrNull(p, "url"));
      const image = safeUrl(strOrNull(p, "image_url"));
      if (image.url) push({ target: "social.image", agent, action: "drag", meta: { image_url: image.url } });
      push({ target: "social.caption", agent, action: "type", text, meta: { channel, image_url: image.url, image_rejected: image.rejected } });
      push({ target: "social.post", agent, action: "click" });
      push({ target: "social.posted", agent: null, action: "appear", text: `Posted to ${channel}`, meta: { url: url.url, url_rejected: url.rejected } });
      orderLine(`${seatName(ev.actor)} posted to ${channelName(channel)}`, ev.actor, "dept");
      break;
    }
    case "dm.drafted": {
      push({ target: "social.dm", agent, action: "type", text: str(p, "text"), meta: { recipient: str(p, "recipient_label") } });
      orderLine(`${seatName(ev.actor)} drafted a DM for your OK`, ev.actor, "dept");
      break;
    }
    case "dm.sent_by_human": {
      const recipient = str(p, "recipient_label");
      str(p, "text");
      push({ target: "social.dm_send", agent, action: "click", meta: { recipient } });
      break;
    }
    case "screen.call": {
      // A candidate's own words and id stay off the projector (hiring is counts only):
      // the phone pane shows that a screen happened and how long it took.
      str(p, "candidate_id");
      str(p, "transcript_excerpt");
      const seconds = nonNeg(p, "seconds");
      push({ target: "phone.dial", agent, action: "dial", text: seconds < 60 ? `${seconds} s` : `${Math.round(seconds / 60)} min`, meta: { seconds } });
      break;
    }
    case "phone.checked": {
      // v1.7: the screenshot is private (it shows the real outgoing mail); the card says what was checked.
      const ok = bool(p, "rendered_ok");
      const note = strOrNull(p, "status_note");
      push({
        target: "phone.check",
        agent,
        action: ok ? "tick" : "circle",
        text: ok ? "Memo checked on a cloud phone" : "Memo failed to render on a cloud phone",
        meta: { status_note: note },
      });
      break;
    }
    case "case.status": {
      const from = str(p, "from");
      const to = str(p, "to");
      push({ target: "rail.cases", agent: null, action: "status", text: prettyStatus(to), meta: { from, to } });
      break;
    }
    case "case.blocked": {
      push({ target: "rail.cases", agent: null, action: "block", text: str(p, "reason"), meta: { needs: str(p, "needs") } });
      break;
    }
    case "case.flagged": {
      if (ev.case_id === null) throw new PayloadError("case_id: required");
      const kind = str(p, "kind"); // possible_injection | unrealistic_inputs (display only)
      push({ target: "rail.cases", agent, action: "flag", text: prettyStatus(kind), meta: { kind } });
      break;
    }
    case "case.not_eligible": {
      push({ target: "rail.cases", agent: null, action: "status", text: "not eligible", meta: { reason: str(p, "reason") } });
      break;
    }
    case "crm.sync_requested":
    case "crm.synced": {
      const object = str(p, "object");
      push({ target: "rail.crm", agent: null, action: "appear", text: `CRM ${object} · ${prettyStatus(str(p, "stage"))}` });
      break;
    }
    case "hr.applicant":
    case "hr.screened":
    case "hr.shortlisted":
    case "hr.hired":
    case "hr.declined":
    case "hr.questionnaire_sent":
    case "hr.decision_mailed": {
      // Never put a candidate's name (or the free-text note) on the projector:
      // hiring shows as counts only (controller rule, contract v1.6).
      const stage = suffix(ev.type);
      str(p, "name_masked");
      push({ target: "rail.hiring", agent, action: "count", text: stage.replace(/_/g, " "), meta: { stage, candidate_id: str(p, "candidate_id") } });
      break;
    }
    case "voice.command": {
      const text = str(p, "text");
      const channel = str(p, "channel"); // display only: vocabulary drift must not drop the event
      const speaker = has(p, "speaker") ? oneOf(p, "speaker", ["customer", "ceo"] as const) : ev.actor === "ceo" ? "ceo" : "customer";
      const caseId = strOrNull(p, "case_id");
      // v1.6.4: a /talk case carries its masked name; show it on the rail like an emailed case.
      const masked = strOrNull(p, "masked_name");
      if (speaker === "customer" && ev.case_id !== null && masked) {
        const known = Object.hasOwn(caseNames, ev.case_id);
        const next: Record<string, string> = Object.create(null);
        Object.assign(next, caseNames, { [ev.case_id]: masked });
        caseNames = next;
        if (!known) push({ pane: "rail", target: "rail.cases", agent: null, action: "appear", text: masked });
      }
      // A customer's words are inbound speech, not an agent action: no cursor,
      // and unmoderated public text is clipped before any renderer sees it.
      const shown = clip(text, MAX_SPOKEN_TEXT);
      push({ target: "rail.voice", agent: speaker === "ceo" ? agent : null, action: "say", text: shown, meta: { channel, speaker, case_id: caseId } });
      break;
    }
    case "ceo.notified": {
      const channel = str(p, "channel");
      const reason = str(p, "reason");
      push({ target: "rail.notify", agent, action: "appear", text: str(p, "subject"), meta: { channel, reason, summary: str(p, "summary") } });
      break;
    }
    case "approval.requested": {
      // The free-text summary can name a candidate ("declined mail: Candidate B"):
      // the projector shows only the action type.
      const approvalId = str(p, "approval_id");
      const actionType = str(p, "action_type");
      str(p, "summary");
      const nextTypes: Record<string, string> = Object.create(null);
      Object.assign(nextTypes, approvalTypes, { [approvalId]: actionType });
      approvalTypes = nextTypes;
      push({ target: "rail.touches", agent: null, action: "appear", text: `${words(actionType)} approval`, meta: { approval_id: approvalId, action_type: actionType } });
      orderLine(`Waiting for your OK: ${words(actionType)}`, approvalSeat(actionType), "id");
      break;
    }
    case "touch.training":
    case "touch.operating":
    case "touch.engineering": {
      // Notes are free text ("decline Candidate B"): never shown. Label by kind and the approval's action type.
      str(p, "note");
      const kind = suffix(ev.type);
      const approvalId = strOrNull(p, "approval_id");
      const actionType = approvalId !== null && Object.hasOwn(approvalTypes, approvalId) ? approvalTypes[approvalId] : null;
      // v1.8: how the CEO gave it (absent on older events = click); the rail marks voice with a mic.
      // An odd value never costs the touch itself: it just shows without the mic.
      const channel = typeof p.channel === "string" ? p.channel : "click";
      push({
        target: "rail.touches",
        agent,
        action: "count",
        text: actionType ? `${kind} · ${words(actionType)}` : kind,
        meta: { kind, approval_id: approvalId, action_type: actionType, channel },
      });
      break;
    }
    case "trust.changed": {
      const actionType = str(p, "action_type");
      const from = str(p, "from");
      const to = str(p, "to");
      push({ target: "rail.trust", agent: null, action: "status", text: `${actionType} → ${to}`, meta: { action_type: actionType, from, to, reason: str(p, "reason") } });
      break;
    }
    case "cost.booked": {
      const cost = envelopeCost(ev);
      push({
        target: "rail.money",
        agent: null,
        action: "count",
        text: formatUsd(cost),
        meta: { provider: str(p, "provider"), model: str(p, "model"), estimated: bool(p, "estimated"), cost_usd: cost },
      });
      break;
    }
    case "job.posted": {
      const job = jobId(p);
      const template = clip(str(p, "template"), 40);
      const price = tokens(p, "price_usd");
      const headline = clip(str(p, "headline"), 80);
      push({ target: "market.job", agent, action: "appear", text: headline, meta: { job_id: job, template, price } });
      orderLine(`Marketing posted a ${words(template)} job · ${price} pathUSD`, ev.actor, "dept");
      break;
    }
    case "job.claimed": {
      const job = jobId(p);
      const contractor = clip(str(p, "contractor"), 40);
      // An outside agent: no seat cursor of ours; the pane draws a grey contractor cursor.
      push({ target: "market.claim", agent: null, action: "click", text: contractor, meta: { job_id: job, cursor: "contractor" } });
      orderLine(`${contractor} (an outside agent) took the job`, "mkt", "dept");
      break;
    }
    case "job.submitted": {
      const job = jobId(p);
      str(p, "contractor");
      str(p, "sha256"); // the artifact stays private until paid: only "a card arrived" shows
      push({ target: "market.thumb", agent: null, action: "appear", meta: { job_id: job } });
      break;
    }
    case "job.verified":
    case "job.failed": {
      const job = jobId(p);
      const checks = checkList(p);
      checks.forEach((c, i) => push({ target: `market.check.${i}`, agent: null, action: c.ok ? "tick" : "circle", text: c.name, meta: { job_id: job } }));
      if (ev.type === "job.failed") {
        const reason = str(p, "reason");
        push({ target: "market.status", agent: null, action: "status", text: reason === "expired" ? "expired" : reason === "contractor_error" ? "contractor error" : "checks failed", meta: { job_id: job } });
        orderLine("The contractor's work failed our checks: not paid", "mkt", "dept");
      } else orderLine("Our code checked the contractor's work: all checks passed", "mkt", "dept");
      break;
    }
    case "payment.sent": {
      const job = jobId(p);
      const contractor = clip(str(p, "contractor"), 40);
      const amount = tokens(p, "amount_usd");
      const token = clip(str(p, "token"), 12);
      const tx = str(p, "tx_hash");
      const link = safeUrl(strOrNull(p, "explorer_url"));
      const ledger = ev.provider !== "tempo"; // provider local: a ledger token, no chain transaction
      push({
        target: "market.pay",
        from: "market.wallet.firm",
        agent: null,
        action: "drag",
        text: `${amount} ${token}`,
        meta: { job_id: job, contractor, tx: ledger ? "" : shortTx(tx), explorer_url: ledger ? null : link.url, ledger },
      });
      orderLine(`U-Tax paid ${contractor} ${amount} ${token}${ledger ? " (ledger token)" : ""}`, "mkt", "dept");
      break;
    }
    case "job.delivered": {
      const job = jobId(p);
      const path = str(p, "image_url");
      push({ target: "market.delivered", agent: null, action: "appear", meta: { job_id: job, image_path: ASSET_PATH.test(path) && !path.includes("..") ? path : null } });
      break;
    }
    case "agent.sale": {
      const amount = tokens(p, "amount_usd");
      const token = clip(str(p, "token"), 12);
      str(p, "payer_masked"); // an agent's wallet, masked: the projector names no payer
      push({ target: "rail.sale", agent: null, action: "count", text: `An agent bought a franchise-tax check · ${amount} ${token}` });
      break;
    }
    case "tool.bought": {
      const cost = envelopeCost(ev);
      const tool = str(p, "tool");
      push({
        target: "rail.money",
        agent,
        action: "count",
        text: `${tool} · ${formatUsd(cost)}`,
        meta: { provider: str(p, "provider"), cost_usd: cost },
      });
      orderLine(`${seatName(ev.actor)} bought ${toolLabel(tool)} · ${formatUsd(cost)}`, ev.actor, "id");
      break;
    }
    case "ceo.proposed":
    case "ceo.confirmed":
    case "ceo.cancelled":
      str(p, "proposal_id");
      break;
    case "naive.confirm": {
      // "Naïve check: board_write allowed for Chief of Staff": the firm vetting its own tools.
      const decision = str(p, "decision");
      if (decision !== "allow" && decision !== "deny") throw new PayloadError("decision: expected allow or deny");
      const seat = str(p, "agent");
      const who = seatName(seat);
      const tool = toolLabel(str(p, "tool"));
      push({
        target: "rail.naive",
        agent: null,
        action: "appear",
        text: `Naïve check: ${tool} ${decision === "allow" ? "allowed" : "denied"} for ${who}`,
        meta: { decision, tool, agent: seat },
      });
      // D31: the Chief of Staff handing an order to a department is the order's first step.
      if (seat === "cos" && tool === "send_to_agent") orderLine(decision === "allow" ? "Chief of Staff handed it to the team" : "Chief of Staff was stopped handing it on", "cos", "handoff");
      break;
    }
    case "tool.called": {
      const tool = toolLabel(str(p, "tool"));
      const raw = str(p, "status");
      // done | needs_approval | refused | error; a new value still shows, as "done" would hide a failure.
      const status = raw === "done" || raw === "needs_approval" || raw === "refused" || raw === "error" ? raw : "other";
      const order = orderRef(p);
      push({ target: "rail.tool", agent: null, action: "status", text: tool, meta: { seat: ev.actor, tool, status, ...(order !== null ? { order_id: order } : {}) } });
      break;
    }
    case "brief.generated": {
      const text = str(p, "text");
      obj(p.counts, "counts");
      push({ target: "rail.brief", agent, action: "say", text, meta: { brief_event_id: ev.id } });
      break;
    }
  }
  return done();
}

/**
 * Live mode: apply new events to a cursor. The batch is sorted; ids at or
 * below `cursor.lastId` are dropped silently (already applied, e.g. an SSE
 * replay after reconnect). The caller must deliver events in id order across
 * batches (the SSE stream does); a late lower id is dropped, never inserted,
 * so already-emitted steps never change.
 */
export function appendEvents(
  cursor: ScriptCursor,
  events: readonly UTaxEvent[],
  opts: ScriptOptions = {},
): { cursor: ScriptCursor; steps: CursorStep[]; skipped: SkippedEvent[]; gaps: IdGap[]; accepted: UTaxEvent[] } {
  const log = opts.log ?? defaultLog;
  const onGap = opts.onGap ?? ((g: IdGap) => console.info(`[theater] ids ${g.from}..${g.to} not in the log (gap)`));
  const gaps: IdGap[] = [];
  const applied = (e: UTaxEvent, last: number) => Number.isSafeInteger(e?.id) && e.id >= 1 && e.id <= last;
  // Invalid ids are kept so validation reports them as bad_event.
  const fresh = events.filter((e) => !applied(e, cursor.lastId));
  const ordered = [...fresh].sort((a, b) => (Number.isFinite(a?.id) ? a.id : 0) - (Number.isFinite(b?.id) ? b.id : 0));
  let { state, lastId } = cursor;
  const steps: CursorStep[] = [];
  const skipped: SkippedEvent[] = [];
  const accepted: UTaxEvent[] = [];
  let prev: UTaxEvent | undefined;
  const skip = (s: SkippedEvent) => {
    skipped.push(s);
    log(s);
  };
  for (const ev of ordered) {
    if (applied(ev, lastId)) {
      // Same id twice in one batch: an exact repeat is a replay; different content is a bad log.
      if (prev && prev.id === ev.id && JSON.stringify(prev) !== JSON.stringify(ev)) {
        skip({ eventId: ev.id, type: typeof ev.type === "string" ? ev.type : "?", reason: "bad_event", detail: "duplicate id with different content" });
      }
      continue;
    }
    prev = ev;
    if (Number.isSafeInteger(ev?.id) && ev.id > lastId + 1 && lastId > 0) {
      // Ids may skip (contract §2). The event still applies; note the gap as info.
      const gap = { from: lastId + 1, to: ev.id - 1 };
      gaps.push(gap);
      onGap(gap);
    }
    const res = stepsForEvent(state, ev);
    state = res.state;
    for (const s of res.steps) steps.push(s);
    if (res.skipped) skip(res.skipped);
    if (!res.skipped || (res.skipped.reason !== "bad_event" && res.skipped.reason !== "bad_payload")) accepted.push(ev);
    if (Number.isSafeInteger(ev?.id) && ev.id >= 1) lastId = Math.max(lastId, ev.id);
  }
  return { cursor: { state, lastId }, steps, skipped, gaps, accepted };
}

/** The whole log at once (fixture, replay). Sorted by id; exact duplicates collapse, conflicting ones are reported. */
export function buildScript(events: readonly UTaxEvent[], opts: ScriptOptions = {}): Script {
  const { steps, skipped, accepted } = appendEvents(startScript(), events, opts);
  return { steps, skipped, accepted };
}
