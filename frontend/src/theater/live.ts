// Live and replay sources (contract §4). Pure helpers, so the player stays thin
// and this logic is tested without a browser or a gateway.

import type { UTaxEvent } from "./events";
import type { FirmNumbers } from "./rail";
import { AGENT_COUNT } from "./rail";

export type Source = "live" | "replay" | "fixture";

/** `?source=live|replay|fixture`; default is live when a gateway URL is configured, else the fixture. */
export function parseSource(search: string, hasGateway: boolean): Source {
  const v = new URLSearchParams(search).get("source");
  if (v === "live" || v === "replay" || v === "fixture") return v;
  return hasGateway ? "live" : "fixture";
}

export interface Endpoints {
  base: string;
  token: string;
}

const withToken = (url: URL, token: string) => {
  if (token) url.searchParams.set("token", token);
  return url.toString();
};

export const stateUrl = (e: Endpoints) => withToken(new URL("/api/state", e.base), e.token);

export function eventsUrl(e: Endpoints, afterId: number, limit = 500): string {
  const u = new URL("/api/events", e.base);
  u.searchParams.set("after_id", String(afterId));
  u.searchParams.set("limit", String(limit));
  return withToken(u, e.token);
}

export function streamUrl(e: Endpoints, afterId: number): string {
  const u = new URL("/api/events/stream", e.base);
  u.searchParams.set("after_id", String(afterId));
  return withToken(u, e.token);
}

export function briefAudioUrl(e: Endpoints, eventId: number): string {
  const u = new URL("/api/brief/audio", e.base);
  u.searchParams.set("event_id", String(eventId));
  return withToken(u, e.token);
}

type FetchJson = (url: string) => Promise<unknown>;

interface EventsPage {
  data: UTaxEvent[];
  next_after_id: number;
  has_more: boolean;
}

function isPage(v: unknown): v is EventsPage {
  if (typeof v !== "object" || v === null) return false;
  const p = v as Record<string, unknown>;
  return Array.isArray(p.data) && typeof p.has_more === "boolean" && Number.isSafeInteger(p.next_after_id);
}

/** Page through GET /api/events (replay and catch-up). Stops on a malformed page or a page that doesn't advance. */
export async function fetchAllEvents(e: Endpoints, getJson: FetchJson, afterId = 0, maxPages = 200): Promise<UTaxEvent[]> {
  const out: UTaxEvent[] = [];
  let after = afterId;
  for (let i = 0; i < maxPages; i++) {
    const page = await getJson(eventsUrl(e, after));
    if (!isPage(page)) throw new Error("malformed /api/events page");
    out.push(...page.data);
    if (!page.has_more || page.next_after_id <= after) break;
    after = page.next_after_id;
  }
  return out;
}

const int = (v: unknown, fallback = 0) => (typeof v === "number" && Number.isSafeInteger(v) && v >= 0 ? v : fallback);

/** GET /api/state → rail numbers. Missing or malformed fields read as zero, never as a crash. */
export function numbersFromState(raw: unknown): FirmNumbers | null {
  if (typeof raw !== "object" || raw === null) return null;
  const s = raw as Record<string, Record<string, unknown> | undefined>;
  const firm = s.firm ?? {};
  const touches = s.touches ?? {};
  const money = s.money ?? {};
  const hiring = s.hiring ?? {};
  const queue = s.queue ?? {};
  const shortlisted = int(hiring.shortlisted);
  const hired = int(hiring.hired);
  const declined = int(hiring.declined);
  return {
    firm: { agents: int(firm.agents, AGENT_COUNT) || AGENT_COUNT, clients: int(firm.clients) },
    touches: { training: int(touches.training), operating: int(touches.operating), engineering: int(touches.engineering) },
    money: {
      tax_saved_cents: int(money.tax_saved_cents),
      spent_usd: typeof money.spent_usd === "string" && /^\d+(\.\d+)?$/.test(money.spent_usd) ? money.spent_usd : "0",
    },
    // Theater token: counts only. "Shortlist ready" while shortlisted candidates still await a decision.
    hiring: { applicants: int(hiring.applicants), screened: int(hiring.screened), shortlisted, hired, declined, awaiting: Math.max(0, shortlisted - hired - declined) },
    queue: { waiting: int(queue.waiting), blocked: int(queue.blocked) },
  };
}

/** GET /api/state → script seed: masked names for known cases, and where the stream should resume. */
export function seedFromState(raw: unknown): { caseNames: Record<string, string>; lastId: number } {
  const caseNames: Record<string, string> = Object.create(null);
  if (typeof raw !== "object" || raw === null) return { caseNames, lastId: 0 };
  const s = raw as { last_event_id?: unknown; cases?: unknown };
  if (Array.isArray(s.cases)) {
    for (const c of s.cases) {
      if (typeof c === "object" && c !== null) {
        const { case_id, masked_name } = c as Record<string, unknown>;
        if (typeof case_id === "string" && typeof masked_name === "string") caseNames[case_id] = masked_name;
      }
    }
  }
  return { caseNames, lastId: int(s.last_event_id) };
}

/**
 * Live mode stays "seconds behind reality": when steps pile up, play faster.
 * The operator's own speed choice is a floor.
 */
export function backlogSpeed(queued: number): number {
  if (queued > 120) return 16;
  if (queued > 30) return 4;
  return 1;
}

/** Reconnect delays for a dropped stream: 1 s, 2 s, 4 s, then every 8 s. */
export const reconnectDelayMs = (attempt: number) => Math.min(8000, 1000 * 2 ** Math.max(0, attempt));

/** Image hosts the projector may load from (NEXT_PUBLIC_ASSET_HOSTS, comma-separated; contract v1.6.4). */
export function parseAssetHosts(raw: string | undefined): string[] {
  return (raw ?? "")
    .split(",")
    .map((h) => h.trim().toLowerCase())
    .filter(Boolean);
}

export function assetAllowed(url: string | undefined, hosts: readonly string[]): boolean {
  if (!url) return false;
  try {
    const u = new URL(url);
    return u.protocol === "https:" && hosts.includes(u.hostname.toLowerCase());
  } catch {
    return false;
  }
}
