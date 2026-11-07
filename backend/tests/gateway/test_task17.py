"""Task 17: buy_tool hardening. Every path that may have paid Monid stays remembered, until
the CEO resolves it, across retries, restarts and day changes; a resolution happens once;
a paid result that can't travel as JSON is booked, never lost with its charge."""

from __future__ import annotations

import json
import threading
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

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


# ---------------------------------------------------------------- review 1


def test_an_implausible_resolution_cost_is_refused_before_anything_is_booked(h):
    # a typo ("5000" for "0.05") used to be booked, answer internal_error, and (resolve
    # once) never be correctable; the cost can't exceed what the purchase reserved
    h.gw.ports.toolbuy = monid(lost)
    buy(h.gw)
    for cost in ("5000", "0.5000"):
        r = h.gw.perform("resolve_purchase", {"key": "k1", "cost_usd": cost})
        assert r.status == "refused" and r.error["code"] == "bad_input"
        assert "ceiling" in r.error["message"] or "$1000" in r.error["message"]
    assert not [e for e in h.events("cost.booked") if e.provider == "monid"]
    h.ok("resolve_purchase", key="k1", cost_usd="0.0400")
    assert h.gw.state.spent_by_provider["monid"] == Decimal("0.0400")


def test_resolve_honours_the_charged_flag(make_harness):
    from fastapi.testclient import TestClient

    from app.main import create_app

    h = make_harness(admin_token="admin-" + "a" * 30, theater_token="theater-" + "b" * 30)
    h.gw.ports.toolbuy = monid(lost)
    buy(h.gw)
    auth = {"Authorization": "Bearer admin-" + "a" * 30}
    with TestClient(create_app(h.gw, start_background=False)) as client:
        no_cost = client.post("/api/dev/resolve-purchase", json={"key": "k1", "charged": True},
                              headers=auth)
        assert no_cost.status_code == 400  # charged, but how much? never "not charged"
        mixed = client.post("/api/dev/resolve-purchase",
                            json={"key": "k1", "charged": False, "cost_usd": "0.04"}, headers=auth)
        assert mixed.status_code == 400
        ok = client.post("/api/dev/resolve-purchase",
                         json={"key": "k1", "charged": True, "cost_usd": "0.0400"}, headers=auth)
        assert ok.status_code == 200 and ok.json()["charged"] is True


def test_resolve_refuses_a_key_an_agent_could_not_have_used(h):
    # "#" is the episode separator: "k1#1" reached k1's second-episode records
    h.gw.ports.toolbuy = monid(lost)
    buy(h.gw)
    r = h.gw.perform("resolve_purchase", {"key": "k1#1", "cost_usd": "0.0400"})
    assert r.status == "refused" and r.error["code"] == "bad_input"
    assert "buy:k1" in h.gw.ports.toolbuy._unsure
    assert h.ok("resolve_purchase", key="k1", cost_usd=None)["charged"] is False


def test_a_leftover_is_recorded_under_its_own_tool(make_harness):
    h = make_harness()
    crash_left_intent(h)
    h.ports.toolbuy = monid()
    gw = h.restart()
    gw.perform("buy_tool", {"tool": "zzz-not-a-tool", "input": {"query": "q"}, "key": "c1"},
               actor="mkt")
    assert [e.payload["tool"] for e in unsure_events(h, "c1")] == ["enrichment"]


def test_resolve_clears_the_leftover_intent_first(h):
    # a crash between the resolution and the clear used to re-block the key; and an intent
    # left beside an unsure record (crash after the record) blocked it for good
    h.gw.ports.toolbuy = monid(lost)
    buy(h.gw, key="c1")
    crash_left_intent(h)
    failing_once(h)  # the resolution's write fails once
    assert h.gw.perform("resolve_purchase", {"key": "c1", "cost_usd": None}).status == "error"
    assert h.gw._buy_intents.get("buy:c1") is None
    assert buy(h.gw, key="c1").status == "refused"  # the log still holds it as unsure
    h.ok("resolve_purchase", key="c1", cost_usd=None)
    h.gw.ports.toolbuy = monid()
    assert buy(h.gw, key="c1").status == "done"


