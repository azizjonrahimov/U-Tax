"use client";

// D33: the CEO console lives in /office as a panel, and can pop out into its own window (/ceo).
// One console at a time, across every window: two would mean two ears and two voices answering
// the CEO, and a STOP in one wouldn't fence the other's queued words. A console is live only while
// it holds the Web Lock "utax-ceo-console": the browser frees it the moment a window closes,
// crashes or navigates away, so no heartbeat (and no timer throttling) decides who is live.
// - A /ceo window (the pop-out, or a tab the CEO opened) takes the lock over when it opens.
// - The office panel waits for it; "Use it here" takes it back (a pop-out then closes itself).
// The session comes along because window.open copies this tab's sessionStorage.

import { useCallback, useEffect, useRef, useState } from "react";

export const POPOUT_NAME = "utax-ceo";
const LOCK = "utax-ceo-console";

/** "live": this console runs; "elsewhere": another window has it; "unsupported": no Web Locks (always live). */
export type ConsoleState = "waiting" | "live" | "elsewhere" | "unsupported";

const locksApi = (): LockManager | undefined => (typeof navigator !== "undefined" && "locks" in navigator ? navigator.locks : undefined);
export const canPopOut = () => !!locksApi();

/**
 * Hold the console lock while `enabled` (signed in). `takeOver`: take it on mount (a /ceo window);
 * otherwise wait for it (the panel). `take()` takes it over now.
 */
export function useConsoleLock(enabled: boolean, takeOver: boolean): { state: ConsoleState; take: () => void } {
  const [state, setState] = useState<ConsoleState>("waiting");
  const takeRef = useRef<() => void>(() => {});
  useEffect(() => {
    if (!enabled) return;
    const locks = locksApi();
    if (!locks) {
      const t = setTimeout(() => setState("unsupported"), 0);
      return () => clearTimeout(t);
    }
    let alive = true;
    let release: (() => void) | null = null;
    let waiting: AbortController | null = null;
    const hold = (steal: boolean) => {
      waiting?.abort();
      const ac = new AbortController();
      waiting = steal ? null : ac;
      locks
        .request(LOCK, steal ? { steal: true } : { signal: ac.signal }, () => {
          if (!alive) return;
          waiting = null;
          setState("live");
          return new Promise<void>((resolve) => {
            release = resolve;
          });
        })
        .catch(() => {
          // Taken over by another window (or our own wait was cancelled): wait for it again.
          release = null;
          if (!alive || ac.signal.aborted) return;
          setState("elsewhere");
          if (!takeOverSelfClose()) hold(false);
        });
    };
    takeRef.current = () => hold(true);
    hold(takeOver);
    // Still waiting a moment later: another window has it.
    const t = setTimeout(() => alive && waiting && setState((s) => (s === "waiting" ? "elsewhere" : s)), 400);
    return () => {
      alive = false;
      clearTimeout(t);
      waiting?.abort();
      release?.();
    };
  }, [enabled, takeOver]);
  const take = useCallback(() => takeRef.current(), []);
  return { state: enabled ? state : "waiting", take };
}

/** A pop-out whose console was taken back closes itself; any other window keeps waiting. */
function takeOverSelfClose(): boolean {
  if (typeof window === "undefined" || window.name !== POPOUT_NAME) return false;
  window.close();
  return true;
}

/**
 * Open the console in its own window, or bring the open one to the front (never reload it).
 * False when the browser blocked the window.
 */
export function popOut(): boolean {
  const w = window.open("", POPOUT_NAME, "popup,width=560,height=940");
  if (!w) return false;
  try {
    if (w.location.pathname !== "/ceo") w.location.href = "/ceo";
  } catch {
    w.location.href = "/ceo";
  }
  w.focus();
  return true;
}
