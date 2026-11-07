// The CEO console (/ceo, contract §5 and §8): pure logic, tested without a browser or a
// gateway. The admin token lives only in this tab and travels only as a Bearer header.

import type { UTaxEvent } from "@/theater/events";

export const ACTION_TYPES = ["email", "social_post", "calendar_invite", "voice_screen"] as const;
export type ActionType = (typeof ACTION_TYPES)[number];
export type TrustState = "ASK" | "AUTO" | "PAUSED";

export const ACTION_LABEL: Record<ActionType, string> = {
  email: "Emails",
  social_post: "Social posts",
  calendar_invite: "Calendar invites",
  voice_screen: "Candidate phone screens",
};

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null;
const str = (v: unknown) => (typeof v === "string" ? v : "");
const int = (v: unknown) => (typeof v === "number" && Number.isSafeInteger(v) && v >= 0 ? v : null);

// ------------------------------------------------------------------ the login brief

export interface Approval {
  approvalId: string;
  actionType: string;
  summary: string;
  caseId: string | null;
}

export interface Brief {
  briefEventId: number | null;
  text: string;
  pending: Approval[];
  lastEventId: number | null;
}

/** GET /api/ceo/brief → {brief_event_id, text, counts, pending_approvals, last_event_id}. */
export function parseBrief(raw: unknown): Brief | null {
  if (!isObj(raw) || typeof raw.text !== "string") return null;
  const pending = Array.isArray(raw.pending_approvals)
    ? raw.pending_approvals.filter(isObj).flatMap((a) =>
        str(a.approval_id) ? [{ approvalId: str(a.approval_id), actionType: str(a.action_type), summary: str(a.summary), caseId: null }] : [],
      )
    : [];
  return { briefEventId: int(raw.brief_event_id), text: raw.text, pending, lastEventId: int(raw.last_event_id) };
}

/** Where the last login brief left off, kept on this device so the next brief covers only what's new. */
export const LAST_SEEN_KEY = "utax.ceo.lastSeen";
export const TOKEN_KEY = "utax.ceo.token";

export function readLastSeen(raw: string | null): number {
  const n = raw !== null && /^\d{1,15}$/.test(raw) ? Number(raw) : 0;
  return Number.isSafeInteger(n) ? n : 0;
}

// ------------------------------------------------------------------ state (admin view)

export interface CaseRow {
  caseId: string;
  name: string;
  status: string;
  flagged: boolean;
}
export interface Candidate {
  candidateId: string;
  name: string;
  stage: string;
  credentials: string | null;
}
export interface Console {
  lastEventId: number | null;
  cases: CaseRow[];
  trust: Partial<Record<ActionType, TrustState>>;
  candidates: Candidate[];
}

/** GET /api/state with the admin bearer: cases, trust and the per-candidate list (admin-only). */
export function consoleFromState(raw: unknown): Console | null {
  if (!isObj(raw)) return null;
  const cases = Array.isArray(raw.cases)
    ? raw.cases.filter(isObj).flatMap((c) =>
        str(c.case_id) ? [{ caseId: str(c.case_id), name: str(c.masked_name) || str(c.case_id), status: str(c.status), flagged: c.flagged === true }] : [],
      )
    : [];
  const trust: Console["trust"] = {};
  if (isObj(raw.trust)) {
    for (const t of ACTION_TYPES) {
      const v = raw.trust[t];
      if (v === "ASK" || v === "AUTO" || v === "PAUSED") trust[t] = v;
    }
  }
  const hiring = isObj(raw.hiring) ? raw.hiring : {};
  const candidates = Array.isArray(hiring.candidates)
    ? hiring.candidates.filter(isObj).flatMap((c) =>
        str(c.candidate_id)
          ? [{ candidateId: str(c.candidate_id), name: str(c.name_masked) || "Candidate", stage: str(c.stage), credentials: str(c.credentials) || null }]
          : [],
      )
    : [];
  return { lastEventId: int(raw.last_event_id), cases, trust, candidates };
}

