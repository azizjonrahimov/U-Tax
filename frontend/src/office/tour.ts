// D41 reference: the office's "task tour" (pure; Office.tsx calls it, tests pin it).
// Copy to frontend/src/office/tour.ts. While the CEO's tasks run, the office shows ONE department
// at a time: the department doing the running task. It starts at the Chief of Staff (who plans the
// task), moves to each department that acts for that task, and moves on only when the gateway says
// the task ended (order.task_done). Events of other orders, or with no task_no, never move it.

export type Seat = "cos" | "intake" | "tax" | "qa" | "acct" | "mkt" | "hr";
export type TaskStatus = "queued" | "running" | "done" | "waiting_on_you" | "blocked" | "failed" | "timeout" | "cancelled";
export interface TourEvent { id: number; ts: string; type: string; actor?: string | null; payload?: unknown }
export interface TourTask { no: number; text: string; status: TaskStatus; seat: Seat | null; seats: Seat[]; startedAt?: string; endedAt?: string; summary?: string; lastEventId?: number }
export interface TourOrder { id: string; tasks: TourTask[]; done: boolean; doneAt?: string }

const ACTOR_SEAT: Record<string, Seat> = {
  cos: "cos", chief_of_staff: "cos", intake: "intake", tax: "tax", qa: "qa",
  acct: "acct", accounting: "acct", mkt: "mkt", marketing: "mkt", hr: "hr",
};
/** Money moves are shown at Accounting's desk; the contractor job's own steps at Marketing's. */
const TYPE_SEAT: Record<string, Seat> = {
  "payment.sent": "acct", "payment.recorded": "acct", "agent.sale": "acct", "payment.received": "acct",
  "form.step": "tax", "sheet.cell": "acct", // D42 T3: the form fills at Tax, the workbook builds at Accounting
  "job.posted": "mkt", "job.claimed": "mkt", "job.submitted": "mkt", "job.verified": "mkt", "job.failed": "mkt", "job.delivered": "mkt",
};
const ENDED: readonly TaskStatus[] = ["done", "waiting_on_you", "blocked", "failed", "timeout", "cancelled"];
const MAX_ORDERS = 5;

type P = Record<string, unknown>;
const obj = (v: unknown): P => (typeof v === "object" && v !== null && !Array.isArray(v) ? (v as P) : {});

export function seatOfEvent(ev: TourEvent): Seat | null {
  return TYPE_SEAT[ev.type] ?? (ev.actor ? ACTOR_SEAT[ev.actor] ?? null : null);
}

/** Fold events (history on open, then the live stream) into the orders' task state. Idempotent per event id. */
export function foldTour(prev: readonly TourOrder[], events: readonly TourEvent[]): TourOrder[] {
  let orders = prev.map((o) => ({ ...o, tasks: o.tasks.map((t) => ({ ...t, seats: [...t.seats] })) }));
  for (const ev of events) {
    const p = obj(ev.payload);
    const oid = typeof p.order_id === "string" ? p.order_id : null;
    if (!oid) continue;
    if (ev.type === "order.queued") {
      if (orders.some((o) => o.id === oid)) continue;
      const list = Array.isArray(p.tasks) ? p.tasks.map(obj) : [];
      const tasks: TourTask[] = list
        .map((t) => ({ no: Number(t.no), text: String(t.text ?? ""), status: "queued" as TaskStatus, seat: null, seats: [] }))
        .filter((t) => Number.isInteger(t.no) && t.no > 0)
        .sort((a, b) => a.no - b.no);
      if (tasks.length) orders = [...orders, { id: oid, tasks, done: false }].slice(-MAX_ORDERS);
      continue;
    }
    const order = orders.find((o) => o.id === oid);
    const no = Number(p.task_no);
    const task = order && Number.isInteger(no) ? order.tasks.find((t) => t.no === no) : undefined;
    if (ev.type === "order.done") {
      if (order) { order.done = true; order.doneAt = ev.ts; }
      continue;
    }
    if (!order || !task) continue;
    if (task.lastEventId !== undefined && ev.id <= task.lastEventId && ev.type !== "order.task_done") continue;
    if (ev.type === "order.task_started") {
      if (task.status === "queued") { task.status = "running"; task.startedAt = ev.ts; task.seat = "cos"; }
    } else if (ev.type === "order.task_done") {
      const st = String(p.status) as TaskStatus;
      if (!ENDED.includes(task.status)) {
        task.status = ENDED.includes(st) ? st : "done";
        task.endedAt = ev.ts;
        task.summary = typeof p.summary === "string" ? p.summary : undefined;
      }
    } else {
      const seat = seatOfEvent(ev);
      if (seat && seat !== "cos") {
        task.seat = seat; // the department acting for this task now
        if (!task.seats.includes(seat)) task.seats.push(seat);
      }
    }
    task.lastEventId = Math.max(task.lastEventId ?? 0, ev.id);
  }
  return orders;
}

/** The running task, if any (the gateway runs one at a time). */
export function activeTask(orders: readonly TourOrder[]): { order: TourOrder; task: TourTask } | null {
  for (const order of orders) for (const task of order.tasks) if (task.status === "running") return { order, task };
  return null;
}

/** Where the office should be: the running task's department, else null (normal follow / overview). */
export function tourTarget(orders: readonly TourOrder[]): Seat | null {
  const a = activeTask(orders);
  return a ? a.task.seat ?? "cos" : null;
}

/** "Task 2 of 3" for the banner, with every task's state for the stepper. */
export function taskLabel(order: TourOrder, task: TourTask): string {
  return `Task ${task.no} of ${order.tasks.length}`;
}
