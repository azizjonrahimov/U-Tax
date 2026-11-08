// The CEO's order cards: each order's steps, live.

import { describe, expect, it } from "vitest";
import type { UTaxEvent } from "@/theater/events";
import { foldOrders, taskChip, type OrderCtx } from "./orders";

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

describe("D41 F47: an order's tasks", () => {
  const start = () => {
    const order = ev("voice.command", { text: "post the ad, pay the designer, email Team #1", speaker: "ceo" }, { actor: "ceo" });
    const oid = `order_${order.id}`;
    const queued = ev("order.queued", { order_id: oid, of: 3, tasks: [{ no: 2, text: "pay the designer" }, { no: 1, text: "post the ad" }, { no: 3, text: "email Team #1" }] });
    return { oid, cards: foldOrders([], [order, queued], ctx) };
  };

  it("order.queued attaches the tasks, queued and in order; the chip says which one is next", () => {
    const { cards } = start();
    expect(cards[0].tasks).toEqual([
      { no: 1, text: "post the ad", status: "queued", seat: null },
      { no: 2, text: "pay the designer", status: "queued", seat: null },
      { no: 3, text: "email Team #1", status: "queued", seat: null },
    ]);
    expect(cards[0].steps).toEqual([]);
    expect(taskChip(cards[0].tasks)).toBe("Task 1 of 3");
  });

  it("task_started runs it, a task_no-stamped event sets its department, task_done ends it with its status", () => {
    const { oid, cards: c0 } = start();
    let cards = foldOrders(c0, [
      ev("order.task_started", { order_id: oid, task_no: 1, of: 3, text: "post the ad" }),
      ev("tool.called", { tool: "post_social", status: "done", order_id: oid, task_no: 1 }, { actor: "mkt" }),
    ], ctx);
    expect(cards[0].tasks?.[0]).toMatchObject({ status: "running", seat: "mkt" });
    expect(cards[0].tasks?.[1]).toMatchObject({ status: "queued", seat: null });
    expect(taskChip(cards[0].tasks)).toBe("Task 1 of 3");
    cards = foldOrders(cards, [
      ev("order.task_done", { order_id: oid, task_no: 1, of: 3, status: "done", summary: "posted", by: "mkt" }),
      ev("order.task_started", { order_id: oid, task_no: 2, of: 3, text: "pay the designer" }),
    ], ctx);
    expect(cards[0].tasks?.map((t) => t.status)).toEqual(["done", "running", "queued"]);
    expect(taskChip(cards[0].tasks)).toBe("Task 2 of 3");
    cards = foldOrders(cards, [
      ev("order.task_done", { order_id: oid, task_no: 2, of: 3, status: "waiting_on_you", summary: "", by: "acct" }),
      ev("order.task_started", { order_id: oid, task_no: 3, of: 3, text: "" }),
      ev("order.task_done", { order_id: oid, task_no: 3, of: 3, status: "cancelled", summary: "", by: "system" }),
      ev("order.done", { order_id: oid, of: 3, done: 1, waiting_on_you: 1, other: 1 }),
    ], ctx);
    expect(cards[0].tasks?.map((t) => t.status)).toEqual(["done", "waiting_on_you", "cancelled"]);
    expect(taskChip(cards[0].tasks)).toBe("1 of 3 done");
  });

  it("an ended task stays ended, a re-delivered queue never resets it, unknown statuses read as done, other orders never move it", () => {
    const { oid, cards: c0 } = start();
    const queuedAgain = ev("order.queued", { order_id: oid, of: 1, tasks: [{ no: 1, text: "something else" }] });
    let cards = foldOrders(c0, [
      ev("order.task_started", { order_id: oid, task_no: 1, of: 3, text: "" }),
      ev("order.task_done", { order_id: oid, task_no: 1, of: 3, status: "weird", summary: "", by: "mkt" }),
      ev("order.task_started", { order_id: oid, task_no: 1, of: 3, text: "" }),
      queuedAgain,
      ev("order.task_started", { order_id: "order_1", task_no: 2, of: 3, text: "" }),
      ev("tool.called", { tool: "send_email", status: "done", order_id: oid }, { actor: "acct" }), // no task_no: no seat
    ], ctx);
    expect(cards[0].tasks?.map((t) => [t.status, t.seat])).toEqual([["done", null], ["queued", null], ["queued", null]]);
    expect(cards[0].tasks?.[0].text).toBe("post the ad");
    cards = foldOrders(cards, [ev("order.queued", { order_id: oid, tasks: "nope" })], ctx);
    expect(cards[0].tasks).toHaveLength(3);
  });

  it("a card with no queued tasks has none, and no chip", () => {
    const order = ev("voice.command", { text: "one thing", speaker: "ceo" }, { actor: "ceo" });
    const cards = foldOrders([], [order, ev("order.queued", { order_id: `order_${order.id}`, tasks: [{ no: 0, text: "bad" }] })], ctx);
    expect(cards[0].tasks).toBeUndefined();
    expect(taskChip(cards[0].tasks)).toBeNull();
  });
});
