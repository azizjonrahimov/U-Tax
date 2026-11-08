// D39: what the landing shows, from the firm's own public reads only (LANDING-BRIEF §0 rule 2,
// §4 S2): GET /api/health, GET /api/state and GET /api/events?types=… with the public theater
// token. Pure parsers (tested) plus one hook. Offline: every live value is null and shows "—".

import { useEffect, useState } from "react";
import { API_URL } from "@/lib/api";
import { eventsUrl, stateUrl, type Endpoints } from "@/theater/live";
import { formatCents } from "@/theater/format";
import { LANDING_TYPES, logStats, type LogStats } from "./stats";

const PUBLIC: Endpoints = { base: API_URL, token: process.env.NEXT_PUBLIC_THEATER_TOKEN ?? "" };
const HAS_GATEWAY = Boolean(process.env.NEXT_PUBLIC_API_URL);
const POLL_MS = 20_000;

export interface HeroCase {
  name: string;
  /** "$85,165 → $450": the authorized-shares bill and what the case owes (the case's own result). */
  math: string | null;
  done: boolean;
}
export interface RunRow {
  key: string;
  time: string;
  text: string;
  /** Right column: "41 s" for a memo, the tokens for a contractor payment. */
  aside: string;
}
export interface Firm {
  /** null while loading (or no gateway configured); false when the gateway is down. */
  open: boolean | null;
  agents: number | null;
  /** Cases the firm actually checked (a computed result; flagged cases never count). */
  checked: number | null;
  savedCents: number | null;
  latest: HeroCase | null;
  runs: RunRow[] | null;
  /** Operating touches (a human had to act), for "steps with no human" (S8). */
  operating: number | null;
  /** What the public log says about tonight (S3–S9); null offline. */
  log: LogStats | null;
}

export const OFFLINE: Firm = { open: null, agents: null, checked: null, savedCents: null, latest: null, runs: null, operating: null, log: null };

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => typeof v === "object" && v !== null && !Array.isArray(v);
const count = (v: unknown) => (typeof v === "number" && Number.isSafeInteger(v) && v >= 0 ? v : null);

interface StateCase {
  id: string;
  name: string;
  status: string;
  authorized: number | null;
  total: number | null;
}

/** /api/state → the numbers the landing shows and the cases (masked names, their own results). */
export function parseState(raw: unknown): { agents: number | null; savedCents: number | null; operating: number | null; lastId: number | null; cases: StateCase[] } {
  if (!isObj(raw)) return { agents: null, savedCents: null, operating: null, lastId: null, cases: [] };
  const firm = isObj(raw.firm) ? raw.firm : {};
  const money = isObj(raw.money) ? raw.money : {};
  const touches = isObj(raw.touches) ? raw.touches : {};
  const cases: StateCase[] = [];
  if (Array.isArray(raw.cases)) {
    for (const c of raw.cases) {
      if (!isObj(c) || typeof c.case_id !== "string" || typeof c.masked_name !== "string" || c.flagged === true) continue;
      const r = isObj(c.result) ? c.result : null;
      cases.push({
        id: c.case_id,
        name: c.masked_name.slice(0, 40),
        status: typeof c.status === "string" ? c.status : "",
        authorized: r ? count(r.authorized_method_cents) : null,
        total: r ? count(r.total_cents) : null,
      });
    }
  }
  return { agents: count(firm.agents), savedCents: count(money.tax_saved_cents), operating: count(touches.operating), lastId: count(raw.last_event_id), cases };
}

/** Statuses with the memo sent (a case is done); a case waiting on a human or not eligible never heads the card. */
const DONE = new Set(["SENT", "CALL_BOOKED"]);
const NOT_SHOWN = new Set(["BLOCKED", "NOT_ELIGIBLE", "QA_FAILED"]);

const mathOf = (c: StateCase | undefined) => (c && c.authorized !== null && c.total !== null ? `${formatCents(c.authorized)} → ${formatCents(c.total)}` : null);
const clock = (ts: unknown) => {
  if (typeof ts !== "string") return "";
  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleTimeString("en-GB", { hour12: false });
};
const tokenAmount = (v: unknown) =>
  typeof v === "number" && Number.isFinite(v) && v >= 0 && v < 1e9 ? v.toFixed(2) : typeof v === "string" && /^\d{1,9}(\.\d{1,18})?$/.test(v) ? Number(v).toFixed(2) : null;