/** Hire/Decline for every screened candidate (contract v1.5), not only shortlisted ones. */
export const canDecide = (stage: string) => stage === "screened" || stage === "shortlisted";

export const STAGE_LABEL: Record<string, string> = {
  applicant: "applied · questionnaire out",
  screened: "screened",
  shortlisted: "shortlisted",
  hired: "hired",
  declined: "declined",
};

// ------------------------------------------------------------------ the log (admin view)

export interface LogView {
  /** approval_id → approval still waiting on the CEO. */
  pending: Map<string, Approval>;
  /** case_id → why it is blocked (latest case.blocked). */
  blockedReason: Map<string, string>;
  lastId: number;
}

export const emptyLog = (): LogView => ({ pending: new Map(), blockedReason: new Map(), lastId: 0 });

/** Seed the pending list from the brief (authoritative at login). */
export function seedPending(v: LogView, pending: readonly Approval[]): LogView {
  const next = new Map(v.pending);
  for (const a of pending) if (!next.has(a.approvalId)) next.set(a.approvalId, a);
  return { ...v, pending: next };
}

/** Fold admin events: an approval is pending until a touch carries its approval_id. */
export function foldLog(v: LogView, events: readonly UTaxEvent[]): LogView {
  let pending = v.pending;
  let blocked = v.blockedReason;
  let lastId = v.lastId;
  for (const e of events) {
    if (!isObj(e) || !(typeof e.id === "number" && e.id > lastId)) continue;
    lastId = e.id;
    if (e.status !== "ok") continue;
    const p = isObj(e.payload) ? e.payload : {};
    if (e.type === "approval.requested" && str(p.approval_id)) {
      pending = new Map(pending).set(str(p.approval_id), {
        approvalId: str(p.approval_id),
        actionType: str(p.action_type),
        summary: str(p.summary),
        caseId: e.case_id,
      });
    } else if (e.type.startsWith("touch.") && str(p.approval_id) && pending.has(str(p.approval_id))) {
      pending = new Map(pending);
      pending.delete(str(p.approval_id));
    } else if (e.type === "trust.changed" && p.to === "AUTO") {
      // The type now runs on its own: the gateway marks the others of that type superseded (no event of their own).
      const t = str(p.action_type);
      if ([...pending.values()].some((a) => a.actionType === t)) pending = new Map([...pending].filter(([, a]) => a.actionType !== t));
    } else if (e.type === "case.blocked" && e.case_id) {
      blocked = new Map(blocked).set(e.case_id, str(p.reason) || "needs a human");
    }
  }
  return { pending, blockedReason: blocked, lastId };
}

// ------------------------------------------------------------------ actions

export type UnblockTo = "READY" | "INTAKE_PENDING" | "NOT_ELIGIBLE";
export const UNBLOCK: readonly { to: UnblockTo; label: string; hint: string }[] = [
  { to: "READY", label: "Retry", hint: "Run the case again with the facts it has" },
  { to: "INTAKE_PENDING", label: "Re-ask", hint: "Ask the client for the facts again" },
  { to: "NOT_ELIGIBLE", label: "Not eligible", hint: "Close it with the not-eligible note" },
];

/** The CEO's typed order: one line to the Chief of Staff. */
export const MAX_ORDER_CHARS = 500;
export const orderBody = (text: string) => ({ text: text.trim().slice(0, MAX_ORDER_CHARS), channel: "typed" as const, speaker: "ceo" as const });

/** Gateway error → a sentence for the CEO. */
export function ceoError(status: number, code: string, message: string): string {
  if (status === 401 || code === "unauthorized") return "The admin token was rejected. Sign in again.";
  if (status === 0 || code === "network_error") return "Can't reach the gateway. Check it's running and try again.";
  if (code === "wrong_status") return "That case already moved on; the list is refreshing.";
  if (code === "no_facts") return "Retry needs the client's facts first. Use Re-ask instead.";
  if (code === "not_found") return "That item is gone; the list is refreshing.";
  if (code === "rate_limited" || status === 429) return "Too many requests. Wait a moment and try again.";
  return message || "The gateway refused that.";
}