def test_a_key_state_refusal_keeps_the_intent(h):
    port = monid()
    port._inflight["buy:k1"] = Decimal("0.05")  # the adapter holds it; the gateway's log doesn't
    h.gw.ports.toolbuy = port
    assert buy(h.gw).status == "refused"
    assert h.gw._buy_intents.get("buy:k1") is not None


def test_restart_after_a_not_charged_resolution_holds_the_second_unsure(make_harness):
    h = make_harness()
    h.gw.ports.toolbuy = monid(lost)
    buy(h.gw)
    h.ok("resolve_purchase", key="k1", cost_usd=None)
    h.gw.ports.toolbuy = monid(lost)
    buy(h.gw)
    fresh = monid()
    h.ports.toolbuy = fresh
    gw = h.restart()
    assert "buy:k1" in fresh._unsure  # its ceiling still counts against the daily cap
    assert buy(gw).status == "refused" and fresh.calls == []


def test_a_not_charged_key_is_free_after_a_restart_and_a_day_change(make_harness):
    h = make_harness()
    h.gw.ports.toolbuy = monid(lost)
    buy(h.gw)
    h.ok("resolve_purchase", key="k1", cost_usd=None)
    h.clock.advance(days=1)
    fresh = monid()
    h.ports.toolbuy = fresh
    gw = h.restart()
    assert "buy:k1" not in fresh._unsure
    assert buy(gw).status == "done" and len(fresh.calls) == 1


def test_a_charged_resolution_is_a_purchase_after_a_restart(make_harness):
    h = make_harness()
    h.gw.ports.toolbuy = monid(lost)
    buy(h.gw)
    h.ok("resolve_purchase", key="k1", cost_usd="0.0400")
    fresh = monid()
    h.ports.toolbuy = fresh
    gw = h.restart()
    assert buy(gw).data["duplicate"] is True and fresh.calls == []
    assert gw.state.spent_by_provider["monid"] == Decimal("0.0400")


def test_a_leftover_without_a_record_is_resolvable(h):
    # the CEO used to get not_found until an agent happened to retry that key
    h.gw.ports.toolbuy = monid()
    crash_left_intent(h)
    assert h.ok("resolve_purchase", key="c1", cost_usd=None)["charged"] is False
    assert len(unsure_events(h, "c1")) == 1 and h.gw._buy_intents.get("buy:c1") is None
    assert buy(h.gw, key="c1").status == "done"


def test_intent_reads_and_writes_never_collide(tmp_path):
    # Windows refuses os.replace onto a file another thread is reading
    from app.gateway.adapters.local import _KeyedJson

    store = _KeyedJson(Path(tmp_path) / "intents.json")
    errors: list[BaseException] = []
    stop = threading.Event()

    def read():
        while not stop.is_set():
            try:
                store.get("k")
            except BaseException as exc:  # noqa: BLE001 - collected for the assert
                errors.append(exc)

    readers = [threading.Thread(target=read) for _ in range(3)]
    for t in readers:
        t.start()
    try:
        for i in range(300):
            store.put("k", {"n": i})
    except BaseException as exc:  # noqa: BLE001
        errors.append(exc)
    finally:
        stop.set()
        for t in readers:
            t.join(5)
    assert errors == [] and store.get("k") == {"n": 299}


def raw_monid(text):
    """A Monid answering with this exact JSON text (escapes as Monid would send them)."""
    body = json.dumps(completed(output={"results": "@OUT@"})).replace('"@OUT@"', text)
    return monid(lambda request: httpx.Response(200, content=body.encode(),
                                                headers={"content-type": "application/json"}))


