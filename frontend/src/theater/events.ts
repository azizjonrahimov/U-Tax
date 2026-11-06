// Event envelope, per contracts/api.md §2–3. Payloads arrive already masked;
// render them as given. Payload shapes are checked where they are used
// (script.ts), so a malformed event is skipped instead of animated.

export const ACTORS = ["cos", "intake", "tax", "qa", "acct", "mkt", "hr", "ceo", "system"] as const;
export type Actor = (typeof ACTORS)[number];

/** Actors that own a cursor on screen (system events animate without one). */
export type Agent = Exclude<Actor, "system">;

export const EVENT_STATUSES = ["ok", "failed", "skipped"] as const;
export type EventStatus = (typeof EVENT_STATUSES)[number];

export interface UTaxEvent {
  id: number;
  ts: string;
  type: string;
  actor: Actor;
  case_id: string | null;
  provider: string | null;
  status: EventStatus;
  payload: Record<string, unknown>;
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

export interface BadLine {
  line: number;
  error: string;
}

/**
 * Parse JSONL (one event per line). A line that is not valid JSON or not an
 * object is reported in `bad` and skipped, so one truncated line never blanks
 * a whole replay. Envelope fields are validated later by the script.
 */
export function parseJsonl(text: string): { events: UTaxEvent[]; bad: BadLine[] } {
  const events: UTaxEvent[] = [];
  const bad: BadLine[] = [];
  text.replace(/^\uFEFF/, "").split(/\r?\n/).forEach((raw, i) => {
    if (raw.trim() === "") return;
    try {
      const value: unknown = JSON.parse(raw);
      if (typeof value !== "object" || value === null || Array.isArray(value)) {
        bad.push({ line: i + 1, error: "not an object" });
      } else {
        events.push(value as UTaxEvent);
      }
    } catch (e) {
      bad.push({ line: i + 1, error: e instanceof Error ? e.message : String(e) });
    }
  });
  return { events, bad };
}
