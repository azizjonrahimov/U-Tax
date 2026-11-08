// D42 P2: live data on the glass. Every caption comes from the firm's public reads (GET /api/state
// and GET /api/events with the public theater token, GET /api/payments); without a real value a
// card keeps its neutral caption (cards.ts). Pure folds (tested) plus one hook, refreshed every 30 s.

import { useEffect, useState } from "react";
import { API_URL } from "@/lib/api";
import { eventsUrl, stateUrl, type Endpoints } from "@/theater/live";
import { BASE_CARDS, CARD_KEYS, type CardKey } from "./cards";
import type { CardFace } from "./scene";

const PUBLIC: Endpoints = { base: API_URL, token: process.env.NEXT_PUBLIC_THEATER_TOKEN ?? "" };
const HAS_GATEWAY = Boolean(process.env.NEXT_PUBLIC_API_URL);
const POLL_MS = 30_000;

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => typeof v === "object" && v !== null && !Array.isArray(v);
const count = (v: unknown) => (typeof v === "number" && Number.isSafeInteger(v) && v >= 0 ? v : null);
const text = (v: unknown, max: number) => (typeof v === "string" ? v.replace(/\s+/g, " ").trim().slice(0, max) : "");
const num = (v: unknown) => {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string" && /^-?\d{1,12}(\.\d{1,18})?$/.test(v.trim())) return Number(v);
  return null;
};

export const SEAT_COLOR: Record<string, string> = { cos: "#FF4D3D", intake: "#4DA3FF", tax: "#FFB547", qa: "#3DDC84", acct: "#C77DFF", mkt: "#FF6FB5", hr: "#2EE6D6" };
const DEPT: Record<string, string> = { cos: "Chief of Staff", intake: "Intake", tax: "Tax", qa: "QA", acct: "Accounting", mkt: "Marketing", hr: "HR", system: "The firm" };
const LIME = "#BEF243";

/** The public event types the glass reads (the gateway filters before its limit). */
export const GLASS_TYPES = [
  "tax.return_computed",
  "form.step",
  "sheet.cell",
  "memo.sent",
  "client.asked",
  "client.followed_up",
  "email.received",
  "facts.extracted",
  "tax.computed",
  "qa.passed",
  "payment.sent",
  "post.published",
];
/** Outbound client mail (the Inbox card's count). */
const CLIENT_MAIL = new Set(["memo.sent", "client.asked", "client.followed_up"]);

export interface Tick {
  id: number;
  text: string;
  color: string;
}
export interface GlassLog {
  after: number;
  mails: number;
  /** Line 31 (total tax) of the newest federal return, in dollars. */
  federalUsd: number | null;
  /** Cells in the newest workbook run (sheet.cell `of`). */
  cells: number | null;
  newest: Tick | null;
}
export const EMPTY_LOG: GlassLog = { after: 0, mails: 0, federalUsd: null, cells: null, newest: null };

const usd = (v: number) => `$${v.toLocaleString("en-US", { maximumFractionDigits: v % 1 ? 2 : 0, minimumFractionDigits: v % 1 ? 2 : 0 })}`;
const clock = (ts: unknown) => {
  if (typeof ts !== "string") return "";
  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", hour12: false });
};

