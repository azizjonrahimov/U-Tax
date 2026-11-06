// How long each cursor step takes on screen at 1× (the player divides by speed).

import type { CursorStep } from "./script";

/** Cursor travel between elements (matches the .cur CSS transition at 1×). */
export const MOVE_MS = 700;
/** Camera re-layout (matches the grid transition). */
export const FOCUS_MS = 650;

const BASE_MS: Record<CursorStep["action"], number> = {
  focus: FOCUS_MS,
  switch: 450,
  open: 900,
  highlight: 350,
  drag: 450,
  fill: 350,
  type: 0, // by length, below
  click: 350,
  appear: 300,
  mark: 250,
  tick: 200,
  circle: 700,
  status: 250,
  block: 1200,
  flag: 500,
  count: 450,
  say: 0, // by length, below
  dial: 800,
  fail: 1200,
};

/** Characters per second when typing; long memos type faster (the design's "real text, sped up"). */
export function typingCps(step: CursorStep): number {
  const len = step.text?.length ?? 0;
  return len > 160 ? 140 : 60;
}

/** Hold time for a step at 1×, excluding cursor travel. Typing and speech are capped so one long payload can't stall the stage. */
export function stepHoldMs(step: CursorStep): number {
  const len = step.text?.length ?? 0;
  if (step.action === "type") return Math.min(4000, Math.max(300, (len / typingCps(step)) * 1000));
  if (step.action === "say") return Math.min(6000, Math.max(1200, len * 45));
  return BASE_MS[step.action];
}

/** Whether the step's cursor travels to its target before acting. */
export const movesCursor = (step: CursorStep) => step.agent !== null && step.action !== "focus" && step.action !== "switch";
