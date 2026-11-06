// Event log → cursor script. Pure and deterministic: no clock, no randomness.
//
// Premise 7 (docs/designs/u-tax.md): every animation comes from a real logged
// event. Each step carries the id of the event that caused it; unknown event
// types produce no steps and are reported in `skipped` (and logged).
//
// Prefix property: the script for events[0..n] is a prefix of the script for
// events[0..m] when n < m, so live mode can append steps as events arrive.

import type {
  Actor,
  Agent,
  ApprovalRequested,
  BriefGenerated,
  CallBooked,
  CaseBlocked,
  CaseNotEligible,
  CaseStatus,
  CeoNotified,
  ClientAsked,
  CostBooked,
  CrmSync,
  DmPayload,
  EmailReceived,
  FactName,
  FactsExtracted,
  FactsMissing,
  HrPayload,
  LawSearched,
  MemoSent,
  PhoneChecked,
  PostPublished,
  QaResult,
  ScreenCall,
  TaxComputed,
  ToolBought,
  TouchPayload,
  TrustChanged,
  UTaxEvent,
  VoiceCommand,
} from "./events";
import { formatCents, formatInt, formatUsd } from "./format";

export type Pane = "mail" | "sheet" | "search" | "calendar" | "social" | "phone" | "rail";

export type Action =
  | "focus" // camera moves to the pane (no cursor)
  | "open" // open a message / document
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
  | "block" // red BLOCKED card
  | "count" // a rail counter ticks
  | "say" // spoken words (voice command, brief)
  | "dial" // phone call starts
  | "fail"; // the event itself has status "failed"

export type Meta = Record<string, string | number | boolean | null>;

export interface CursorStep {
  /** Stable key: `<eventId>:<index within event>`. */
  key: string;
  eventId: number;
  ts: string;
  caseId: string | null;
  pane: Pane;
  /** Stable element key, e.g. "sheet.cell.total" or "mail.quote.authorized_shares". */
  target: string;
  /** Cursor owner; null = no cursor (system updates, camera moves). */
  agent: Agent | null;
  action: Action;
  text?: string;
  /** Drag source element key. */
  from?: string;
  meta?: Meta;
}

export type SkipReason = "unknown_type" | "status_skipped" | "bad_payload";

export interface SkippedEvent {
  eventId: number;
  type: string;
  reason: SkipReason;
}

export interface ScriptState {
  /** Pane the camera is on (rail steps don't move the camera). */
  camera: Pane | null;
  /** case_id → masked name, learned from email.received. */
  caseNames: Record<string, string>;
}

export interface Script {
  steps: CursorStep[];
  skipped: SkippedEvent[];
}

export interface ScriptOptions {
  /** Called once per skipped event. Default: console.warn. */
  log?: (skipped: SkippedEvent) => void;
}

export const initialState = (): ScriptState => ({ camera: null, caseNames: {} });

/** Sheet rows for extracted facts, in display order. */
export const FACT_ROWS: FactName[] = [
  "incorporated_delaware",
  "authorized_shares",
  "issued_shares",
  "gross_assets",
  "stated_par",
  "foreign_owner_25",
  "gross_receipts",
  "years_with_receipts",
];

const yesNo = (b: boolean) => (b ? "Yes" : "No");

function factValue(facts: FactsExtracted["facts"], fact: FactName): string | undefined {
  switch (fact) {
    case "incorporated_delaware":
      return facts.incorporated_delaware === undefined ? undefined : yesNo(facts.incorporated_delaware);
    case "foreign_owner_25":
      return facts.foreign_owner_25 === undefined ? undefined : yesNo(facts.foreign_owner_25);
    case "authorized_shares":
      return facts.authorized_shares === undefined ? undefined : formatInt(facts.authorized_shares);
    case "issued_shares":
      return facts.issued_shares === undefined ? undefined : formatInt(facts.issued_shares);
    case "gross_assets":
      return facts.gross_assets_cents === undefined ? undefined : formatCents(facts.gross_assets_cents);
    case "gross_receipts":
      return facts.gross_receipts_cents === undefined ? undefined : formatCents(facts.gross_receipts_cents);
    case "years_with_receipts":
      return facts.years_with_receipts === undefined ? undefined : formatInt(facts.years_with_receipts);
    case "stated_par":
      return facts.stated_par === undefined ? undefined : `$${facts.stated_par}`;
  }
}

/** QA check field → sheet cell key. Unknown fields keep their own name. */
const QA_CELL: Record<string, string> = {
  total: "total",
  authorized_method: "authorized_method",
  assumed_par_method: "assumed_par_method",
  annual_report_fee: "annual_report_fee",
};

const prettyStatus = (s: string) => s.toLowerCase().replace(/_/g, " ");

