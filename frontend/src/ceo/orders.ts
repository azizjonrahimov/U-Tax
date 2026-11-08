// D33/D37: the CEO's order cards, live. An order is the CEO's voice.command (id `order_<event id>`,
// contract v1.11.1); everything the firm does for it carries `payload.order_id`: tool calls,
// artifacts (posts, emails, invites, documents) and, for admin readers, the agents' activity
// (v1.18). The card lists those steps as they happen, with what each agent is doing right now.

import type { UTaxEvent } from "@/theater/events";
import { seatOf } from "@/office/artifact";
import { SEAT_NAME, stepOf, type Seat, type WsStep } from "@/office/workspace";

export interface OrderStep {
  key: string;
  ts: string;
  seat: Seat | null;
  text: string;
  status: WsStep["status"];
}
export interface OrderCard {
  id: string;
  /** The CEO's words (admin readers; else a placeholder). */
  text: string;
  ts: string;
  steps: OrderStep[];
  /** What an agent is doing for it right now (agent.activity), while recent. */
  live: { seat: Seat; text: string; ts: string } | null;
}

export interface OrderCtx {
  name: (caseId: string | null) => string;
  flagged: (caseId: string | null) => boolean;
  image: (url: unknown) => string | undefined;
}

const MAX_CARDS = 5;
const MAX_STEPS = 30;
const clip = (t: string, max: number) => (Array.from(t).length > max ? `${Array.from(t).slice(0, max - 1).join("")}…` : t);

type P = Record<string, unknown>;
const isObj = (v: unknown): v is P => typeof v === "object" && v !== null && !Array.isArray(v);

/** Fold a batch of the console's events into the order cards (newest card first). */
export function foldOrders(prev: readonly OrderCard[], events: readonly UTaxEvent[], ctx: OrderCtx): OrderCard[] {
  let cards = [...prev];
  for (const ev of events) {
    const p: P = isObj(ev.payload) ? ev.payload : {};
    if (ev.type === "voice.command" && ev.actor === "ceo" && p.speaker === "ceo") {
      const id = `order_${ev.id}`;
      if (cards.some((c) => c.id === id)) continue;
      const text = typeof p.text === "string" && p.text.trim() ? clip(p.text.replace(/\s+/g, " ").trim(), 200) : "Your order";
      cards = [{ id, text, ts: ev.ts, steps: [], live: null }, ...cards].slice(0, MAX_CARDS);
      continue;
    }
    const oid = typeof p.order_id === "string" ? p.order_id : null;
    if (!oid) continue;
    const i = cards.findIndex((c) => c.id === oid);
    if (i < 0) continue;
    const card = cards[i];
    if (ev.type === "agent.activity") {
      const seat = seatOf(ev);
      if (!seat) continue;
      const kind = p.kind;
      const tool = typeof p.tool === "string" ? p.tool.replace(/^[a-z0-9_-]+\./i, "").slice(0, 40) : "";
      const what = kind === "writing" ? "is writing…" : kind === "tool_started" && tool ? `is using ${tool}` : kind === "idle" ? null : "is thinking…";
      cards[i] = { ...card, live: what ? { seat, text: `${SEAT_NAME[seat]} ${what}`, ts: ev.ts } : card.live?.seat === seat ? null : card.live };
      continue;
    }
    if (card.steps.some((s) => s.key === `${ev.id}`)) continue;
    const d = stepOf(ev, ctx.name, ctx.flagged, ctx.image);
    if (!d) continue;
    cards[i] = { ...card, steps: [...card.steps, { key: `${ev.id}`, ts: ev.ts, seat: d.seat, text: d.text, status: d.status ?? "done" }].slice(-MAX_STEPS) };
  }
  return cards;
}