/** The hero card: the newest checked case, and the three newest memos / contractor payments. */
export function heroFrom(state: ReturnType<typeof parseState>, events: readonly unknown[]): { latest: HeroCase | null; runs: RunRow[]; checked: number } {
  const byId = new Map(state.cases.map((c) => [c.id, c]));
  const memoed = new Set<string>();
  const rows: { id: number; row: RunRow }[] = [];
  for (const e of events) {
    if (!isObj(e) || typeof e.id !== "number" || e.status !== "ok") continue;
    const p = isObj(e.payload) ? e.payload : {};
    if (e.type === "memo.sent" && typeof e.case_id === "string") {
      memoed.add(e.case_id);
      const c = byId.get(e.case_id);
      if (!c) continue; // a flagged or unknown case is never listed
      const secs = count(p.seconds_since_last_fact);
      rows.push({ id: e.id, row: { key: `m${e.id}`, time: clock(e.ts), text: `${c.name.toLowerCase()}${mathOf(c) ? ` · ${mathOf(c)}` : ""}`, aside: secs !== null ? `${secs} s` : "" } });
    } else if (e.type === "payment.sent") {
      const amt = tokenAmount(p.amount_usd);
      const who = typeof p.contractor === "string" ? p.contractor.slice(0, 30) : "a contractor";
      if (amt) rows.push({ id: e.id, row: { key: `p${e.id}`, time: clock(e.ts), text: `contractor job · paid ${who}`, aside: amt } });
    }
  }
  const withResult = state.cases.filter((c) => c.authorized !== null && c.total !== null);
  const shown = withResult.filter((c) => !NOT_SHOWN.has(c.status));
  const newest = shown.at(-1);
  return {
    latest: newest ? { name: newest.name, math: mathOf(newest), done: DONE.has(newest.status) || memoed.has(newest.id) } : null,
    runs: rows
      .sort((a, b) => b.id - a.id)
      .slice(0, 3)
      .map((r) => r.row),
    checked: withResult.length,
  };
}

async function getJson(url: string, signal: AbortSignal): Promise<unknown> {
  const r = await fetch(url, { cache: "no-store", signal });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

/** Only the rows past `after` (a few pages at most); the caller keeps what it already read. */
async function newEvents(after: number, signal: AbortSignal): Promise<{ events: unknown[]; next: number }> {
  const out: unknown[] = [];
  let cursor = after;
  for (let i = 0; i < 10; i++) {
    const u = new URL(eventsUrl(PUBLIC, cursor, 500));
    u.searchParams.set("types", LANDING_TYPES.join(","));
    const page = await getJson(u.toString(), signal);
    if (!isObj(page) || !Array.isArray(page.data)) break;
    out.push(...page.data);
    const next = page.next_after_id;
    if (typeof next === "number" && next > cursor) cursor = next;
    if (page.has_more !== true) break;
  }
  return { events: out, next: cursor };
}

/**
 * The firm, live: every 20 s while the tab is visible (a hidden tab costs the gateway nothing).
 * The log is read once, then only what's new (a reset log, seen as a smaller last id, starts over).
 */
export function useFirm(): Firm {
  const [firm, setFirm] = useState<Firm>(OFFLINE);
  useEffect(() => {
    if (!HAS_GATEWAY) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let ac: AbortController | null = null;
    let busy = false;
    let seen: unknown[] = [];
    let after = 0;
    const schedule = () => {
      if (timer) clearTimeout(timer);
      timer = alive && !document.hidden ? setTimeout(load, POLL_MS) : undefined;
    };
    const load = async () => {
      if (!alive || busy || document.hidden) return;
      busy = true;
      ac = new AbortController();
      const kill = setTimeout(() => ac?.abort(), 8000);
      try {
        const health = await fetch(`${API_URL.replace(/\/+$/, "")}/api/health`, { cache: "no-store", signal: ac.signal });
        if (!health.ok) throw new Error("down");
        const state = parseState(await getJson(stateUrl(PUBLIC), ac.signal));
        if (state.lastId !== null && state.lastId < after) {
          seen = []; // the gateway's log restarted: ids are reused
          after = 0;
        }
        let log: LogStats | null = null;
        try {
          const fresh = await newEvents(after, ac.signal);
          seen = seen.concat(fresh.events);
          after = fresh.next;
          log = logStats(seen, new Set(state.cases.map((c) => c.id)), new Map(state.cases.map((c) => [c.id, c.name])));
        } catch {
          // the stream failed: the card still shows from /api/state (its status says done or not)
        }
        const hero = heroFrom(state, seen);
        if (alive) setFirm({ open: true, agents: state.agents, checked: hero.checked, savedCents: state.savedCents, operating: state.operating, latest: hero.latest, runs: hero.runs, log });
      } catch {
        if (alive) setFirm({ ...OFFLINE, open: false });
      } finally {
        clearTimeout(kill);
        busy = false;
        schedule();
      }
    };
    const onVisibility = () => {
      if (document.hidden) {
        if (timer) clearTimeout(timer);
        timer = undefined;
      } else void load();
    };
    document.addEventListener("visibilitychange", onVisibility);
    void load();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
      ac?.abort();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, []);
  return firm;
}
