"use client";

// D33: the CEO console lives in /office as a panel, and can pop out into its own window (/ceo).
// One console at a time, across every window: two would mean two ears and two voices answering
// the CEO, and a STOP in one wouldn't fence the other's queued words. A console is live only while
// it holds the Web Lock "utax-ceo-console": the browser frees it the moment a window closes,
// crashes or navigates away, so no heartbeat (and no timer throttling) decides who is live.
// - A /ceo window (the pop-out, or a tab the CEO opened) takes the lock over when it opens.
// - The office panel waits for it; "Use it here" takes it back (a pop-out then closes).
// - No Web Locks (an old browser, or plain http on a LAN address): the /ceo page runs, and the
//   panel only links to it ("unsupported"), so two consoles can never both be live.
// The session comes along because window.open copies this tab's sessionStorage.

import { useCallback, useEffect, useRef, useState } from "react";
import { SESSION_KEY } from "./session";

export const POPOUT_NAME = "utax-ceo";
const LOCK = "utax-ceo-console";

/** "live": this console runs; "elsewhere": another window has it; "unsupported": no Web Locks. */
export type ConsoleState = "waiting" | "live" | "elsewhere" | "unsupported";

const locksApi = (): LockManager | undefined => (typeof navigator !== "undefined" && "locks" in navigator ? navigator.locks : undefined);
export const canPopOut = () => !!locksApi();

/**
 * Hold the console lock while `enabled` (signed in). `takeOver`: take it on mount (a /ceo window);
 * otherwise wait for it (the panel). `take()` takes it over now. `everElsewhere`: the console has
 * been in another window since this one mounted (a grant after that isn't a fresh opening).
 */
export function useConsoleLock(enabled: boolean, takeOver: boolean): { state: ConsoleState; take: () => void; everElsewhere: boolean } {
  const [state, setState] = useState<ConsoleState>("waiting");
  const [everElsewhere, setEverElsewhere] = useState(false);
  const takeRef = useRef<() => void>(() => {});
  useEffect(() => {
    if (!enabled) return;
    const locks = locksApi();
    if (!locks) {
      const t = setTimeout(() => setState("unsupported"), 0);
      return () => clearTimeout(t);
    }
    let alive = true;
    /** Each request has a generation; only the newest one may change anything. */
    let gen = 0;
    let release: (() => void) | null = null;
    let waiting: AbortController | null = null;
    let stealing = false;
    const hold = (steal: boolean) => {
      if (steal && stealing) return; // a takeover is already on its way (a double click)
      const mine = ++gen;
      waiting?.abort();
      const ac = new AbortController();
      waiting = steal ? null : ac;
      stealing = steal;
      let resolveMine: (() => void) | null = null;
      locks
        .request(LOCK, steal ? { steal: true } : { signal: ac.signal }, () => {
          if (!alive || mine !== gen) return; // outdated: release at once
          waiting = null;
          stealing = false;
          setState("live");
          return new Promise<void>((resolve) => {
            resolveMine = resolve;
            release = resolve;
          });
        })
        .catch(() => {
          // Taken over by another window (or our own wait was cancelled): wait for it again.
          if (release === resolveMine) release = null;
          if (!alive || mine !== gen || ac.signal.aborted) return;
          stealing = false;
          setState("elsewhere");
          setEverElsewhere(true);
          if (!(takeOver && selfClose())) hold(false);
        });
    };
    takeRef.current = () => hold(true);
    hold(takeOver);
    // Still waiting a moment later: another window has it.
    const t = setTimeout(() => {
      if (alive && waiting) {
        setState((s) => (s === "waiting" ? "elsewhere" : s));
        setEverElsewhere(true);
      }
    }, 400);
    return () => {
      alive = false;
      gen += 1;
      clearTimeout(t);
      waiting?.abort();
      release?.();
    };
  }, [enabled, takeOver]);
  const take = useCallback(() => takeRef.current(), []);
  return { state: enabled ? state : "waiting", take, everElsewhere };
}

/** A pop-out (the /ceo page in the window this console opened) whose console was taken back closes itself. */
function selfClose(): boolean {
  if (typeof window === "undefined" || window.name !== POPOUT_NAME) return false;
  window.close();
  return true;
}

/**
 * Open the console in its own window, or bring the open one to the front (never reload it). A
 * reused window gets this tab's current session (it may have signed out since). False when the
 * browser blocked the window.
 */
export function popOut(): boolean {
  const w = window.open("", POPOUT_NAME, "popup,width=560,height=940");
  if (!w) return false;
  try {
    const mine = sessionStorage.getItem(SESSION_KEY);
    const theirs = w.sessionStorage.getItem(SESSION_KEY);
    if (mine && theirs !== mine) {
      w.sessionStorage.setItem(SESSION_KEY, mine);
      if (w.location.pathname === "/ceo") w.location.reload();
    }
    if (w.location.pathname !== "/ceo") w.location.href = "/ceo";
  } catch {
    w.location.href = "/ceo";
  }
  w.focus();
  return true;
}
