"use client";

// D32: the firm's live feed for the office workspaces. It reads /api/state (theater token, read-only),
// folds the recent log silently so the desks start with their latest work, then follows the stream.
// Every event goes through the theater's script first: only events it accepts (contract-valid)
// reach the workspaces.

import { useEffect, useRef, useState } from "react";
import { API_URL } from "@/lib/api";
import type { UTaxEvent } from "@/theater/events";
import { assetAllowed, eventsUrl, numbersFromState, originOf, parseAssetHosts, reconnectDelayMs, seedFromState, stateUrl, streamUrl, type Endpoints } from "@/theater/live";
import type { FirmNumbers } from "@/theater/rail";
import { appendEvents, startScript, type ScriptCursor } from "@/theater/script";
import { writeSession } from "@/ceo/session";
import { applyStudio, emptyStudio, STUDIO_ONLY, type Studio, type StudioCtx } from "./artifact";
import { applyEvent, emptyOffice, type Office, type Seat, type StepCtx } from "./workspace";

const GATEWAY: Endpoints = { base: API_URL, token: process.env.NEXT_PUBLIC_THEATER_TOKEN ?? "" };
const HAS_GATEWAY = Boolean(process.env.NEXT_PUBLIC_API_URL);
const HOSTS = parseAssetHosts(process.env.NEXT_PUBLIC_ASSET_HOSTS);
const GATEWAY_ORIGIN = originOf(process.env.NEXT_PUBLIC_API_URL);
/** Events folded on open, so every desk shows its latest work at once (more when signed in: the agents' activity is in the log too). */
const LOOKBACK = 300;
const LOOKBACK_SIGNED_IN = 900;
const STATE_MS = 5000;
const quiet = { log: () => {}, onGap: () => {} };

export type FeedStatus = "connecting" | "live" | "reconnecting" | "offline";

export interface Feed {
  office: Office;
  numbers: FirmNumbers | null;
  status: FeedStatus;
  /** D37: what each desk is making right now (agent.activity and the artifact events). */
  studio: Studio;
  /**
   * The desk a live stream event last moved (never the log folded on open): what auto-follow follows.
   * `activity`: an agent.activity or artifact event (it has no step; `ts` is its log time).
   */
  live: { seat: Seat; key: string; ts?: string; activity?: true } | null;
}

/** The gateway refused the CEO session: it was revoked, or the gateway's secret changed. */
class DeadSession extends Error {}

