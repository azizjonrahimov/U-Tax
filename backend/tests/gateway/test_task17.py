"""Task 17: buy_tool hardening. Every path that may have paid Monid stays remembered, until
the CEO resolves it, across retries, restarts and day changes; a resolution happens once;
a paid result that can't travel as JSON is booked, never lost with its charge."""

from __future__ import annotations

import json
import threading
from datetime import timedelta
from decimal import Decimal

import httpx

from app.gateway.ports.store import EventWriteError

from ..adapters.test_monid import completed
from .test_task10 import monid
from .test_task10_review import buy


def lost(request):
    raise httpx.ReadTimeout("gone", request=request)


def failing_once(h, event_type="tool.bought"):
    """The next _emit carrying `event_type` raises EventWriteError (a store hiccup)."""
    real = h.gw._emit
    state = {"left": 1}

    def emit(events):
        if state["left"] and any(e.type == event_type for e in events):
            state["left"] -= 1
            raise EventWriteError("store hiccup")
        return real(events)

    h.gw._emit = emit


def crash_left_intent(h, key="c1", query="delaware startups", days_ago=0):
    """What a crash between the intent and Monid's answer leaves on disk."""
    fp = h.gw._buy_fingerprint("enrichment", {"query": query})
    ts = (h.clock.now - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")
    h.gw._buy_intents.put(f"buy:{key}", {"tool": "enrichment", "fingerprint": fp,
                                        "ceiling": "0.05", "ts": ts})


def unsure_events(h, key):
    return [e for e in h.events("tool.bought")
            if (e.private or {}).get("key") == f"buy:{key}" and (e.private or {}).get("unsure")]


# ---------------------------------------------------------------- 1. intents that may have paid


def test_a_crash_left_intent_is_never_erased_by_a_retry(make_harness):
    h = make_harness()
    crash_left_intent(h)
    fresh = monid()
    h.ports.toolbuy = fresh
    gw = h.restart()
    for _ in range(2):  # retries never clear it, never reach Monid
        r = buy(gw, key="c1")
        assert r.status == "refused" and "may already be charged" in r.error["message"]
    assert fresh.calls == [] and len(unsure_events(h, "c1")) == 1  # on the log, once
    again = monid()
    h.ports.toolbuy = again
    gw = h.restart()
    assert "buy:c1" in again._unsure and buy(gw, key="c1").status == "refused"
    h.ok("resolve_purchase", key="c1", cost_usd=None)  # the CEO checked Monid's runs
    assert buy(h.gw, key="c1").status == "done" and len(again.calls) == 1


def test_an_intent_from_an_earlier_day_still_blocks_its_key(make_harness):
    # a restart after midnight used to drop the intent: the key was bought (charged) again
    h = make_harness()
    crash_left_intent(h, days_ago=1)
    fresh = monid()
    h.ports.toolbuy = fresh
    gw = h.restart()
    assert "buy:c1" not in fresh._unsure  # its ceiling counts on its own day only
    r = buy(gw, key="c1")
    assert r.status == "refused" and "may already be charged" in r.error["message"]
    assert fresh.calls == [] and len(unsure_events(h, "c1")) == 1


def test_an_unsure_whose_record_failed_is_refused_on_retry_and_survives_a_restart(make_harness):
    h = make_harness()
    port = monid(lost)
    h.gw.ports.toolbuy = port
    failing_once(h)
    first = buy(h.gw)
    assert first.status == "error" and first.error["code"] == "store_unavailable"
    r = buy(h.gw)  # the retry the error asks for
    assert r.status == "refused" and "may already be charged" in r.error["message"]
    assert len(unsure_events(h, "k1")) == 1
    fresh = monid()
    h.ports.toolbuy = fresh
    gw = h.restart()
    assert "buy:k1" in fresh._unsure and buy(gw).status == "refused" and fresh.calls == []


def test_a_charge_whose_events_failed_is_booked_on_the_retry_not_bought_again(h):
    port = monid()
    h.gw.ports.toolbuy = port
    failing_once(h)
    assert buy(h.gw).status == "error" and len(port.calls) == 1  # charged, not logged
    r = buy(h.gw)
    assert r.status == "done" and r.data["bought"] is True and len(port.calls) == 1
    assert r.data["result"] == {"results": [{"title": "Team #7"}]}
    assert len([e for e in h.events("cost.booked") if e.provider == "monid"]) == 1
    assert h.gw.state.spent_by_provider["monid"] == Decimal("0.0013")
    assert h.gw._buy_intents.get("buy:k1") is None
    assert buy(h.gw).data["duplicate"] is True and len(port.calls) == 1


def test_a_recovered_charge_retried_with_other_input_is_booked_then_refused(h):
    port = monid()
    h.gw.ports.toolbuy = port
    failing_once(h)
    buy(h.gw, query="q1")
    r = buy(h.gw, query="q2")
    assert r.status == "refused" and "different purchase" in r.error["message"]
    assert len([e for e in h.events("cost.booked") if e.provider == "monid"]) == 1
    assert buy(h.gw, query="q1").data["duplicate"] is True and len(port.calls) == 1


def test_a_key_can_go_unsure_again_after_a_not_charged_resolution(h):
    # each episode keeps its own records: the second unsure used to dedupe into the first
    h.gw.ports.toolbuy = monid(lost)
    buy(h.gw)
    h.ok("resolve_purchase", key="k1", cost_usd=None)
    h.gw.ports.toolbuy = monid(lost)
    buy(h.gw)  # lost again
    r = buy(h.gw)
    assert r.status == "refused" and "may already be charged" in r.error["message"]
    assert len(unsure_events(h, "k1")) == 2
    h.ok("resolve_purchase", key="k1", cost_usd="0.0400")
    assert h.gw.state.spent_by_provider["monid"] == Decimal("0.0400")
    assert buy(h.gw).data["duplicate"] is True


# ---------------------------------------------------------------- 2. a result that can't travel


def test_a_nan_result_is_booked_but_neither_stored_nor_relayed(h):
    out = {"results": [{"title": "Team #7", "score": float("nan")}]}
    h.gw.ports.toolbuy = monid(lambda request: httpx.Response(
        200, content=json.dumps(completed(output=out)).encode(),
        headers={"content-type": "application/json"}))
    r = buy(h.gw)
    assert r.status == "done" and r.data["bought"] is True and r.data["result"] is None
    json.dumps(r.as_dict(), allow_nan=False)  # the MCP reply renders
    assert h.events("tool.bought")[-1].private["result"] is None
    assert len([e for e in h.events("cost.booked") if e.provider == "monid"]) == 1


def test_a_normal_result_is_relayed_and_kept(h):
    h.gw.ports.toolbuy = monid()
    r = buy(h.gw)
    assert r.data["result"] == {"results": [{"title": "Team #7"}]}
    assert h.events("tool.bought")[-1].private["result"] == r.data["result"]


# ---------------------------------------------------------------- 3-4. resolve_purchase


def test_resolve_names_the_agents_key_exactly(h):
    # "buy:x" used to resolve the agent key "x" (another, maybe charged, purchase)
    h.gw.ports.toolbuy = monid(lost)
    buy(h.gw, key="buy:x")
    buy(h.gw, key="x")
    h.ok("resolve_purchase", key="buy:x", cost_usd=None)
    h.gw.ports.toolbuy = monid()
    assert buy(h.gw, key="buy:x").status == "done"
    still = buy(h.gw, key="x")
    assert still.status == "refused" and "may already be charged" in still.error["message"]


def test_a_purchase_is_resolved_once(h):
    h.gw.ports.toolbuy = monid(lost)
    buy(h.gw)
    h.ok("resolve_purchase", key="k1", cost_usd="0.0400")
    again = h.gw.perform("resolve_purchase", {"key": "k1", "cost_usd": "0.9000"})
    assert again.status == "refused" and again.error["code"] == "wrong_status"
    assert h.gw.state.spent_by_provider["monid"] == Decimal("0.0400")
    assert len([e for e in h.events("cost.booked") if e.provider == "monid"]) == 1


def test_a_second_not_charged_resolve_books_nothing(h):
    h.gw.ports.toolbuy = monid(lost)
    buy(h.gw)
    h.ok("resolve_purchase", key="k1", cost_usd=None)
    again = h.gw.perform("resolve_purchase", {"key": "k1", "cost_usd": "0.9000"})
    assert again.status == "refused" and again.error["code"] == "wrong_status"
    assert not [e for e in h.events("cost.booked") if e.provider == "monid"]
    port = monid()
    h.gw.ports.toolbuy = port
    assert buy(h.gw).status == "done"  # the real re-buy books its own cost
    assert h.gw.state.spent_by_provider["monid"] == Decimal("0.0013")


# ---------------------------------------------------------------- reservations


def test_store_down_before_paying_is_store_unavailable(h, monkeypatch):
    port = monid()
    h.gw.ports.toolbuy = port

    def down():
        raise EventWriteError("disk full")

    monkeypatch.setattr(h.gw.store, "ensure_writable", down)
    r = buy(h.gw)
    assert r.status == "error" and r.error["code"] == "store_unavailable" and port.calls == []
    assert not h.gw._buy_inflight


def test_a_slow_read_crash_releases_its_reservations(h):
    def boom(*args, **kwargs):
        raise KeyError("adapter bug")

    port = monid()
    port.buy = boom
    h.gw.ports.toolbuy = port
    assert buy(h.gw).status == "error" and not h.gw._buy_inflight
    h.ports.scrape.search = boom
    assert h.do("law_search", case_id=h.new_case(), query="delaware").status == "error"
    assert h.gw._law_inflight == 0


def test_a_store_error_after_the_charge_releases_the_key(h):
    h.gw.ports.toolbuy = monid()
    failing_once(h)
    assert buy(h.gw).status == "error" and not h.gw._buy_inflight


def test_a_caller_flag_cannot_free_another_calls_reservation(h):
    inside, release = threading.Event(), threading.Event()

    def slow(request):
        inside.set()
        assert release.wait(5)
        return httpx.Response(200, json=completed())

    h.gw.ports.toolbuy = monid(slow)
    first = threading.Thread(target=lambda: buy(h.gw, key="k"))
    first.start()
    try:
        assert inside.wait(5)
        h.gw.perform("buy_tool", {"tool": "enrichment", "input": {}, "key": "bad key!",
                                  "_buy_reserved": "buy:k"}, actor="mkt")
        assert "buy:k" in h.gw._buy_inflight
    finally:
        release.set()
        first.join(5)


def test_an_in_flight_duplicate_leaves_only_the_first_purchase_after_a_restart(make_harness):
    h = make_harness()
    inside, release = threading.Event(), threading.Event()

    def slow(request):
        inside.set()
        assert release.wait(5)
        return httpx.Response(200, json=completed())

    h.gw.ports.toolbuy = monid(slow)
    first = threading.Thread(target=lambda: buy(h.gw, key="k", query="a"))
    first.start()
    assert inside.wait(5)
    assert buy(h.gw, key="k", query="b").status == "refused"
    release.set()
    first.join(5)
    fresh = monid()
    h.ports.toolbuy = fresh
    gw = h.restart()
    assert "buy:k" in fresh._bought and "buy:k" not in fresh._unsure
    assert buy(gw, key="k", query="a").data["duplicate"] is True and fresh.calls == []
