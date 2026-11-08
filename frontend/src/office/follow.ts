// D32 /office auto-follow, as pure decisions (Office.tsx calls them; tests pin them).

import { SEATS, type Office, type Seat } from "./workspace";

export type View = "overview" | Seat;

/** Auto-follow stays on a view at least this long before moving to the next desk that acted. */
export const DWELL_MS = 6000;
/** A step older than this (by its log time) is history, not something to follow. */
export const FOLLOW_FRESH_MS = 15_000;

/**
 * The desk to follow, or null. Only a step that arrived on the live stream (`live`, never the log
 * folded on open) counts, only if it is still that desk's newest step, and only while it is recent.
 */
export function followTarget(follow: boolean, live: { seat: Seat; key: string } | null, office: Office, now: number, freshMs = FOLLOW_FRESH_MS): Seat | null {
  if (!follow || !live) return null;
  const newest = office.desks[live.seat]?.steps[0];
  if (!newest || newest.key !== live.key) return null;
  const at = Date.parse(newest.ts);
  if (!Number.isFinite(at) || Math.abs(now - at) > freshMs) return null;
  return live.seat;
}

/** How long to wait before switching, so each view gets its dwell. */
export const followDelay = (lastSwitch: number, now: number, dwell = DWELL_MS) => Math.max(0, lastSwitch + dwell - now);

/**
 * The view a URL opens: `?d=<seat>` opens that desk and stays there; a replay or fixture
 * (`?source=`) plays on the Overview's theater, so nothing may pull the view away from it.
 */
export function initialView(search: string): { view: View; follow: boolean } {
  const q = new URLSearchParams(search);
  const d = q.get("d");
  const seat = d !== null && (SEATS as readonly string[]).includes(d) ? (d as Seat) : null;
  return { view: seat ?? "overview", follow: seat === null && !q.has("source") };
}
