// D32 review: /office auto-follow waits for a real, live agent step; never the initial replay.

import { describe, expect, it } from "vitest";
import type { UTaxEvent } from "@/theater/events";
import { ticketStreamUrl } from "./feed";
import { followDelay, followTarget, initialView } from "./follow";
import { applyEvent, emptyOffice, type StepCtx } from "./workspace";

const ev = (id: number, type: string, payload: Record<string, unknown>, extra: Partial<UTaxEvent> = {}): UTaxEvent => ({
  id, ts: `2026-11-07T21:00:${String(id % 60).padStart(2, "0")}.000Z`, type, actor: "system", case_id: null, provider: null, status: "ok", payload, tokens_in: null, tokens_out: null, cost_usd: null, ...extra,
});
const ctx: StepCtx = { name: () => "Team #1", flagged: () => false, image: () => undefined };

describe("auto-follow", () => {
  const o = applyEvent(emptyOffice(), ev(1, "tool.called", { tool: "compute_tax", status: "done" }, { actor: "tax" }), ctx);
  const t0 = Date.parse("2026-11-07T21:00:01.000Z");

  it("follows only a fresh step that arrived live, and only while following", () => {
    expect(followTarget(true, { seat: "tax", key: "1" }, o, t0 + 1000)).toBe("tax");
    expect(followTarget(false, { seat: "tax", key: "1" }, o, t0 + 1000)).toBeNull();
    expect(followTarget(true, null, o, t0 + 1000)).toBeNull(); // the lookback fold sets no live step
    expect(followTarget(true, { seat: "tax", key: "1" }, o, t0 + 15_001)).toBeNull(); // history
    expect(followTarget(true, { seat: "tax", key: "1" }, o, t0 - 15_001)).toBeNull(); // clock far off
    expect(followTarget(true, { seat: "tax", key: "99" }, o, t0)).toBeNull(); // not the desk's newest step
    expect(followTarget(true, { seat: "acct", key: "1" }, o, t0)).toBeNull(); // an empty desk
  });

  it("each view gets its dwell from the last switch", () => {
    expect(followDelay(1000, 3000)).toBe(4000);
    expect(followDelay(1000, 9000)).toBe(0);
  });

  it("a shared desk link or a replay source turns auto-follow off", () => {
    expect(initialView("")).toEqual({ view: "overview", follow: true });
    expect(initialView("?source=fixture")).toEqual({ view: "overview", follow: false });
    expect(initialView("?source=replay&d=tax")).toEqual({ view: "tax", follow: false });
    expect(initialView("?d=tax")).toEqual({ view: "tax", follow: false });
    expect(initialView("?d=__proto__")).toEqual({ view: "overview", follow: true });
  });
});

describe("the signed-in stream URL (task 36/39)", () => {
  it("carries a ticket and the last event id, never a session or token", () => {
    const u = new URL(ticketStreamUrl("https://gw.example", 57, "tk.abc"));
    expect(u.pathname).toBe("/api/events/stream");
    expect(u.searchParams.get("ticket")).toBe("tk.abc");
    expect(u.searchParams.get("after_id")).toBe("57");
    expect(u.searchParams.has("token")).toBe(false);
    expect(new URL(ticketStreamUrl("https://gw.example", -3, "t")).searchParams.get("after_id")).toBe("0");
  });
});
