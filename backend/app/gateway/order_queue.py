"""D41 reference: the CEO's tasks, run one at a time across the whole firm (pure; no I/O).

Copied from docs/designs/fixes/reference/order_queue.py. The gateway owns one OrderQueue,
rebuilds it from the event log on boot with `apply()`, and turns every decision here into events (see FIXES-RUNBOOK §B).
Times are float seconds (time.time()); tests pass their own clock.
"""
from __future__ import annotations

from dataclasses import dataclass, field

TASK_IDLE_S = 90    # a running task with no order-linked event for this long ...
TASK_SOFT_S = 240   # ... after running this long, times out
TASK_HARD_S = 420   # a task never runs longer than this
ENDED = ("done", "waiting_on_you", "blocked", "failed", "timeout", "cancelled")


@dataclass
class Task:
    no: int
    text: str
    status: str = "queued"          # queued | running | one of ENDED
    started_at: float | None = None
    last_at: float | None = None
    ended_at: float | None = None
    seats: list[str] = field(default_factory=list)   # departments that acted, in order
    waiting: bool = False  # an approval.requested while it ran: it ends waiting_on_you


@dataclass
class Order:
    order_id: str
    tasks: list[Task]
    done_sent: bool = False  # its order.done is written (once)

    @property
    def of(self) -> int:
        return len(self.tasks)

    @property
    def finished(self) -> bool:
        return all(t.status in ENDED for t in self.tasks)


class OrderQueue:
    def __init__(self, idle_s: float = TASK_IDLE_S, soft_s: float = TASK_SOFT_S, hard_s: float = TASK_HARD_S):
        self.orders: list[Order] = []
        self.idle_s, self.soft_s, self.hard_s = idle_s, soft_s, hard_s

    # -- reads ---------------------------------------------------------------------------
    def get(self, order_id: str) -> Order | None:
        return next((o for o in self.orders if o.order_id == order_id), None)

    def active(self) -> tuple[Order, Task] | None:
        for o in self.orders:
            for t in o.tasks:
                if t.status == "running":
                    return o, t
        return None

    def next_to_start(self) -> tuple[Order, Task] | None:
        """FIFO: the first queued task of the oldest unfinished order, only when nothing runs."""
        if self.active():
            return None
        for o in self.orders:
            for t in o.tasks:
                if t.status == "queued":
                    return o, t
        return None

    def task_no_for(self, order_id: str) -> int | None:
        """The task an order-linked event belongs to: the running one, else the last started."""
        o = self.get(order_id)
        if o is None:
            return None
        started = [t for t in o.tasks if t.started_at is not None]
        running = [t for t in started if t.status == "running"]
        pick = running or started[-1:]
        return pick[0].no if pick else None

    def due_timeout(self, now: float) -> tuple[Order, Task] | None:
        a = self.active()
        if a is None:
            return None
        o, t = a
        started = t.started_at if t.started_at is not None else now
        last = t.last_at if t.last_at is not None else started
        age, idle = now - started, now - last  # never `x or now`: a clock may start at 0
        if age >= self.hard_s or (age >= self.soft_s and idle >= self.idle_s):
            return o, t
        return None

    # -- writes (call these when the matching event is written, or from apply()) ------------
    def add(self, order_id: str, tasks: list[str]) -> Order:
        existing = self.get(order_id)
        if existing:
            return existing
        o = Order(order_id, [Task(i + 1, text) for i, text in enumerate(tasks)])
        self.orders.append(o)
        return o

    def start(self, order_id: str, no: int, now: float) -> None:
        t = self._task(order_id, no)
        if t and t.status == "queued":
            t.status, t.started_at, t.last_at = "running", now, now

    def touch(self, order_id: str, no: int | None, actor: str | None, now: float) -> None:
        t = self._task(order_id, no) if no else None
        if t and t.status == "running":
            t.last_at = now
            if actor and actor not in ("system", "ceo", "chief_of_staff", "cos") and actor not in t.seats:
                t.seats.append(actor)

    def finish(self, order_id: str, no: int, status: str, now: float) -> bool:
        """True if this ended a running (or queued) task; False if it had already ended."""
        assert status in ENDED, status
        t = self._task(order_id, no)
        if t is None or t.status in ENDED:
            return False
        t.status, t.ended_at = status, now
        return True

    def cancel_all(self, now: float) -> list[tuple[Order, Task]]:
        """STOP EVERYTHING / undo: every running or queued task ends as cancelled."""
        ended = []
        for o in self.orders:
            for t in o.tasks:
                if t.status not in ENDED:
                    t.status, t.ended_at = "cancelled", now
                    ended.append((o, t))
        return ended

    def cancel_order(self, order_id: str, now: float) -> list[tuple[Order, Task]]:
        o = self.get(order_id)
        ended = []
        for t in (o.tasks if o else []):
            if t.status not in ENDED:
                t.status, t.ended_at = "cancelled", now
                ended.append((o, t))
        return ended

    def apply(self, etype: str, payload: dict, now: float, actor: str | None = None) -> None:
        """Rebuild from the log (boot). A task still running after the replay restarts its clock."""
        oid = payload.get("order_id")
        if etype == "order.queued":
            self.add(oid, [t["text"] for t in sorted(payload["tasks"], key=lambda t: t["no"])])
        elif etype == "order.task_started":
            self.start(oid, payload["task_no"], now)
        elif etype == "order.task_done":
            self.finish(oid, payload["task_no"], payload["status"], now)
        elif etype == "order.done":
            o = self.get(oid)
            if o is not None:
                o.done_sent = True
        elif oid and payload.get("task_no"):
            self.touch(oid, payload["task_no"], actor, now)
            t = self._task(oid, payload["task_no"])
            if etype == "approval.requested" and t is not None and t.status == "running":
                t.waiting = True

    def _task(self, order_id: str, no: int | None) -> Task | None:
        o = self.get(order_id)
        if o is None or not no or not 1 <= no <= len(o.tasks):
            return None
        return o.tasks[no - 1]
