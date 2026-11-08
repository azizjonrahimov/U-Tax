import { describe, expect, it } from "vitest";
import { activeTask, foldTour, tourTarget, type TourEvent } from "./tour";

let id = 0;
const ev = (type: string, actor: string, payload: object): TourEvent => ({ id: ++id, ts: `2026-11-08T15:00:${String(id).padStart(2, "0")}Z`, type, actor, payload });
const O = "order_600";

describe("task tour", () => {
  it("walks one department at a time and only moves on when the task ends", () => {
    const log: TourEvent[] = [
      ev("voice.command", "ceo", { speaker: "ceo", text: "1) advertise 2) hire 2 3) email clients" }),
      ev("order.queued", "system", { order_id: O, tasks: [{ no: 1, text: "Advertise the firm" }, { no: 2, text: "Hire 2 people" }, { no: 3, text: "Email our clients" }] }),
    ];
    let s = foldTour([], log);
    expect(tourTarget(s)).toBeNull(); // queued, nothing running yet
    s = foldTour(s, [ev("order.task_started", "system", { order_id: O, task_no: 1, of: 3 })]);
    expect(tourTarget(s)).toBe("cos"); // the Chief of Staff plans it first
    s = foldTour(s, [ev("tool.called", "cos", { order_id: O, task_no: 1 })]);
    expect(tourTarget(s)).toBe("cos");
    s = foldTour(s, [ev("post.published", "mkt", { order_id: O, task_no: 1 })]);
    expect(tourTarget(s)).toBe("mkt");
    s = foldTour(s, [ev("tool.called", "cos", { order_id: O, task_no: 1 })]); // CoS chatter never pulls it back
    expect(tourTarget(s)).toBe("mkt");
    s = foldTour(s, [ev("post.published", "hr", { order_id: "order_999", task_no: 1 })]); // another order
    expect(tourTarget(s)).toBe("mkt");
    s = foldTour(s, [ev("order.task_done", "mkt", { order_id: O, task_no: 1, status: "done", summary: "posted" })]);
    expect(tourTarget(s)).toBeNull(); // between tasks
    s = foldTour(s, [ev("order.task_started", "system", { order_id: O, task_no: 2, of: 3 }), ev("hr.outreach", "hr", { order_id: O, task_no: 2 })]);
    expect(tourTarget(s)).toBe("hr");
    expect(activeTask(s)?.task.no).toBe(2);
    s = foldTour(s, [ev("order.task_done", "system", { order_id: O, task_no: 2, status: "waiting_on_you" })]);
    s = foldTour(s, [ev("order.task_started", "system", { order_id: O, task_no: 3, of: 3 }), ev("client.followed_up", "intake", { order_id: O, task_no: 3 })]);
    expect(tourTarget(s)).toBe("intake");
    s = foldTour(s, [ev("order.task_done", "intake", { order_id: O, task_no: 3, status: "done" }), ev("order.done", "system", { order_id: O, of: 3 })]);
    expect(tourTarget(s)).toBeNull();
    expect(s[0].done).toBe(true);
    expect(s[0].tasks.map((t) => t.status)).toEqual(["done", "waiting_on_you", "done"]);
  });

  it("shows a contractor payment at Accounting's desk", () => {
    let s = foldTour([], [
      ev("order.queued", "system", { order_id: "order_7", tasks: [{ no: 1, text: "Share card" }] }),
      ev("order.task_started", "system", { order_id: "order_7", task_no: 1 }),
      ev("job.posted", "mkt", { order_id: "order_7", task_no: 1 }),
    ]);
    expect(tourTarget(s)).toBe("mkt");
    s = foldTour(s, [ev("payment.sent", "system", { order_id: "order_7", task_no: 1 })]);
    expect(tourTarget(s)).toBe("acct");
  });

  it("is the same whether folded at once (reload) or event by event", () => {
    id = 100;
    const log = [
      ev("order.queued", "system", { order_id: "order_8", tasks: [{ no: 1, text: "a" }, { no: 2, text: "b" }] }),
      ev("order.task_started", "system", { order_id: "order_8", task_no: 1 }),
      ev("post.published", "mkt", { order_id: "order_8", task_no: 1 }),
      ev("order.task_done", "mkt", { order_id: "order_8", task_no: 1, status: "done" }),
      ev("order.task_started", "system", { order_id: "order_8", task_no: 2 }),
    ];
    const once = foldTour([], log);
    const each = log.reduce((s, e) => foldTour(s, [e]), foldTour([], []));
    expect(once).toEqual(each);
    expect(foldTour(once, log)).toEqual(once); // the same events again change nothing
    expect(tourTarget(once)).toBe("cos");
  });
});