/** One line for the ticker, from a public event (payloads are already masked by the gateway). */
export function tickText(type: string, actor: string, p: Obj): string | null {
  const who = DEPT[actor] ?? "The firm";
  switch (type) {
    case "form.step": {
      const doc = text(p.doc, 30);
      const label = text(p.label, 50);
      return doc || label ? `${who} filled ${[doc, label].filter(Boolean).join(" ")}` : null;
    }
    case "sheet.cell": {
      const label = text(p.label, 40) || text(p.cell, 12);
      return label ? `${who} wrote ${label} in the ledger` : `${who} updated the ledger`;
    }
    case "tax.return_computed": {
      const form = text(p.form, 20);
      const j = text(p.jurisdiction, 20);
      return `${who} computed ${form ? `Form ${form.replace(/^form\s*/i, "")}` : "a return"}${j ? ` · ${j}` : ""} · draft`;
    }
    case "memo.sent":
      return `${who} sent a client memo`;
    case "client.asked":
      return `${who} asked a client for a fact`;
    case "client.followed_up":
      return `${who} followed up with a client`;
    case "email.received":
      return `${who} received a client email`;
    case "facts.extracted":
      return `${who} read the facts from an email`;
    case "tax.computed":
      return `${who} computed a franchise tax`;
    case "qa.passed":
      return `${who} passed a check`;
    case "payment.sent": {
      const amt = num(p.amount_usd);
      const token = text(p.token, 16);
      return amt !== null && amt >= 0 && token ? `${who} paid a contractor ${amt.toFixed(2)} ${token}` : `${who} paid a contractor`;
    }
    case "post.published":
      return `${who} published a post`;
    default:
      return null;
  }
}

const isFederal = (j: unknown) => typeof j === "string" && /^(federal|us|usa|irs|fed)$/i.test(j.trim());

/** Fold new public events (oldest first) into what the glass shows. */
export function foldEvents(log: GlassLog, events: readonly unknown[]): GlassLog {
  const out: GlassLog = { ...log };
  for (const e of events) {
    if (!isObj(e) || typeof e.id !== "number" || typeof e.type !== "string") continue;
    if (e.id > out.after) out.after = e.id;
    if (e.status !== "ok") continue;
    const p = isObj(e.payload) ? e.payload : {};
    const actor = typeof e.actor === "string" ? e.actor : "";
    if (CLIENT_MAIL.has(e.type)) out.mails += 1;
    if (e.type === "tax.return_computed" && isFederal(p.jurisdiction) && p.status !== "human_review") {
      const amt = num(p.amount_usd);
      if (amt !== null && amt >= 0 && amt < 1e12) out.federalUsd = amt;
    }
    if (e.type === "sheet.cell") {
      const of = count(p.of);
      if (of !== null && of > 0) out.cells = of;
    }
    const line = tickText(e.type, actor, p);
    if (line && (!out.newest || e.id > out.newest.id)) {
      const at = clock(e.ts);
      out.newest = { id: e.id, text: at ? `${line} · ${at}` : line, color: SEAT_COLOR[actor] ?? LIME };
    }
  }
  return out;
}

export interface StateNumbers {
  clients: number | null;
  touches: number | null;
  lastId: number | null;
}
export function stateNumbers(raw: unknown): StateNumbers {
  if (!isObj(raw)) return { clients: null, touches: null, lastId: null };
  const firm = isObj(raw.firm) ? raw.firm : {};
  const t = isObj(raw.touches) ? raw.touches : null;
  const parts = t ? [count(t.training), count(t.operating), count(t.engineering)] : [];
  const touches = parts.length && parts.every((x) => x !== null) ? (parts as number[]).reduce((a, b) => a + b, 0) : null;
  return { clients: count(firm.clients), touches, lastId: count(raw.last_event_id) };
}

/** The newest contractor payment from /api/payments ({data: rows newest first}): "0.50 pathUSD · tempo testnet". */
export function paidCaption(raw: unknown): string | null {
  const rows = isObj(raw) && Array.isArray(raw.data) ? raw.data : Array.isArray(raw) ? raw : isObj(raw) && Array.isArray(raw.payments) ? raw.payments : [];
  for (const r of rows) {
    if (!isObj(r) || r.direction !== "out" || r.status !== "ok") continue;
    const amt = num(r.amount);
    const token = text(r.token, 16);
    if (amt === null || amt < 0 || amt >= 1e9 || !token) continue;
    const rail = text(r.rail, 16).toLowerCase();
    return `${amt.toFixed(2)} ${token} · ${rail.startsWith("tempo") ? "tempo testnet" : rail.replace(/-/g, " ") || "testnet"}`;
  }
  return null;
}

