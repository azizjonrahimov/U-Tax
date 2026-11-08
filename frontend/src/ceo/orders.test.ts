// The CEO's order cards: each order's steps, live.

import { describe, expect, it } from "vitest";
import type { UTaxEvent } from "@/theater/events";
import { foldOrders, type OrderCtx } from "./orders";

let id = 100;
const ev = (type: string, payload: Record<string, unknown>, extra: Partial<UTaxEvent> = {}): UTaxEvent => ({
  id: ++id, ts: `2026-11-08T11:00:${String(id % 60).padStart(2, "0")}.000Z`, type, actor: "system", case_id: null, provider: null, status: "ok", payload, tokens_in: null, tokens_out: null, cost_usd: null, ...extra,
});
const ctx: OrderCtx = { name: () => "Team #7", flagged: () => false, image: () => undefined };

describe("order cards", () => {
  it("an order collects the steps done for it, in order, and what an agent is doing now", () => {
    const order = ev("voice.command", { text: "Follow up with the customer", channel: "voice", speaker: "ceo" }, { actor: "ceo" });
    const oid = `order_${order.id}`;
    let cards = foldOrders([], [order], ctx);
    expect(cards).toEqual([{ id: oid, text: "Follow up with the customer", ts: order.ts, steps: [], live: null }]);
    cards = foldOrders(cards, [
      ev("tool.called", { tool: "send_to_agent", status: "done", order_id: oid }, { actor: "cos" }),
      ev("agent.activity", { agent: "utax-intake", kind: "tool_started", tool: "utax.follow_up", order_id: oid }, { actor: "intake" }),
      ev("client.followed_up", { kind: "memo_check", order_id: oid }, { actor: "intake", case_id: "c7" }),
      ev("tool.called", { tool: "send_email", status: "done", order_id: "order_999" }, { actor: "mkt" }), // another order: not this card
    ], ctx);
    expect(cards[0].steps.map((s) => s.seat)).toEqual(["cos", "intake"]);
    expect(cards[0].steps[1].text).toMatch(/Followed up with Team #7/);
    expect(cards[0].live).toMatchObject({ seat: "intake", text: "Intake is using follow_up" });
    cards = foldOrders(cards, [ev("agent.activity", { agent: "utax-intake", kind: "idle", order_id: oid }, { actor: "intake" })], ctx);
    expect(cards[0].live).toBeNull();
  });

  it("re-delivered events are counted once; the customer's own voice is never an order; five cards at most", () => {
    const order = ev("voice.command", { text: "hire 2 people", speaker: "ceo" }, { actor: "ceo" });
    const step = ev("tool.called", { tool: "list_candidates", status: "done", order_id: `order_${order.id}` }, { actor: "hr" });
    const once = foldOrders([], [order, step, step], ctx);
    expect(once[0].steps).toHaveLength(1);
    expect(foldOrders([], [ev("voice.command", { text: "hi", speaker: "customer" }, { actor: "intake" })], ctx)).toEqual([]);
    const many = foldOrders([], Array.from({ length: 7 }, (_, i) => ev("voice.command", { text: `order ${i}`, speaker: "ceo" }, { actor: "ceo" })), ctx);
    expect(many).toHaveLength(5);
    expect(many[0].text).toBe("order 6");
  });
});

describe("orders the D34 assistant placed", () => {
  it("get a card too (actor ceo_assistant, speaker ceo)", () => {
    const order = ev("voice.command", { text: "post the hiring ad", speaker: "ceo" }, { actor: "ceo_assistant" as UTaxEvent["actor"] });
    const cards = foldOrders([], [order, ev("tool.called", { tool: "make_ad", status: "done", order_id: `order_${order.id}` }, { actor: "mkt" })], ctx);
    expect(cards).toHaveLength(1);
    expect(cards[0].steps).toHaveLength(1);
  });
});
