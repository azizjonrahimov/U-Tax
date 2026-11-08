from order_queue import OrderQueue


def test_runs_one_task_at_a_time_fifo_across_orders():
    q = OrderQueue()
    q.add("order_10", ["advertise the firm", "hire 2 people"])
    q.add("order_20", ["email our clients"])
    o, t = q.next_to_start(); assert (o.order_id, t.no) == ("order_10", 1)
    q.start("order_10", 1, 0)
    assert q.next_to_start() is None                     # something runs: nothing else starts
    assert q.finish("order_10", 1, "done", 5)
    assert not q.finish("order_10", 1, "done", 6)        # a second task_done is a duplicate
    o, t = q.next_to_start(); assert (o.order_id, t.no) == ("order_10", 2)
    q.start("order_10", 2, 6); q.finish("order_10", 2, "waiting_on_you", 9)
    assert q.get("order_10").finished
    o, t = q.next_to_start(); assert (o.order_id, t.no) == ("order_20", 1)


def test_timeouts():
    q = OrderQueue(idle_s=90, soft_s=240, hard_s=420)
    q.add("order_1", ["a b c"]); q.start("order_1", 1, 0)
    assert q.due_timeout(200) is None                    # young
    q.touch("order_1", 1, "mkt", 200)
    assert q.due_timeout(250) is None                    # old but active 50 s ago
    assert q.due_timeout(291) is not None                # old and idle 91 s
    q.touch("order_1", 1, "mkt", 400)
    assert q.due_timeout(420) is not None                # hard cap


def test_task_no_for_and_seats():
    q = OrderQueue()
    q.add("order_1", ["x one", "y two"])
    assert q.task_no_for("order_1") is None
    q.start("order_1", 1, 0); q.touch("order_1", 1, "chief_of_staff", 1); q.touch("order_1", 1, "marketing", 2)
    assert q.task_no_for("order_1") == 1 and q.get("order_1").tasks[0].seats == ["marketing"]
    q.finish("order_1", 1, "done", 3)
    assert q.task_no_for("order_1") == 1                 # late events still belong to task 1
    q.start("order_1", 2, 4)
    assert q.task_no_for("order_1") == 2


def test_rebuild_from_log_and_cancel():
    q = OrderQueue()
    log = [("order.queued", {"order_id": "order_7", "tasks": [{"no": 2, "text": "b b"}, {"no": 1, "text": "a a"}]}),
           ("order.task_started", {"order_id": "order_7", "task_no": 1}),
           ("order.task_done", {"order_id": "order_7", "task_no": 1, "status": "done"}),
           ("order.task_started", {"order_id": "order_7", "task_no": 2})]
    for et, p in log:
        q.apply(et, p, 100)
    o, t = q.active(); assert (t.no, t.text) == (2, "b b")
    ended = q.cancel_all(101)
    assert [(x.order_id, y.no) for x, y in ended] == [("order_7", 2)] and q.active() is None