async function fetchJson(url: string, bearer: string | null): Promise<unknown> {
  const r = await fetch(url, { cache: "no-store", signal: AbortSignal.timeout(10_000), ...(bearer ? { headers: { Authorization: `Bearer ${bearer}` } } : {}) });
  if (bearer && (r.status === 401 || r.status === 403)) throw new DeadSession();
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

/**
 * A 60 s single-use stream ticket (contract task 36/39): the CEO's session is never in a URL, so
 * a signed-in EventSource opens with `?ticket=`. Throws DeadSession when the session is refused.
 */
async function streamTicket(session: string): Promise<string> {
  const r = await fetch(`${API_URL}/api/stream-ticket`, { method: "POST", headers: { Authorization: `Bearer ${session}` }, cache: "no-store", signal: AbortSignal.timeout(10_000) });
  if (r.status === 401 || r.status === 403) throw new DeadSession();
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  const { ticket } = (await r.json()) as { ticket?: unknown };
  if (typeof ticket !== "string" || !ticket) throw new Error("no ticket");
  return ticket;
}

/** The stream URL with a ticket (and never a session). */
export function ticketStreamUrl(base: string, afterId: number, ticket: string): string {
  const u = new URL(`${base}/api/events/stream`);
  u.searchParams.set("after_id", String(Math.max(0, afterId)));
  u.searchParams.set("ticket", ticket);
  return u.toString();
}

/**
 * The firm's live feed. Signed in (D33), state and events carry the CEO's session as a Bearer
 * header and the stream opens with a fresh single-use ticket each time (never a session in a URL),
 * so the desks see what the console sees; otherwise the theater token as `?token=`. A change of
 * session reconnects; a session the gateway refuses signs this tab out, and the feed falls back
 * to the theater token.
 */
export function useFirmFeed(session: string | null = null): Feed {
  const [office, setOffice] = useState<Office>(emptyOffice);
  const [numbers, setNumbers] = useState<FirmNumbers | null>(null);
  const [status, setStatus] = useState<FeedStatus>(HAS_GATEWAY ? "connecting" : "offline");
  const [live, setLive] = useState<Feed["live"]>(null);
  const officeRef = useRef(office);
  const [studio, setStudio] = useState<Studio>(emptyStudio);
  const studioRef = useRef(studio);

  useEffect(() => {
    if (!HAS_GATEWAY) return;
    // Signed in: no query token at all (the session rides the header).
    const ep: Endpoints = session ? { base: API_URL, token: "" } : GATEWAY;
    let alive = true;
    let es: EventSource | null = null;
    let cursor: ScriptCursor = startScript();
    const flagged = new Set<string>();
    let retry: ReturnType<typeof setTimeout> | undefined;
    let attempt = 0;

    const ctx: StepCtx = {
      // A case flagged as a possible injection is never named by its mail again.
      name: (id) => (id !== null && flagged.has(id) ? "a flagged client" : id !== null && Object.hasOwn(cursor.state.caseNames, id) ? cursor.state.caseNames[id] : "a new client"),
      flagged: (id) => id !== null && flagged.has(id),
      image: (url) => (typeof url === "string" && assetAllowed(url, HOSTS, GATEWAY_ORIGIN) ? url : undefined),
    };
    const studioCtx: StudioCtx = {
      ...ctx,
      // A desk whose newest step is about a flagged case: its agent's notes may quote that mail.
      seatFlagged: (seat) => {
        const c = officeRef.current.desks[seat].steps[0]?.caseId ?? null;
        return c !== null && flagged.has(c);
      },
    };
    // D37: events the theater's script doesn't know (agent.activity, doc.*) go to the studio
    // directly, once each (a reconnect can deliver them again). Everything folds in log order.
    const studioSeen = new Set<number>();
    /** The newest event id seen of any kind: a reconnect resumes after it (activity included). */
    let maxSeen = 0;
    const wellFormed = (e: unknown): e is UTaxEvent => {
      const v = e as Partial<UTaxEvent> | null;
      return !!v && typeof v.id === "number" && typeof v.type === "string" && typeof v.ts === "string" && typeof v.actor === "string" && typeof v.payload === "object" && v.payload !== null;
    };
    const fold = (events: UTaxEvent[], live = false) => {
      const st0 = studioRef.current;
      const o0 = officeRef.current;
      let st = st0;
      let o = o0;
      for (const e of events) {
        if (wellFormed(e)) maxSeen = Math.max(maxSeen, e.id);
        if (wellFormed(e) && STUDIO_ONLY.has(e.type)) {
          if (studioSeen.has(e.id)) continue;
          studioSeen.add(e.id);
          if (studioSeen.size > 5000) studioSeen.delete(studioSeen.values().next().value as number);
          st = applyStudio(st, e, studioCtx);
          continue;
        }
        const res = appendEvents(cursor, [e], quiet);
        cursor = res.cursor;
        for (const ev of res.accepted) {
          if (ev.type === "case.flagged" && ev.case_id && (ev.payload as { kind?: unknown })?.kind === "possible_injection") flagged.add(ev.case_id);
          o = applyEvent(o, ev, ctx);
          officeRef.current = o; // seatFlagged reads the newest steps
          st = applyStudio(st, ev, studioCtx);
        }
      }
      if (live && o.lastSeat && o.lastKey !== o0.lastKey) setLive({ seat: o.lastSeat, key: o.lastKey });
      else if (live && st.lastSeat && st.lastKey !== st0.lastKey) {
        const act = st.desks[st.lastSeat].act;
        setLive({ seat: st.lastSeat, key: st.lastKey, ts: act?.ts, activity: true });
      }
      if (st !== st0) {
        studioRef.current = st;
        setStudio(st);
      }
      officeRef.current = o;
      if (o !== o0) setOffice(o);
    };

    /** The session is dead at the gateway: sign this tab out (the effect re-runs with the theater token). */
    const dead = () => {
      if (!alive) return;
      alive = false;
      writeSession(null);
    };

    const pollState = async () => {
      try {
        const s = await fetchJson(stateUrl(ep), session);
        if (!alive) return;
        const n = numbersFromState(s);
        if (n) setNumbers(n);
        return s;
      } catch (e) {
        if (e instanceof DeadSession) dead();
        return null;
      }
    };

    const onMessage = (data: string) => {
      try {
        fold([JSON.parse(data) as UTaxEvent], true);
      } catch {
        /* a malformed message is skipped, as on the theater */
      }
    };
    const retryLater = () => {
      if (!alive) return;
      setStatus("reconnecting");
      retry = setTimeout(() => alive && connect(Math.max(cursor.lastId, maxSeen)), reconnectDelayMs(attempt++));
    };

    const wire = (src: EventSource) => {
      src.onopen = () => {
        attempt = 0;
        if (alive) setStatus("live");
      };
      src.onmessage = (msg) => onMessage(msg.data);
      // Closed at once: the browser's own retry would reuse a spent ticket (401). A fresh one
      // (or the theater token again) reopens from the last event seen.
      src.onerror = () => {
        src.close();
        retryLater();
      };
    };

    const connect = (after: number) => {
      es?.close();
      es = null;
      if (!session) {
        es = new EventSource(streamUrl(ep, after));
        wire(es);
        return;
      }
      streamTicket(session).then(
        (ticket) => {
          if (!alive) return;
          es = new EventSource(ticketStreamUrl(API_URL, after, ticket));
          wire(es);
        },
        (e) => {
          if (e instanceof DeadSession) dead();
          else retryLater();
        },
      );
    };

    (async () => {
      const s = await pollState();
      if (!alive) return;
      const seed = seedFromState(s);
      for (const c of seed.cases) if (c.flagged) flagged.add(c.caseId);
      cursor = startScript({ caseNames: seed.caseNames });
      // A new read token (signed in or out) starts the desks over: the lookback is folded once, and
      // nothing from the old connection may arm auto-follow.
      officeRef.current = emptyOffice();
      setOffice(officeRef.current);
      studioRef.current = emptyStudio();
      setStudio(studioRef.current);
      setLive(null);
      const last = seed.lastId ?? 0;
      if (last > 0) {
        try {
          const look = session ? LOOKBACK_SIGNED_IN : LOOKBACK;
          const page = await fetchJson(eventsUrl(ep, Math.max(0, last - look), look), session);
          const data = (page as { data?: unknown }).data;
          if (alive && Array.isArray(data)) fold((data as UTaxEvent[]).filter((e) => typeof e?.id === "number" && e.id <= last));
        } catch (e) {
          if (e instanceof DeadSession) return dead();
          /* no history: the desks fill from the stream */
        }
      }
      if (!alive) return;
      connect(Math.max(cursor.lastId, last, maxSeen));
    })();
    const poll = setInterval(() => void pollState(), STATE_MS);

    return () => {
      alive = false;
      clearInterval(poll);
      clearTimeout(retry);
      es?.close();
      // Signed out (or in): the admin-only desks go at once, not after the next state fetch.
      studioRef.current = emptyStudio();
      setStudio(studioRef.current);
    };
  }, [session]);

  return { office, numbers, status, live, studio };
}
