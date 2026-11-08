"use client";

// D42 T3: pacing for the form and the sheet. What was already in the log when the view opened shows
// at once (a reload never replays); what arrives after plays one step at a time. Reduced motion: all at once.
// Clock-driven (one interval, elapsed time since the view opened), so events arriving mid-play never
// restart the pace.

import { useEffect, useState } from "react";

export const TIMING = { formStepMs: 450, sheetCellMs: 350 } as const;

/**
 * How many of `total` items are on screen, and the one that just played (null when nothing animates).
 * `base`: how many came from the log folded on open (shown at once); the items after it play.
 */
export function usePlayback(total: number, stepMs: number, base = total): { shown: number; playing: number | null; reduce: boolean } {
  const [t0, setT0] = useState<number | null>(null);
  const [tick, setTick] = useState(0);
  const [reduce, setReduce] = useState(false);
  const start = Math.min(base, total);

  useEffect(() => {
    const t = setTimeout(() => {
      setReduce(window.matchMedia("(prefers-reduced-motion: reduce)").matches);
      const now = Date.now();
      setT0(now);
      setTick(now);
    }, 0);
    return () => clearTimeout(t);
  }, []);

  const steps = t0 === null ? 0 : Math.floor((tick - t0) / stepMs);
  const shown = reduce ? total : Math.min(total, start + steps);
  const done = t0 !== null && (reduce || shown >= total) && tick - t0 > (total - start + 2) * stepMs;

  useEffect(() => {
    if (t0 === null || done) return;
    const iv = setInterval(() => setTick(Date.now()), Math.min(100, stepMs / 3));
    return () => clearInterval(iv);
  }, [t0, done, stepMs]);

  const playing = !reduce && shown > start && t0 !== null && tick - t0 < (shown - start + 1) * stepMs ? shown : null;
  return { shown, playing, reduce };
}
