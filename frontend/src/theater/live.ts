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

/** Join like lib/api.ts does, so a path prefix in the base (a reverse proxy) is kept. */
const api = (e: Endpoints, path: string) => new URL(`${e.base.replace(/\/+$/, "")}/api${path}`);

export const stateUrl = (e: Endpoints) => withToken(api(e, "/state"), e.token);

export function eventsUrl(e: Endpoints, afterId: number, limit = 500): string {
  const u = api(e, "/events");
  u.searchParams.set("after_id", String(afterId));
  u.searchParams.set("limit", String(limit));
  return withToken(u, e.token);
}

export function streamUrl(e: Endpoints, afterId: number): string {
  const u = api(e, "/events/stream");
  u.searchParams.set("after_id", String(afterId));
  return withToken(u, e.token);
}

export function briefAudioUrl(e: Endpoints, eventId: number): string {
  const u = api(e, "/brief/audio");
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

/**
 * Page through GET /api/events (replay and catch-up). Stops on a malformed page or a
 * page that doesn't advance; `truncated` says the page cap was hit with more to come.
 */
export async function fetchAllEvents(
  e: Endpoints,
  getJson: FetchJson,
  afterId = 0,
  maxPages = 200,
): Promise<{ events: UTaxEvent[]; truncated: boolean }> {
  const events: UTaxEvent[] = [];
  let after = afterId;
  for (let i = 0; i < maxPages; i++) {
    const page = await getJson(eventsUrl(e, after));
    if (!isPage(page)) throw new Error("malformed /api/events page");
    for (const ev of page.data) events.push(ev); // no push(...spread): pages can be large
    if (!page.has_more || page.next_after_id <= after) return { events, truncated: false };
    after = page.next_after_id;
  }
  return { events, truncated: true };
}

/** An HTTP error with its status, so the caller can tell a bad token from an outage. */
export class HttpError extends Error {
  constructor(public readonly status: number) {
    super(`HTTP ${status}`);
  }
}

/** 401/403 means the theater token is wrong: retrying forever would just look like an outage. */
export const isAuthError = (e: unknown) => e instanceof HttpError && (e.status === 401 || e.status === 403);

const int = (v: unknown, fallback = 0) => (typeof v === "number" && Number.isSafeInteger(v) && v >= 0 ? v : fallback);

/** A testnet-token total from /api/state (number or decimal string); anything else is left out. */
function tokenField(k: "contractor_paid_usd" | "agent_sales_usd", v: unknown): Partial<Record<typeof k, string>> {
  if (typeof v === "number" && Number.isFinite(v) && v >= 0) return { [k]: String(v) };
  if (typeof v === "string" && /^\d+(\.\d+)?$/.test(v)) return { [k]: v };
  return {};
}

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
      // v1.19: the market's totals (top level per §9.1, or under money); absent = market off.
      ...tokenField("contractor_paid_usd", s.contractor_paid_usd ?? money.contractor_paid_usd),
      ...tokenField("agent_sales_usd", s.agent_sales_usd ?? money.agent_sales_usd),
    },
    // Theater token: counts only. "Shortlist ready" while shortlisted candidates still await a decision:
    // the gateway's own `awaiting` when it sends one; otherwise an estimate from the cumulative counts.
    hiring: {
      applicants: int(hiring.applicants),
      screened: int(hiring.screened),
      shortlisted,
      hired,
      declined,
      ...(typeof hiring.sourced === "number" ? { sourced: int(hiring.sourced) } : {}),
      ...(typeof hiring.contacted === "number" ? { contacted: int(hiring.contacted) } : {}),
      awaiting: typeof hiring.awaiting === "number" ? int(hiring.awaiting) : Math.max(0, shortlisted - hired - declined),
    },
    queue: { waiting: int(queue.waiting), blocked: int(queue.blocked) },
  };
}

export interface StateSeed {
  caseNames: Record<string, string>;
  /** Where the stream should resume; null when the gateway didn't say (never read as 0). */
  lastId: number | null;
  /** Case rows for the rail, so a reload shows cards that match the numbers. */
  cases: { caseId: string; name: string; status?: string; flagged?: boolean }[];
}

/** GET /api/state → script and screen seed. */
export function seedFromState(raw: unknown): StateSeed {
  const caseNames: Record<string, string> = Object.create(null);
  const cases: StateSeed["cases"] = [];
  if (typeof raw !== "object" || raw === null) return { caseNames, lastId: null, cases };
  const s = raw as { last_event_id?: unknown; cases?: unknown };
  if (Array.isArray(s.cases)) {
    for (const c of s.cases) {
      if (typeof c === "object" && c !== null) {
        const { case_id, masked_name, status, flagged } = c as Record<string, unknown>;
        if (typeof case_id === "string" && typeof masked_name === "string") {
          caseNames[case_id] = masked_name;
          cases.push({ caseId: case_id, name: masked_name, status: typeof status === "string" ? status : undefined, flagged: flagged === true || undefined });
        }
      }
    }
  }
  const id = s.last_event_id;
  return { caseNames, lastId: typeof id === "number" && Number.isSafeInteger(id) && id >= 0 ? id : null, cases };
}

/** How often live mode checks /api/state, independent of events (liveness + store resets). */
export const WATCHDOG_MS = 15_000;

export type LiveVerdict = "ok" | "resync" | "reconnect";

/**
 * The live watchdog's decision on each /api/state poll.
 * - The gateway's log went backwards (a reset or new data dir): resync from scratch.
 *   The stream can't tell us: after a reset it drops every id at or below our old one.
 * - The gateway is ahead of us and nothing arrived since the last poll, twice in a row:
 *   the connection is half-open (a ": ping" comment fires no event), so reconnect.
 */
export function liveVerdict(input: { stateLastId: number | null; cursorLastId: number; messagesSinceLastPoll: number; stalledPolls: number }): {
  verdict: LiveVerdict;
  stalledPolls: number;
} {
  const { stateLastId, cursorLastId, messagesSinceLastPoll } = input;
  if (stateLastId === null) return { verdict: "ok", stalledPolls: 0 };
  if (stateLastId < cursorLastId) return { verdict: "resync", stalledPolls: 0 };
  const stalled = stateLastId > cursorLastId && messagesSinceLastPoll === 0 ? input.stalledPolls + 1 : 0;
  return stalled >= 2 ? { verdict: "reconnect", stalledPolls: 0 } : { verdict: "ok", stalledPolls: stalled };
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

/** An origin ("https://gw.example:8443"), or null for anything that isn't an http(s) URL. */
export function originOf(raw: string | undefined): string | null {
  try {
    const u = new URL(raw ?? "");
    return u.protocol === "https:" || u.protocol === "http:" ? u.origin : null;
  } catch {
    return null;
  }
}

/**
 * Whether the projector may load this image: https on a listed host, or anything on the gateway's
 * own origin (D31: it serves the ads it renders; the theater already streams from it). A local
 * rehearsal gateway on http works through the origin rule; no other http host or port does.
 */
export function assetAllowed(url: string | undefined, hosts: readonly string[], gateway: string | null = null): boolean {
  if (!url) return false;
  try {
    const u = new URL(url);
    if (u.username || u.password) return false;
    if (gateway !== null && u.origin === gateway) return true;
    return u.protocol === "https:" && hosts.includes(u.hostname.toLowerCase());
  } catch {
    return false;
  }
}
