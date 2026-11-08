// D39: what the landing shows, from the firm's own public reads only (LANDING-BRIEF §0 rule 2,
// §4 S2): GET /api/health, GET /api/state and GET /api/events?types=… with the public theater
// token. Pure parsers (tested) plus one hook. Offline: every live value is null and shows "—".

import { useEffect, useState } from "react";
import { API_URL } from "@/lib/api";
import { eventsUrl, stateUrl, type Endpoints } from "@/theater/live";
import { formatCents } from "@/theater/format";

const PUBLIC: Endpoints = { base: API_URL, token: process.env.NEXT_PUBLIC_THEATER_TOKEN ?? "" };
const HAS_GATEWAY = Boolean(process.env.NEXT_PUBLIC_API_URL);

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
  /** null until the gateway answers (and while it's down): the page shows "—" / the resting copy. */
  open: boolean | null;
  agents: number | null;
  clients: number | null;
  savedCents: number | null;
  latest: HeroCase | null;
  runs: RunRow[] | null;
}

export const OFFLINE: Firm = { open: null, agents: null, clients: null, savedCents: null, latest: null, runs: null };

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => typeof v === "object" && v !== null && !Array.isArray(v);
const count = (v: unknown) => (typeof v === "number" && Number.isSafeInteger(v) && v >= 0 ? v : null);

interface StateCase {
  id: string;
  name: string;
  authorized: number | null;
  total: number | null;
}

/** /api/state → the numbers the landing shows and the cases (masked names, their own results). */
export function parseState(raw: unknown): { agents: number | null; clients: number | null; savedCents: number | null; cases: StateCase[] } {
  if (!isObj(raw)) return { agents: null, clients: null, savedCents: null, cases: [] };
  const firm = isObj(raw.firm) ? raw.firm : {};
  const money = isObj(raw.money) ? raw.money : {};
  const cases: StateCase[] = [];
  if (Array.isArray(raw.cases)) {
    for (const c of raw.cases) {
      if (!isObj(c) || typeof c.case_id !== "string" || typeof c.masked_name !== "string" || c.flagged === true) continue;
      const r = isObj(c.result) ? c.result : null;
      cases.push({ id: c.case_id, name: c.masked_name.slice(0, 40), authorized: r ? count(r.authorized_method_cents) : null, total: r ? count(r.total_cents) : null });
    }
  }
  return { agents: count(firm.agents), clients: count(firm.clients), savedCents: count(money.tax_saved_cents), cases };
}

const mathOf = (c: StateCase | undefined) => (c && c.authorized !== null && c.total !== null ? `${formatCents(c.authorized)} → ${formatCents(c.total)}` : null);
const clock = (ts: unknown) => {
  if (typeof ts !== "string") return "";
  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleTimeString("en-GB", { hour12: false });
};

/** The hero card: the newest case with a result, and the three newest memos / contractor payments. */
export function heroFrom(state: ReturnType<typeof parseState>, events: readonly unknown[]): { latest: HeroCase | null; runs: RunRow[] } {
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
      const amt = typeof p.amount_usd === "number" ? p.amount_usd.toFixed(2) : typeof p.amount_usd === "string" && /^\d{1,9}(\.\d+)?$/.test(p.amount_usd) ? Number(p.amount_usd).toFixed(2) : null;
      const who = typeof p.contractor === "string" ? p.contractor.slice(0, 30) : "a contractor";
      if (amt) rows.push({ id: e.id, row: { key: `p${e.id}`, time: clock(e.ts), text: `share card · paid ${who}`, aside: amt } });
    }
  }
  const withResult = state.cases.filter((c) => c.authorized !== null && c.total !== null);
  const newest = withResult.at(-1);
  return {
    latest: newest ? { name: newest.name, math: mathOf(newest), done: memoed.has(newest.id) } : null,
    runs: rows
      .sort((a, b) => b.id - a.id)
      .slice(0, 3)
      .map((r) => r.row),
  };
}

async function getJson(url: string, signal: AbortSignal): Promise<unknown> {
  const r = await fetch(url, { cache: "no-store", signal });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

/** Newest-first rows need the whole (filtered) log: at most a few pages of memos and payments. */
async function publicEvents(signal: AbortSignal): Promise<unknown[]> {
  const out: unknown[] = [];
  let after = 0;
  for (let i = 0; i < 10; i++) {
    const u = new URL(eventsUrl(PUBLIC, after, 500));
    u.searchParams.set("types", "memo.sent,payment.sent");
    const page = await getJson(u.toString(), signal);
    if (!isObj(page) || !Array.isArray(page.data)) break;
    out.push(...page.data);
    const next = page.next_after_id;
    if (page.has_more !== true || typeof next !== "number" || next <= after) break;
    after = next;
  }
  return out;
}

/** The firm, live: refreshed every 20 s while the page is open. Any failure → offline ("—"). */
export function useFirm(): Firm {
  const [firm, setFirm] = useState<Firm>(OFFLINE);
  useEffect(() => {
    if (!HAS_GATEWAY) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = async () => {
      const ac = new AbortController();
      const kill = setTimeout(() => ac.abort(), 8000);
      try {
        const health = await fetch(`${API_URL.replace(/\/+$/, "")}/api/health`, { cache: "no-store", signal: ac.signal });
        if (!health.ok) throw new Error("down");
        const state = parseState(await getJson(stateUrl(PUBLIC), ac.signal));
        let hero: ReturnType<typeof heroFrom> = { latest: null, runs: [] };
        try {
          hero = heroFrom(state, await publicEvents(ac.signal));
        } catch {
          hero = heroFrom(state, []); // the stream failed: the case card still shows from /api/state
        }
        if (alive) setFirm({ open: true, agents: state.agents, clients: state.clients, savedCents: state.savedCents, ...hero });
      } catch {
        if (alive) setFirm({ ...OFFLINE, open: false });
      } finally {
        clearTimeout(kill);
        if (alive) timer = setTimeout(load, 20_000);
      }
    };
    void load();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
  }, []);
  return firm;
}
