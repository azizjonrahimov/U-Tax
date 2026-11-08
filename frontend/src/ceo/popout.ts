"use client";

// D33: the CEO console lives in /office as a panel, and can pop out into its own window (/ceo).
// One console at a time: two would mean two ears and two voices answering the CEO. A standalone
// /ceo window announces itself on BroadcastChannel "utax-ceo" (a beat every 2 s, "bye" when it
// closes); while one is open, the panel steps aside and offers to bring it back. The session is
// shared because window.open copies this tab's sessionStorage into the new window.

import { useCallback, useEffect, useState } from "react";

export const CEO_CHANNEL = "utax-ceo";
export const POPOUT_NAME = "utax-ceo";
const BEAT_MS = 2000;
/** No beat for this long: the pop-out is gone (it crashed, or the browser killed it). */
const GONE_MS = 6000;

type Msg = { type: "open" | "bye" | "ping" | "close" };
const isMsg = (v: unknown): v is Msg => typeof v === "object" && v !== null && ["open", "bye", "ping", "close"].includes((v as Msg).type);

/** A standalone console: tell the office it's open, and close when the panel takes it back. */
export function usePopoutBeacon(active: boolean) {
  useEffect(() => {
    if (!active || typeof BroadcastChannel === "undefined") return;
    const bc = new BroadcastChannel(CEO_CHANNEL);
    const say = (type: Msg["type"]) => bc.postMessage({ type });
    say("open");
    const beat = setInterval(() => say("open"), BEAT_MS);
    bc.onmessage = (e) => {
      if (!isMsg(e.data)) return;
      if (e.data.type === "ping") say("open");
      // Only a window this console's panel opened closes itself; a tab the CEO opened just stays.
      if (e.data.type === "close" && window.name === POPOUT_NAME) window.close();
    };
    const bye = () => say("bye");
    window.addEventListener("pagehide", bye);
    return () => {
      clearInterval(beat);
      bye();
      window.removeEventListener("pagehide", bye);
      bc.close();
    };
  }, [active]);
}

/** The office panel: whether a standalone console is open right now, and a way to bring it back. */
export function usePopoutOpen(): { open: boolean; bringBack: () => void } {
  const [open, setOpen] = useState(false);
  const [bc, setBc] = useState<BroadcastChannel | null>(null);
  useEffect(() => {
    if (typeof BroadcastChannel === "undefined") return;
    const ch = new BroadcastChannel(CEO_CHANNEL);
    let last = 0;
    ch.onmessage = (e) => {
      if (!isMsg(e.data)) return;
      if (e.data.type === "open") {
        last = Date.now();
        setOpen(true);
      } else if (e.data.type === "bye") {
        last = 0;
        setOpen(false);
      }
    };
    ch.postMessage({ type: "ping" });
    const check = setInterval(() => {
      if (last && Date.now() - last > GONE_MS) {
        last = 0;
        setOpen(false);
      }
    }, BEAT_MS);
    const t = setTimeout(() => setBc(ch), 0);
    return () => {
      clearTimeout(t);
      clearInterval(check);
      ch.close();
    };
  }, []);
  const bringBack = useCallback(() => {
    bc?.postMessage({ type: "close" });
    setOpen(false);
  }, [bc]);
  return { open, bringBack };
}

/** Open the console in its own window (the session comes along: window.open copies sessionStorage). */
export function popOut() {
  window.open("/ceo", POPOUT_NAME, "popup,width=560,height=940");
}