def test_a_result_that_cannot_be_written_is_dropped_and_the_cost_booked(h):
    # a lone surrogate failed the log write (UnicodeEncodeError) after the charge, on every
    # retry; a NUL is refused by Postgres jsonb
    for n, text in enumerate(('[{"t": "\\ud800"}]', '[{"t": "a\\u0000b"}]')):
        h.gw.ports.toolbuy = raw_monid(text)
        r = buy(h.gw, key=f"s{n}")
        assert r.status == "done" and r.data["bought"] is True and r.data["result"] is None
        json.dumps(r.as_dict(), ensure_ascii=False).encode("utf-8")
    assert len([e for e in h.events("cost.booked") if e.provider == "monid"]) == 2


def test_a_crash_left_intent_is_on_the_log_at_boot(make_harness):
    # the CEO could only see it if an agent happened to retry that key
    h = make_harness()
    crash_left_intent(h)
    h.ports.toolbuy = monid()
    gw = h.restart()
    assert len(unsure_events(h, "c1")) == 1 and gw._buy_intents.get("buy:c1") is None
    assert gw.perform("resolve_purchase", {"key": "c1", "cost_usd": "0.0400"}).status == "done"
    assert gw.state.spent_by_provider["monid"] == Decimal("0.0400")
    h.restart()
    assert len(unsure_events(h, "c1")) == 1  # once


def test_a_repeat_is_answered_even_when_the_budget_is_spent(make_harness):
    h = make_harness(spend_cap_usd=Decimal("0.0013"))
    port = monid()
    h.gw.ports.toolbuy = port
    assert buy(h.gw).status == "done"  # the budget is now spent
    again = buy(h.gw)
    assert again.status == "done" and again.data["duplicate"] is True and len(port.calls) == 1
    other = buy(h.gw, key="k2")
    assert other.status == "refused" and "budget" in other.error["message"]


# ---------------------------------------------------------------- review 2


def test_a_true_cost_over_the_ceiling_books_with_an_explicit_confirmation(h):
    # max_usd is our estimate, not Monid's limit: the real charge must stay bookable
    h.gw.ports.toolbuy = monid(lost)
    buy(h.gw)
    r = h.gw.perform("resolve_purchase", {"key": "k1", "cost_usd": "0.5000"})
    assert r.status == "refused" and "over_ceiling" in r.error["message"]
    for bad in ({"over_ceiling": "yes"}, {"charged": "false"}):
        assert h.gw.perform("resolve_purchase", {"key": "k1", "cost_usd": "0.5000", **bad}
                            ).error["code"] == "bad_input"
    never = h.gw.perform("resolve_purchase", {"key": "k1", "cost_usd": "5000",
                                              "over_ceiling": True})
    assert never.status == "refused" and "$1000" in never.error["message"]
    h.ok("resolve_purchase", key="k1", cost_usd="0.5000", over_ceiling=True)
    assert h.gw.state.spent_by_provider["monid"] == Decimal("0.5000")


def test_a_prefixed_key_gets_a_hint(h):
    h.gw.ports.toolbuy = monid(lost)
    buy(h.gw)
    r = h.gw.perform("resolve_purchase", {"key": "buy:k1", "cost_usd": None})
    assert r.error["code"] == "not_found" and "without the buy: prefix" in r.error["message"]


def test_a_literal_backslash_u0000_is_not_a_nul(h):
    h.gw.ports.toolbuy = raw_monid(json.dumps([{"path": "C:\\u0000dir"}]))
    r = buy(h.gw)
    assert r.data["result"] == {"results": [{"path": "C:\\u0000dir"}]}


def test_settled_intents_leave_the_file(h):
    h.gw.ports.toolbuy = monid()
    buy(h.gw)
    assert "buy:k1" not in h.gw._buy_intents.load()


def test_one_bad_intent_never_stops_the_boot(make_harness):
    h = make_harness()
    h.gw._buy_intents.put("buy:junk", "not an intent")
    crash_left_intent(h)
    h.ports.toolbuy = monid()
    gw = h.restart()  # boots
    assert len(unsure_events(h, "c1")) == 1 and gw._buy_intents.get("buy:junk") == "not an intent"


