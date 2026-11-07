"""Task 17b: the buy_tool money items left open when task 17 was timeboxed."""

from __future__ import annotations

from decimal import Decimal

from app.gateway.ports.store import EventWriteError

from .test_task10 import monid
from .test_task10_review import buy
from .test_task17 import crash_left_intent, lost, unsure_events


def resolve(h, **args):
    return h.gw.perform("resolve_purchase", {"key": "k1", **args})


def test_a_replayed_resolve_never_settles_a_newer_unchecked_episode(h):
    h.gw.ports.toolbuy = monid(lost)
    buy(h.gw)  # episode 0: unsure
    assert resolve(h, charged=False).status == "done"  # the CEO checked it: not charged
    buy(h.gw)  # episode 1: unsure again, nobody has checked it yet
    replay = resolve(h, charged=False)  # the first request again (a client retry)
    assert replay.status == "refused" and replay.error["code"] == "wrong_status"
    assert "episode: 1" in replay.error["message"]
    assert len(unsure_events(h, "k1")) == 2 and "buy:k1" in h.gw.ports.toolbuy._unsure
    checked = resolve(h, charged=True, cost_usd="0.0300", episode=1)  # checked, named
    assert checked.status == "done" and checked.data["episode"] == 1
    assert h.gw.state.spent_by_provider == {"monid": Decimal("0.0300")}


def test_a_leftover_recorded_by_a_resolve_is_checked_before_it_is_settled(h):
    h.gw.ports.toolbuy = monid(lost)
    buy(h.gw)
    assert resolve(h, charged=False).status == "done"
    crash_left_intent(h, key="k1")  # a crash left a second, unrecorded attempt
    replay = resolve(h, charged=False)
    assert replay.status == "refused" and "episode: 1" in replay.error["message"]
    assert len(unsure_events(h, "k1")) == 2  # recorded for the CEO, not settled
    assert resolve(h, charged=False, episode=1).status == "done"


def test_an_episode_that_is_not_a_whole_number_is_bad_input(h):
    h.gw.ports.toolbuy = monid(lost)
    buy(h.gw)
    for bad in (-1, "1", True, 1.5):
        assert resolve(h, charged=False, episode=bad).error["code"] == "bad_input", bad


def test_a_charge_whose_commit_ack_was_lost_still_reaches_the_spend(h):
    h.gw.ports.toolbuy = monid(lost)
    buy(h.gw)
    real = h.gw.store.append_batch

    def committed_then_lost(events, **kw):
        out = real(events, **kw)
        if any(e.type == "cost.booked" for e in events):
            raise EventWriteError("commit reply lost")
        return out

    h.gw.store.append_batch = committed_then_lost
    assert resolve(h, charged=True, cost_usd="0.0400").status == "error"
    h.gw.store.append_batch = real
    h.new_case()  # the firm moves on: newer events land before the retry
    again = resolve(h, charged=True, cost_usd="0.0400")
    assert again.error["code"] == "wrong_status"  # once only
    assert h.gw.state.spent_by_provider == {"monid": Decimal("0.0400")}  # booked, once
    resolve(h, charged=True, cost_usd="0.0400")
    assert h.gw.state.spent_by_provider == {"monid": Decimal("0.0400")}