/** The five faces: a live caption where the firm has the number, the neutral one otherwise. */
export function glassFaces(log: GlassLog, st: StateNumbers, paid: string | null): CardFace[] {
  const live: Partial<Record<CardKey, string>> = {};
  if (log.federalUsd !== null) live.form = `federal return · line 31 · ${usd(log.federalUsd)}`;
  if (log.cells !== null) live.ledger = `${log.cells} cells · formulas on screen`;
  if (log.mails > 0) live.inbox = `${log.mails} client ${log.mails === 1 ? "email" : "emails"} answered`;
  if (paid) live.paid = paid;
  if (st.clients !== null && st.touches !== null) live.agents = `${st.clients} ${st.clients === 1 ? "client" : "clients"} · ${st.touches} human ${st.touches === 1 ? "touch" : "touches"}`;
  return CARD_KEYS.map((k) => ({ ...BASE_CARDS[k], caption: live[k] ?? BASE_CARDS[k].caption }));
}

export interface Glass {
  faces: CardFace[] | null;
  ticker: Tick | null;
  /** false once the gateway failed to answer; null before the first read (or without a gateway). */
  open: boolean | null;
}

async function getJson(url: string, signal: AbortSignal): Promise<unknown> {
  const r = await fetch(url, { cache: "no-store", signal });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

function paymentsUrl(e: Endpoints) {
  const u = new URL(`${e.base.replace(/\/+$/, "")}/api/payments`);
  if (e.token) u.searchParams.set("token", e.token);
  return u.toString();
}

/** Only the public rows past `after` (a few pages at most). */
async function readEvents(after: number, signal: AbortSignal): Promise<unknown[]> {
  const out: unknown[] = [];
  let cursor = after;
  for (let i = 0; i < 10; i++) {
    const u = new URL(eventsUrl(PUBLIC, cursor, 500));
    u.searchParams.set("types", GLASS_TYPES.join(","));
    const page = await getJson(u.toString(), signal);
    if (!isObj(page) || !Array.isArray(page.data)) break;
    out.push(...page.data);
    const next = page.next_after_id;
    if (typeof next === "number" && next > cursor) cursor = next;
    else break;
    if (page.has_more !== true) break;
  }
  return out;
}

/** The glass, live: every 30 s while the tab is visible. */
export function useGlass(): Glass {
  const [glass, setGlass] = useState<Glass>({ faces: null, ticker: null, open: null });
  useEffect(() => {
    if (!HAS_GATEWAY) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let ac: AbortController | null = null;
    let busy = false;
    let log = EMPTY_LOG;
    let paid: string | null = null;
    const schedule = () => {
      if (timer) clearTimeout(timer);
      timer = alive && !document.hidden ? setTimeout(load, POLL_MS) : undefined;
    };
    const load = async () => {
      if (!alive || busy || document.hidden) return;
      busy = true;
      ac = new AbortController();
      const kill = setTimeout(() => ac?.abort(), 10_000);
      try {
        const st = stateNumbers(await getJson(stateUrl(PUBLIC), ac.signal));
        if (st.lastId !== null && st.lastId < log.after) log = EMPTY_LOG; // the log restarted
        try {
          log = foldEvents(log, await readEvents(log.after, ac.signal));
        } catch {
          // the event read failed: keep what was folded so far
        }
        try {
          paid = paidCaption(await getJson(paymentsUrl(PUBLIC), ac.signal));
        } catch {
          // /api/payments missing or down: the Paid card keeps its last (or neutral) caption
        }
        if (alive) setGlass({ faces: glassFaces(log, st, paid), ticker: log.newest, open: true });
      } catch {
        if (alive) setGlass((g) => ({ ...g, open: false }));
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
  return glass;
}