def test_a_leftover_the_boot_could_not_record_is_recorded_by_the_first_retry(make_harness,
                                                                             monkeypatch):
    from app.gateway.perform import Gateway

    h = make_harness()
    crash_left_intent(h)
    real = Gateway._emit

    def down(self, events):
        if any(e.type == "tool.bought" for e in events):
            raise EventWriteError("store hiccup at boot")
        return real(self, events)

    monkeypatch.setattr(Gateway, "_emit", down)
    fresh = monid()
    h.ports.toolbuy = fresh
    gw = h.restart()
    monkeypatch.setattr(Gateway, "_emit", real)
    assert gw._buy_intents.get("buy:c1") is not None and not unsure_events(h, "c1")
    r = buy(gw, key="c1")
    assert r.status == "refused" and fresh.calls == [] and len(unsure_events(h, "c1")) == 1


def test_a_second_episode_leftover_gets_its_own_record(h):
    h.gw.ports.toolbuy = monid(lost)
    buy(h.gw)
    h.ok("resolve_purchase", key="k1", cost_usd=None)
    h.gw.ports.toolbuy = monid(lost)
    failing_once(h)
    assert buy(h.gw).error["code"] == "store_unavailable"
    assert buy(h.gw).status == "refused"
    assert len(unsure_events(h, "k1")) == 2


def test_a_not_charged_key_is_free_after_a_same_day_restart(make_harness):
    h = make_harness()
    h.gw.ports.toolbuy = monid(lost)
    buy(h.gw)
    h.ok("resolve_purchase", key="k1", cost_usd=None)
    fresh = monid()
    h.ports.toolbuy = fresh
    gw = h.restart()
    assert "buy:k1" not in fresh._unsure
    assert buy(gw).status == "done" and len(fresh.calls) == 1


def test_a_recovered_charge_books_under_its_own_tool(h):
    h.gw.ports.toolbuy = monid()
    failing_once(h)
    buy(h.gw)  # charged, its events failed: the intent stays, the adapter remembers
    h.gw.perform("buy_tool", {"tool": "zzz-not-a-tool", "input": {"query": "delaware startups"},
                              "key": "k1"}, actor="mkt")
    booked = [e for e in h.events("tool.bought") if e.status == "ok"]
    assert [e.payload["tool"] for e in booked] == ["enrichment"]


def test_a_booked_purchase_with_a_stale_intent_records_nothing_at_boot(make_harness):
    h = make_harness()
    h.gw.ports.toolbuy = monid()
    buy(h.gw, key="c1")
    crash_left_intent(h)  # the clear after the events was lost
    h.ports.toolbuy = monid()
    h.restart()
    assert not unsure_events(h, "c1")


def test_a_leftover_keeps_its_ceiling(make_harness):
    h = make_harness()
    crash_left_intent(h)  # ceiling 0.05
    h.ports.toolbuy = monid()
    gw = h.restart()
    r = gw.perform("resolve_purchase", {"key": "c1", "cost_usd": "0.5000"})
    assert r.status == "refused" and "ceiling" in r.error["message"]


def test_a_too_wide_result_is_dropped_and_booked(h):
    h.gw.BUY_RESULT_VALUES = 3
    h.gw.ports.toolbuy = monid()
    r = buy(h.gw)
    assert r.status == "done" and r.data["result"] is None
    assert len([e for e in h.events("cost.booked") if e.provider == "monid"]) == 1


def test_a_leftover_is_answered_when_the_budget_is_spent(make_harness):
    h = make_harness(spend_cap_usd=Decimal("0.0013"))
    h.gw.ports.toolbuy = monid()
    assert buy(h.gw, key="k0").status == "done"
    crash_left_intent(h)
    r = buy(h.gw, key="c1")
    assert r.status == "refused" and "may already be charged" in r.error["message"]
    assert len(unsure_events(h, "c1")) == 1


