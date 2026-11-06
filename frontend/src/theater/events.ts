// Event envelope and payloads, per contracts/api.md v1.1 §2–3.
// Payloads arrive already masked; render them as given.

export type Actor =
  | "cos"
  | "intake"
  | "tax"
  | "qa"
  | "acct"
  | "mkt"
  | "hr"
  | "ceo"
  | "system";

/** Actors that own a cursor on screen (system events animate without one). */
export type Agent = Exclude<Actor, "system">;

export type EventStatus = "ok" | "failed" | "skipped";

export interface UTaxEvent<P = Record<string, unknown>> {
  id: number;
  ts: string;
  type: string;
  actor: Actor;
  case_id: string | null;
  provider: string | null;
  status: EventStatus;
  payload: P;
  tokens_in: number | null;
  tokens_out: number | null;
  /** Decimal string of USD, e.g. "0.0034". */
  cost_usd: string | null;
}

export type FactName =
  | "incorporated_delaware"
  | "authorized_shares"
  | "issued_shares"
  | "stated_par"
  | "gross_assets"
  | "foreign_owner_25"
  | "gross_receipts"
  | "years_with_receipts";

export interface EmailReceived {
  masked_name: string;
  from_masked: string;
  subject: string;
  body: string;
}

export interface FactsExtracted {
  facts: {
    incorporated_delaware?: boolean;
    authorized_shares?: number;
    issued_shares?: number;
    stated_par?: string;
    gross_assets_cents?: number;
    foreign_owner_25?: boolean;
    gross_receipts_cents?: number;
    years_with_receipts?: number;
  };
  quotes: Partial<Record<FactName, string>>;
  assumed: FactName[];
}

export interface FactsMissing {
  missing: FactName[];
  asks_so_far: number;
}

export interface ClientAsked {
  missing: FactName[];
  body: string;
}

export interface CaseStatus {
  from: string;
  to: string;
}

export interface CaseBlocked {
  reason: string;
  needs: "human";
}

export interface CaseNotEligible {
  reason: string;
}

export interface LawSearched {
  query: string;
  results: { cite: string; snippet: string }[];
}

export interface TaxComputed {
  authorized_method_cents: number;
  assumed_par_method_cents: number;
  chosen: "assumed_par" | "authorized";
  annual_report_fee_cents: number;
  fee_source: string;
  total_cents: number;
  due_date: string;
  form5472_flag: boolean;
  rnd_flag: boolean;
  engine_version: string;
}

export interface QaResult {
  checks: { field: string; ok: boolean; note: string }[];
}

export interface MemoSent {
  to_masked: string;
  subject: string;
  body: string;
  message_id: string;
  seconds_since_last_fact: number;
}

export interface CallBooked {
  start: string;
  minutes: number;
  invitee_masked: string;
}

export interface PhoneChecked {
  screenshot_url: string;
  rendered_ok: boolean;
}

export interface CrmSync {
  object: "contact" | "deal";
  stage: string;
}

export interface PostPublished {
  channel: string;
  text: string;
  url: string;
  image_url: string | null;
}

export interface DmPayload {
  recipient_label: string;
  text: string;
}

export interface HrPayload {
  candidate_id: string;
  name_masked: string;
  note: string;
}

export interface ScreenCall {
  candidate_id: string;
  transcript_excerpt: string;
  seconds: number;
}

/** v1.2: actor "intake" + case_id for customer conversations (/talk), "ceo" for typed orders. */
export interface VoiceCommand {
  text: string;
  channel: "voice" | "typed";
  speaker?: "customer" | "ceo";
  case_id?: string | null;
}

export interface CeoNotified {
  channel: "email" | "login";
  subject: string;
  summary: string;
  reason: "approval" | "blocked" | "digest" | "login";
}

export interface ApprovalRequested {
  approval_id: string;
  action_type: string;
  summary: string;
}

export interface TouchPayload {
  note: string;
  approval_id: string | null;
}

export interface TrustChanged {
  action_type: string;
  from: "ASK" | "AUTO" | "PAUSED";
  to: "ASK" | "AUTO" | "PAUSED";
  reason: string;
}

export interface CostBooked {
  provider: string;
  model: string;
  estimated: boolean;
}

export interface ToolBought {
  tool: string;
  provider: string;
}

export interface BriefGenerated {
  text: string;
  counts: Record<string, number>;
}

/** Parse a JSONL string (one event per line) into events, ordered by id. */
export function parseJsonl(text: string): UTaxEvent[] {
  return text
    .split(/\r?\n/)
    .filter((line) => line.trim() !== "")
    .map((line) => JSON.parse(line) as UTaxEvent)
    .sort((a, b) => a.id - b.id);
}
