"use client";

// D41 F48 (runbook §C.4): the task the tour is showing, at the top of the department's desk.
// ORDER · TASK 2 OF 3, the task text, its status, and a stepper of every task in the order.

import type { TaskStatus, TourOrder, TourTask } from "@/office/tour";

const STATUS_WORD: Record<TaskStatus, string> = {
  queued: "queued",
  running: "running",
  done: "done",
  waiting_on_you: "waiting on you",
  blocked: "blocked",
  failed: "failed",
  timeout: "timed out",
  cancelled: "cancelled",
};
const tone = (s: TaskStatus) => (s === "running" ? "run" : s === "done" ? "done" : s === "waiting_on_you" ? "wait" : s === "queued" ? "queued" : "off");

export function TaskBanner({ order, task }: { order: TourOrder; task: TourTask }) {
  const ended = task.status !== "running" && task.status !== "queued";
  return (
    <section className="task-banner" aria-label={`Order task ${task.no} of ${order.tasks.length}`} aria-live="polite">
      <p className="tb-kick">
        order · task {task.no} of {order.tasks.length}
      </p>
      <div className="tb-row">
        <p className="tb-text" title={task.text}>
          {task.text}
        </p>
        <span className={`tb-chip ${tone(task.status)}`}>
          {task.status === "running" && <i className="tb-pulse" aria-hidden />}
          {ended && task.status === "done" ? `✓ Task ${task.no} done` : STATUS_WORD[task.status]}
        </span>
      </div>
      <ol className="tb-steps" aria-label="Tasks in this order">
        {order.tasks.map((t) => (
          <li key={t.no} className={`tb-step ${tone(t.status)}${t.no === task.no ? " cur" : ""}`} title={`Task ${t.no}: ${t.text} (${STATUS_WORD[t.status]})`}>
            <span aria-hidden>{t.status === "done" ? "✓" : t.status === "queued" ? "○" : t.no}</span>
            <span className="sr-only">
              Task {t.no}, {STATUS_WORD[t.status]}
            </span>
          </li>
        ))}
      </ol>
    </section>
  );
}
