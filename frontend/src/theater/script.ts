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

export type Pane = "mail" | "sheet" | "search" | "calendar" | "social" | "phone" | "rail";

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
export function startScript(seed: { caseNames?: Record<string, string>; lastId?: number | string } = {}): ScriptCursor {
  const caseNames: Record<string, string> = Object.create(null);
  for (const [id, name] of Object.entries(seed.caseNames ?? {})) caseNames[id] = name;
  // A seed may come from localStorage as a string ("57"); anything else that isn't a
  // non-negative integer starts from 0 and says so (it would replay history).
  const raw = typeof seed.lastId === "string" && /^\d+$/.test(seed.lastId.trim()) ? Number(seed.lastId.trim()) : seed.lastId;
  const valid = typeof raw === "number" && Number.isSafeInteger(raw) && raw >= 0;
  if (seed.lastId !== undefined && !valid) console.warn(`[theater] ignoring invalid lastId seed ${JSON.stringify(seed.lastId)}; starting from 0`);
  const lastId = valid ? raw : 0;
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
]);

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

const LOCAL_URL = /^local:\/\/[A-Za-z0-9_\-/.]+$/;

/**
 * URLs from agents are untrusted: only https: (no credentials) and the stand-in
 * local:// asset scheme reach the renderer. A rejected URL is reported in meta.
 */
function safeUrl(v: string | null): { url: string | null; rejected: boolean } {
  if (v === null) return { url: null, rejected: false };
  try {
    const u = new URL(v);
    if (u.protocol === "https:" && !u.username && !u.password) return { url: u.href, rejected: false };
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
      push({ target: "mail.sent", agent: null, action: "appear", text: `✓ Sent · ${secs} s after last fact`, meta: { message_id: str(p, "message_id") } });
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
      push({ target: "calendar.block", agent, action: "drag", text: `${caseName()} · ${minutes}-min review`, meta: { start, minutes, invitee } });
      break;
    }
    case "post.published": {
      const channel = str(p, "channel");
      const text = str(p, "text");
      const url = safeUrl(strOrNull(p, "url"));
      const image = safeUrl(strOrNull(p, "image_url"));
      if (image.url) push({ target: "social.image", agent, action: "drag", meta: { image_url: image.url } });
      push({ target: "social.caption", agent, action: "type", text, meta: { channel, image_rejected: image.rejected } });
      push({ target: "social.post", agent, action: "click" });
      push({ target: "social.posted", agent: null, action: "appear", text: `Posted to ${channel}`, meta: { url: url.url, url_rejected: url.rejected } });
      break;
    }
    case "dm.drafted": {
      push({ target: "social.dm", agent, action: "type", text: str(p, "text"), meta: { recipient: str(p, "recipient_label") } });
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
      const ok = bool(p, "rendered_ok");
      const shot = safeUrl(strOrNull(p, "screenshot_url"));
      push({
        target: "phone.check",
        agent,
        action: ok ? "tick" : "circle",
        text: ok ? "Memo renders on phone" : "Memo failed to render on phone",
        meta: { screenshot_url: shot.url, screenshot_rejected: shot.rejected },
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
      push({ target: "rail.touches", agent, action: "count", text: actionType ? `${kind} · ${words(actionType)}` : kind, meta: { kind, approval_id: approvalId, action_type: actionType } });
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
