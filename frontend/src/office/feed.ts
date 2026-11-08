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
import { applyEvent, emptyOffice, type Office, type StepCtx } from "./workspace";

const GATEWAY: Endpoints = { base: API_URL, token: process.env.NEXT_PUBLIC_THEATER_TOKEN ?? "" };
const HAS_GATEWAY = Boolean(process.env.NEXT_PUBLIC_API_URL);
const HOSTS = parseAssetHosts(process.env.NEXT_PUBLIC_ASSET_HOSTS);
const GATEWAY_ORIGIN = originOf(process.env.NEXT_PUBLIC_API_URL);
/** Events folded on open, so every desk shows its latest work at once. */
const LOOKBACK = 300;
const STATE_MS = 5000;
const quiet = { log: () => {}, onGap: () => {} };

export type FeedStatus = "connecting" | "live" | "reconnecting" | "offline";

export interface Feed {
  office: Office;
  numbers: FirmNumbers | null;
  status: FeedStatus;
}

async function fetchJson(url: string): Promise<unknown> {
  const r = await fetch(url, { cache: "no-store", signal: AbortSignal.timeout(10_000) });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export function useFirmFeed(): Feed {
  const [office, setOffice] = useState<Office>(emptyOffice);
  const [numbers, setNumbers] = useState<FirmNumbers | null>(null);
  const [status, setStatus] = useState<FeedStatus>(HAS_GATEWAY ? "connecting" : "offline");
  const officeRef = useRef(office);

  useEffect(() => {
    if (!HAS_GATEWAY) return;
    let alive = true;
    let es: EventSource | null = null;
    let cursor: ScriptCursor = startScript();
    const flagged = new Set<string>();
    let retry: ReturnType<typeof setTimeout> | undefined;
    let attempt = 0;

    const ctx: StepCtx = {
      name: (id) => (id !== null && Object.hasOwn(cursor.state.caseNames, id) ? cursor.state.caseNames[id] : "a new client"),
      flagged: (id) => id !== null && flagged.has(id),
      image: (url) => (typeof url === "string" && assetAllowed(url, HOSTS, GATEWAY_ORIGIN) ? url : undefined),
    };
    const fold = (events: UTaxEvent[]) => {
      const res = appendEvents(cursor, events, quiet);
      cursor = res.cursor;
      if (!res.accepted.length) return;
      let o = officeRef.current;
      for (const ev of res.accepted) {
        if (ev.type === "case.flagged" && ev.case_id && (ev.payload as { kind?: unknown })?.kind === "possible_injection") flagged.add(ev.case_id);
        o = applyEvent(o, ev, ctx);
      }
      officeRef.current = o;
      setOffice(o);
    };

    const pollState = async () => {
      try {
        const s = await fetchJson(stateUrl(GATEWAY));
        if (!alive) return;
        const n = numbersFromState(s);
        if (n) setNumbers(n);
        return s;
      } catch {
        return null;
      }
    };

    const connect = (after: number) => {
      es?.close();
      es = new EventSource(streamUrl(GATEWAY, after));
      es.onopen = () => {
        attempt = 0;
        if (alive) setStatus("live");
      };
      es.onmessage = (msg) => {
        try {
          fold([JSON.parse(msg.data) as UTaxEvent]);
        } catch {
          /* a malformed message is skipped, as on the theater */
        }
      };
      es.onerror = () => {
        es?.close();
        if (!alive) return;
        setStatus("reconnecting");
        retry = setTimeout(() => alive && connect(cursor.lastId), reconnectDelayMs(attempt++));
      };
    };

    (async () => {
      const s = await pollState();
      if (!alive) return;
      const seed = seedFromState(s);
      for (const c of seed.cases) if (c.flagged) flagged.add(c.caseId);
      cursor = startScript({ caseNames: seed.caseNames });
      const last = seed.lastId ?? 0;
      if (last > 0) {
        try {
          const page = await fetchJson(eventsUrl(GATEWAY, Math.max(0, last - LOOKBACK), LOOKBACK));
          const data = (page as { data?: unknown }).data;
          if (alive && Array.isArray(data)) fold((data as UTaxEvent[]).filter((e) => typeof e?.id === "number" && e.id <= last));
        } catch {
          /* no history: the desks fill from the stream */
        }
      }
      if (!alive) return;
      connect(Math.max(cursor.lastId, last));
    })();
    const poll = setInterval(() => void pollState(), STATE_MS);

    return () => {
      alive = false;
      clearInterval(poll);
      clearTimeout(retry);
      es?.close();
    };
  }, []);

  return { office, numbers, status };
}