def test_a_remembered_charge_is_booked_when_the_budget_is_spent(make_harness):
    h = make_harness(spend_cap_usd=Decimal("0.0013"))
    port = monid()
    h.gw.ports.toolbuy = port
    failing_once(h)
    assert buy(h.gw, key="k1").status == "error"
    assert buy(h.gw, key="k2", query="x").status == "done"
    assert "budget" in buy(h.gw, key="k3", query="y").error["message"]
    r = buy(h.gw, key="k1")
    assert r.status == "done" and r.data["bought"] is True and len(port.calls) == 2
    assert h.gw.state.spent_by_provider["monid"] == Decimal("0.0026")


def test_a_second_episode_charged_after_a_day_change_and_restart(make_harness):
    h = make_harness()
    h.gw.ports.toolbuy = monid(lost)
    buy(h.gw)
    h.ok("resolve_purchase", key="k1", cost_usd=None)
    h.gw.ports.toolbuy = monid(lost)
    buy(h.gw)
    assert buy(h.gw, query="other").status == "refused"
    h.clock.advance(days=1)
    fresh = monid()
    h.ports.toolbuy = fresh
    gw = h.restart()
    assert "buy:k1" not in fresh._unsure
    assert buy(gw).status == "refused" and fresh.calls == []
    assert gw.perform("resolve_purchase", {"key": "k1", "cost_usd": "0.0400"}).status == "done"
    assert buy(gw).data["duplicate"] is True and fresh.calls == []
    assert gw.perform("resolve_purchase", {"key": "k1", "cost_usd": None}).status == "refused"
    assert len([e for e in h.events("cost.booked") if e.provider == "monid"]) == 1


def test_a_boot_leftover_after_a_not_charged_episode(make_harness):
    h = make_harness()
    h.gw.ports.toolbuy = monid(lost)
    buy(h.gw, key="c1")
    h.ok("resolve_purchase", key="c1", cost_usd=None)
    crash_left_intent(h)  # the second episode crashed
    h.ports.toolbuy = monid()
    gw = h.restart()
    assert len(unsure_events(h, "c1")) == 2
    assert gw.perform("resolve_purchase", {"key": "c1", "cost_usd": None}).status == "done"
    assert buy(gw, key="c1").status == "done"


def test_the_admin_route_needs_an_explicit_charged(make_harness):
    # "not charged" frees the key for a second paid buy: never from a blank field
    from fastapi.testclient import TestClient

    from app.main import create_app

    h = make_harness(admin_token="admin-" + "a" * 30, theater_token="theater-" + "b" * 30)
    h.gw.ports.toolbuy = monid(lost)
    buy(h.gw)
    auth = {"Authorization": "Bearer admin-" + "a" * 30}
    with TestClient(create_app(h.gw, start_background=False)) as client:
        for body in ({"key": "k1"}, {"key": "k1", "cost_usd": ""}, {"key": "k1", "cost_usd": None}):
            assert client.post("/api/dev/resolve-purchase", json=body, headers=auth
                               ).status_code == 400
    assert unsure_events(h, "k1") and buy(h.gw).status == "refused"  # still held


def test_the_local_toolbuy_never_turns_a_leftover_into_an_unsure(make_harness):
    # log-only, never charges: a leftover is just run again (the demo path)
    from app.gateway.adapters.local import LocalToolBuy

    h = make_harness()
    h.gw.ports.toolbuy = LocalToolBuy()
    crash_left_intent(h)
    r = buy(h.gw, key="c1")
    assert r.status == "done" and r.data["bought"] is False and not unsure_events(h, "c1")
    crash_left_intent(h, key="c2")
    h.ports.toolbuy = LocalToolBuy()
    gw = h.restart()
    assert not unsure_events(h, "c2") and gw._buy_intents.get("buy:c2") is None