/** Main pane for each known type: used for camera moves and "fail" steps. */
const PANE_OF: Record<string, Pane> = {
  "email.received": "mail",
  "client.asked": "mail",
  "memo.sent": "mail",
  "facts.extracted": "sheet",
  "facts.missing": "sheet",
  "tax.computed": "sheet",
  "qa.passed": "sheet",
  "qa.failed": "sheet",
  "law.searched": "search",
  "call.booked": "calendar",
  "post.published": "social",
  "dm.drafted": "social",
  "dm.sent_by_human": "social",
  "phone.checked": "phone",
  "screen.call": "phone",
  "case.status": "rail",
  "case.blocked": "rail",
  "case.not_eligible": "rail",
  "crm.sync_requested": "rail",
  "crm.synced": "rail",
  "hr.applicant": "rail",
  "hr.screened": "rail",
  "hr.shortlisted": "rail",
  "hr.hired": "rail",
  "hr.declined": "rail",
  "voice.command": "rail",
  "ceo.notified": "rail",
  "approval.requested": "rail",
  "touch.training": "rail",
  "touch.operating": "rail",
  "touch.engineering": "rail",
  "trust.changed": "rail",
  "cost.booked": "rail",
  "tool.bought": "rail",
  "brief.generated": "rail",
};

export const KNOWN_TYPES: readonly string[] = Object.keys(PANE_OF);

const cursorOf = (actor: Actor): Agent | null => (actor === "system" ? null : actor);

type StepInput = Omit<CursorStep, "key" | "eventId" | "ts" | "caseId" | "pane"> & {
  pane?: Pane;
};

/**
 * Map one event to its steps. Returns the next state; never mutates `state`.
 * Throws only on a malformed payload (buildScript turns that into a skip).
 */
