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
/** D41 F47: one of the order's tasks (contract v1.20 `order.queued` / `order.task_*`). */
export type TaskStatus = "queued" | "running" | "done" | "waiting_on_you" | "blocked" | "failed" | "timeout" | "cancelled";
export interface OrderTask {
  no: number;
  text: string;
  status: TaskStatus;
  /** The department that last acted for it (seatOf on its task_no-stamped events). */
  seat: Seat | null;
}
export interface OrderCard {
  id: string;
  /** The CEO's words (admin readers; else a placeholder). */
  text: string;
  ts: string;
  steps: OrderStep[];
  /** What an agent is doing for it right now (agent.activity), while recent. */
  live: { seat: Seat; text: string; ts: string } | null;
  /** D41 F47: the order's tasks, once the gateway queued them (absent for a card with none yet). */
  tasks?: OrderTask[];
}

const ENDED: ReadonlySet<TaskStatus> = new Set(["done", "waiting_on_you", "blocked", "failed", "timeout", "cancelled"]);
const MAX_TASKS = 6;
export const taskEnded = (s: TaskStatus) => ENDED.has(s);

/** The card's header chip: "Task 2 of 3" (the running one, else the next to run), or "3 of 3 done". */
export function taskChip(tasks: readonly OrderTask[] | undefined): string | null {
  if (!tasks || tasks.length === 0) return null;
  const n = tasks.length;
  const now = tasks.find((t) => t.status === "running") ?? tasks.find((t) => t.status === "queued");
  if (now) return `Task ${tasks.indexOf(now) + 1} of ${n}`;
  return `${tasks.filter((t) => t.status === "done").length} of ${n} done`;
}

const taskNo = (v: unknown) => (typeof v === "number" && Number.isSafeInteger(v) && v > 0 ? v : null);

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
    // The CEO's own order, typed or said, or placed for the CEO by the D34 assistant (actor
    // ceo_assistant, speaker still "ceo"): the gateway links order_id by the speaker alone.
    if (ev.type === "voice.command" && (ev.actor === "ceo" || (ev.actor as string) === "ceo_assistant") && p.speaker === "ceo") {
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
    // D41 F47: the order's tasks, run one at a time by the gateway.
    if (ev.type === "order.queued") {
      if (card.tasks) continue;
      const list = Array.isArray(p.tasks) ? p.tasks.filter(isObj) : [];
      const tasks = list
        .flatMap((t): OrderTask[] => {
          const no = taskNo(t.no);
          const words = typeof t.text === "string" ? t.text.replace(/\s+/g, " ").trim() : "";
          return no ? [{ no, text: clip(words || `Task ${no}`, 200), status: "queued", seat: null }] : [];
        })
        .sort((a, b) => a.no - b.no)
        .slice(0, MAX_TASKS);
      if (tasks.length) cards[i] = { ...card, tasks };
      continue;
    }
    const no = taskNo(p.task_no);
    if (ev.type === "order.task_started" || ev.type === "order.task_done" || ev.type === "order.done") {
      if (no === null || !card.tasks || ev.type === "order.done") continue;
      const st = typeof p.status === "string" && ENDED.has(p.status as TaskStatus) ? (p.status as TaskStatus) : "done";
      cards[i] = {
        ...card,
        tasks: card.tasks.map((t) =>
          t.no !== no || taskEnded(t.status) ? t : ev.type === "order.task_started" ? (t.status === "queued" ? { ...t, status: "running" } : t) : { ...t, status: st },
        ),
      };
      continue;
    }
    if (no !== null && card.tasks?.some((t) => t.no === no)) {
      const seat = seatOf(ev);
      if (seat) cards[i] = { ...card, tasks: card.tasks.map((t) => (t.no === no ? { ...t, seat } : t)) };
    }
    const at = cards[i];
    if (ev.type === "agent.activity") {
      const seat = seatOf(ev);
      if (!seat) continue;
      const kind = p.kind;
      const tool = typeof p.tool === "string" ? p.tool.replace(/^[a-z0-9_-]+\./i, "").slice(0, 40) : "";
      const what = kind === "writing" ? "is writing…" : kind === "tool_started" && tool ? `is using ${tool}` : kind === "idle" ? null : "is thinking…";
      cards[i] = { ...at, live: what ? { seat, text: `${SEAT_NAME[seat]} ${what}`, ts: ev.ts } : at.live?.seat === seat ? null : at.live };
      continue;
    }
    if (at.steps.some((s) => s.key === `${ev.id}`)) continue;
    const d = stepOf(ev, ctx.name, ctx.flagged, ctx.image);
    if (!d) continue;
    cards[i] = { ...at, steps: [...at.steps, { key: `${ev.id}`, ts: ev.ts, seat: d.seat, text: d.text, status: d.status ?? "done" }].slice(-MAX_STEPS) };
  }
  return cards;
}