export function stepsForEvent(
  state: ScriptState,
  ev: UTaxEvent,
): { state: ScriptState; steps: CursorStep[]; skipped?: SkippedEvent } {
  const pane = PANE_OF[ev.type];
  if (!pane) {
    return { state, steps: [], skipped: { eventId: ev.id, type: ev.type, reason: "unknown_type" } };
  }
  if (ev.status === "skipped") {
    return { state, steps: [], skipped: { eventId: ev.id, type: ev.type, reason: "status_skipped" } };
  }

  const next: ScriptState = { camera: state.camera, caseNames: { ...state.caseNames } };
  const out: CursorStep[] = [];
  const agent = cursorOf(ev.actor);
  const push = (s: StepInput) => {
    const p = s.pane ?? pane;
    if (p !== "rail" && next.camera !== p) {
      next.camera = p;
      out.push({
        key: `${ev.id}:${out.length}`,
        eventId: ev.id,
        ts: ev.ts,
        caseId: ev.case_id,
        pane: p,
        target: p,
        agent: null,
        action: "focus",
      });
    }
    out.push({ ...s, pane: p, key: `${ev.id}:${out.length}`, eventId: ev.id, ts: ev.ts, caseId: ev.case_id });
  };
  const caseName = () =>
    (ev.case_id && next.caseNames[ev.case_id]) || ev.case_id || "client";

  if (ev.status === "failed") {
    push({ target: pane, agent, action: "fail", text: ev.type });
    return { state: next, steps: out };
  }

  const p = ev.payload as unknown;
  switch (ev.type) {
    case "email.received": {
      const e = p as EmailReceived;
      if (ev.case_id) next.caseNames[ev.case_id] = e.masked_name;
      push({ pane: "rail", target: "rail.cases", agent: null, action: "appear", text: e.masked_name });
      push({
        target: "mail.message",
        agent,
        action: "open",
        text: e.body,
        meta: { from: e.from_masked, subject: e.subject, masked_name: e.masked_name },
      });
      break;
    }
    case "facts.extracted": {
      const f = p as FactsExtracted;
      const quoted = FACT_ROWS.filter((fact) => f.quotes[fact] !== undefined);
      for (const fact of quoted) {
        push({ pane: "mail", target: `mail.quote.${fact}`, agent, action: "highlight", text: f.quotes[fact] });
      }
      for (const fact of quoted) {
        const value = factValue(f.facts, fact);
        if (value === undefined) continue;
        push({ pane: "sheet", target: `sheet.cell.${fact}`, from: `mail.quote.${fact}`, agent, action: "drag", text: value });
      }
      for (const fact of FACT_ROWS.filter((x) => f.assumed.includes(x))) {
        const value = factValue(f.facts, fact);
        if (value === undefined) continue;
        push({ pane: "sheet", target: `sheet.cell.${fact}`, agent, action: "fill", text: value, meta: { assumed: true } });
      }
      break;
    }
    case "facts.missing": {
      const m = p as FactsMissing;
      for (const fact of FACT_ROWS.filter((x) => m.missing.includes(x))) {
        push({ target: `sheet.cell.${fact}`, agent, action: "fill", text: "?", meta: { missing: true } });
      }
      break;
    }
    case "client.asked":
    case "memo.sent": {
      const isMemo = ev.type === "memo.sent";
      const to = isMemo ? (p as MemoSent).to_masked : null;
      const subject = isMemo ? (p as MemoSent).subject : "Re: your franchise tax";
      const body = (p as MemoSent | ClientAsked).body;
      push({ target: "mail.compose", agent, action: "appear", text: to ? `To: ${to} · Subject: ${subject}` : `Reply · ${subject}` });
      push({ target: "mail.memo", agent, action: "type", text: body });
      push({ target: "mail.send", agent, action: "click" });
      const sentText = isMemo
        ? `✓ Sent · ${(p as MemoSent).seconds_since_last_fact} s after last fact`
        : `✓ Sent · asked for ${(p as ClientAsked).missing.length} facts`;
      push({ target: "mail.sent", agent: null, action: "appear", text: sentText });
      break;
    }
    case "law.searched": {
      const l = p as LawSearched;
      push({ target: "search.query", agent, action: "type", text: l.query });
      l.results.forEach((r, i) =>
        push({ target: `search.result.${i}`, agent: null, action: "appear", text: `${r.cite} · ${r.snippet}` }),
      );
      break;
    }
    case "tax.computed": {
      const t = p as TaxComputed;
      push({ target: "sheet.cell.authorized_method", agent, action: "fill", text: formatCents(t.authorized_method_cents) });
      push({ target: "sheet.cell.assumed_par_method", agent, action: "fill", text: formatCents(t.assumed_par_method_cents) });
      const [win, lose] =
        t.chosen === "assumed_par"
          ? ["assumed_par_method", "authorized_method"]
          : ["authorized_method", "assumed_par_method"];
      push({ target: `sheet.cell.${win}`, agent: null, action: "mark", text: "win" });
      push({ target: `sheet.cell.${lose}`, agent: null, action: "mark", text: "lose" });
      push({ target: "sheet.cell.annual_report_fee", agent, action: "fill", text: formatCents(t.annual_report_fee_cents) });
      push({ target: "sheet.cell.total", agent, action: "fill", text: formatCents(t.total_cents), meta: { due_date: t.due_date } });
      push({ target: "sheet.cell.total", agent: null, action: "mark", text: "win" });
      if (t.form5472_flag) push({ target: "sheet.flag.form5472", agent, action: "appear", text: "Form 5472 may be required" });
      if (t.rnd_flag) push({ target: "sheet.flag.rnd", agent, action: "appear", text: "May qualify for R&D payroll credit" });
      break;
    }
    case "qa.passed":
    case "qa.failed": {
      const q = p as QaResult;
      for (const c of q.checks) {
        const cell = QA_CELL[c.field] ?? c.field;
        push({ target: `sheet.cell.${cell}`, agent, action: c.ok ? "tick" : "circle", text: c.note });
      }
      const passed = ev.type === "qa.passed";
      push({ target: "sheet.qa", agent: null, action: "appear", text: passed ? "✓ QA" : "✗ QA" });
      break;
    }
    case "call.booked": {
      const c = p as CallBooked;
      push({
        target: "calendar.block",
        agent,
        action: "drag",
        text: `${caseName()} · ${c.minutes}-min review`,
        meta: { start: c.start, minutes: c.minutes, invitee: c.invitee_masked },
      });
      break;
    }
    case "post.published": {
      const s = p as PostPublished;
      if (s.image_url) push({ target: "social.image", agent, action: "drag", meta: { image_url: s.image_url } });
      push({ target: "social.caption", agent, action: "type", text: s.text, meta: { channel: s.channel } });
      push({ target: "social.post", agent, action: "click" });
      push({ target: "social.posted", agent: null, action: "appear", text: `Posted to ${s.channel}`, meta: { url: s.url } });
      break;
    }
    case "dm.drafted": {
      const d = p as DmPayload;
      push({ target: "social.dm", agent, action: "type", text: d.text, meta: { recipient: d.recipient_label } });
      break;
    }
    case "dm.sent_by_human": {
      const d = p as DmPayload;
      push({ target: "social.dm_send", agent, action: "click", meta: { recipient: d.recipient_label } });
      break;
    }
    case "screen.call": {
      const s = p as ScreenCall;
      push({ target: "phone.dial", agent, action: "dial", text: s.candidate_id, meta: { seconds: s.seconds } });
      push({ target: "phone.transcript", agent, action: "type", text: s.transcript_excerpt });
      break;
    }
    case "phone.checked": {
      const c = p as PhoneChecked;
      push({
        target: "phone.check",
        agent,
        action: c.rendered_ok ? "tick" : "circle",
        text: c.rendered_ok ? "Memo renders on phone" : "Memo failed to render on phone",
        meta: { screenshot_url: c.screenshot_url },
      });
      break;
    }
    case "case.status": {
      const s = p as CaseStatus;
      push({ target: "rail.cases", agent: null, action: "status", text: prettyStatus(s.to), meta: { from: s.from, to: s.to } });
      break;
    }
    case "case.blocked": {
      const b = p as CaseBlocked;
      push({ target: "rail.cases", agent: null, action: "block", text: b.reason, meta: { needs: b.needs } });
      break;
    }
    case "case.not_eligible": {
      const n = p as CaseNotEligible;
      push({ target: "rail.cases", agent: null, action: "status", text: "not eligible", meta: { reason: n.reason } });
      break;
    }
    case "crm.sync_requested":
    case "crm.synced": {
      const c = p as CrmSync;
      push({ target: "rail.crm", agent: null, action: "appear", text: `CRM ${c.object} · ${prettyStatus(c.stage)}` });
      break;
    }
    case "hr.applicant":
    case "hr.screened":
    case "hr.shortlisted":
    case "hr.hired":
    case "hr.declined": {
      const h = p as HrPayload;
      const stage = ev.type.slice(3);
      push({ target: "rail.hiring", agent, action: "count", text: `${h.name_masked} · ${stage}`, meta: { stage, note: h.note } });
      break;
    }
    case "voice.command": {
      const v = p as VoiceCommand;
      push({
        target: "rail.voice",
        agent,
        action: "say",
        text: v.text,
        meta: { channel: v.channel, speaker: v.speaker ?? (ev.actor === "ceo" ? "ceo" : "customer"), case_id: v.case_id ?? null },
      });
      break;
    }
    case "ceo.notified": {
      const n = p as CeoNotified;
      push({ target: "rail.notify", agent, action: "appear", text: n.subject, meta: { channel: n.channel, reason: n.reason, summary: n.summary } });
      break;
    }
    case "approval.requested": {
      const a = p as ApprovalRequested;
      push({ target: "rail.touches", agent: null, action: "appear", text: a.summary, meta: { approval_id: a.approval_id, action_type: a.action_type } });
      break;
    }
    case "touch.training":
    case "touch.operating":
    case "touch.engineering": {
      const t = p as TouchPayload;
      const kind = ev.type.slice(6);
      push({ target: "rail.touches", agent, action: "count", text: t.note, meta: { kind, approval_id: t.approval_id } });
      break;
    }
    case "trust.changed": {
      const t = p as TrustChanged;
      push({ target: "rail.trust", agent: null, action: "status", text: `${t.action_type} → ${t.to}`, meta: { action_type: t.action_type, from: t.from, to: t.to, reason: t.reason } });
      break;
    }
    case "cost.booked": {
      const c = p as CostBooked;
      push({
        target: "rail.money",
        agent: null,
        action: "count",
        text: ev.cost_usd ? formatUsd(ev.cost_usd) : undefined,
        meta: { provider: c.provider, model: c.model, estimated: c.estimated, cost_usd: ev.cost_usd },
      });
      break;
    }
    case "tool.bought": {
      const t = p as ToolBought;
      push({
        target: "rail.money",
        agent,
        action: "count",
        text: `${t.tool}${ev.cost_usd ? ` · ${formatUsd(ev.cost_usd)}` : ""}`,
        meta: { provider: t.provider, cost_usd: ev.cost_usd },
      });
      break;
    }
    case "brief.generated": {
      const b = p as BriefGenerated;
      push({ target: "rail.brief", agent, action: "say", text: b.text, meta: { brief_event_id: ev.id } });
      break;
    }
  }
  return { state: next, steps: out };
}

/**
 * Build the full cursor script for an event list. Events are ordered by id and
 * de-duplicated (an SSE reconnect can replay an id we already have).
 */
export function buildScript(events: readonly UTaxEvent[], opts: ScriptOptions = {}): Script {
  const log = opts.log ?? ((s: SkippedEvent) => console.warn(`[theater] skipped event ${s.eventId} (${s.type}): ${s.reason}`));
  const ordered = [...events].sort((a, b) => a.id - b.id);
  let state = initialState();
  const steps: CursorStep[] = [];
  const skipped: SkippedEvent[] = [];
  let lastId = -Infinity;
  for (const ev of ordered) {
    if (ev.id === lastId) continue;
    lastId = ev.id;
    let res: ReturnType<typeof stepsForEvent>;
    try {
      res = stepsForEvent(state, ev);
    } catch {
      res = { state, steps: [], skipped: { eventId: ev.id, type: ev.type, reason: "bad_payload" } };
    }
    state = res.state;
    steps.push(...res.steps);
    if (res.skipped) {
      skipped.push(res.skipped);
      log(res.skipped);
    }
  }
  return { steps, skipped };
}
