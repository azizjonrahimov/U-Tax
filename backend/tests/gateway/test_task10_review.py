"""Regression tests for the /review pass over task 10 (buy_tool, the outbox, the digest)."""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest

from app.gateway.ports import RateLimited, Unavailable
from app.gateway.ports.store import EventWriteError

from .test_task10 import monid


def buy(gw, key="k1", query="delaware startups"):
    return gw.perform("buy_tool", {"tool": "enrichment", "input": {"query": query}, "key": key},
                      actor="mkt")


# ---------------------------------------------------------------- buy_tool money safety


def test_nothing_is_paid_while_the_log_cant_be_written(h, monkeypatch):
    # review 1: Monid was charged, the event failed, a restart paid again
    port = monid()
    h.gw.ports.toolbuy = port

    def down():
        raise EventWriteError("disk full")

    monkeypatch.setattr(h.gw.store, "ensure_writable", down)
    assert buy(h.gw).status == "error"
    assert port.calls == []


def test_a_crash_after_the_charge_replays_as_unsure(make_harness):
    # review 1: the window between Monid's charge and our event
    h = make_harness()
    port = monid()
    h.gw.ports.toolbuy = port
    real_emit = h.gw._emit
    calls = {"n": 0}

    def crash(events):
        if any(e.type == "tool.bought" for e in events):
            calls["n"] += 1
            raise EventWriteError("crashed")
        return real_emit(events)

    h.gw._emit = crash
    assert buy(h.gw).status == "error" and len(port.calls) == 1  # charged, not logged
    fresh = monid()
    h.ports.toolbuy = fresh
    gw = h.restart()
    assert "buy:k1" in fresh._unsure  # the intent replays as unsure: reserved, never re-bought
    assert buy(gw).status in ("refused", "error") and fresh.calls == []


def test_a_reused_key_never_pays_again_even_past_the_adapters_memory(h, monkeypatch):
    # review 2: past MAX_REMEMBERED, a reused key paid again with any input
    import app.gateway.adapters.monid as monid_mod

    monkeypatch.setattr(monid_mod, "MAX_REMEMBERED", 2)
    port = monid()
    h.gw.ports.toolbuy = port
    for i in range(3):
        assert buy(h.gw, key=f"k{i}", query=f"q{i}").status == "done"
    assert buy(h.gw, key="k0", query="something else").error["code"] == "bad_input"
    again = buy(h.gw, key="k0", query="q0")
    assert again.status == "done" and again.data["duplicate"] is True
    assert len(port.calls) == 3
    assert h.gw.state.spent_by_provider["monid"] == Decimal("0.0039")


def test_the_ceo_resolves_an_unsure_purchase(make_harness):
    # review 3: nothing could clear an unsure key, so buy_tool could lock up for good
    def lost(request):
        raise httpx.ReadTimeout("gone", request=request)

    h = make_harness()
    h.gw.ports.toolbuy = monid(lost)
    assert buy(h.gw).status in ("refused", "error")
    assert buy(h.gw).status == "refused"  # blocked until resolved
    h.ok("resolve_purchase", key="k1", cost_usd=None)  # Monid's runs show no charge
    h.gw.ports.toolbuy = monid()
    assert buy(h.gw).status == "done"
    charged = make_harness()
    charged.gw.ports.toolbuy = monid(lost)
    buy(charged.gw)
    charged.ok("resolve_purchase", key="k1", cost_usd="0.0400")
    assert charged.gw.state.spent_by_provider["monid"] == Decimal("0.0400")
    assert buy(charged.gw).data["duplicate"] is True  # charged: remembered, not re-bought


def test_an_old_unsure_reservation_never_blocks_a_later_day(make_harness):
    def lost(request):
        raise httpx.ReadTimeout("gone", request=request)

    h = make_harness()
    h.gw.ports.toolbuy = monid(lost)
    buy(h.gw)
    h.clock.advance(days=2)
    fresh = monid()
    h.ports.toolbuy = fresh
    h.restart()
    assert fresh._unsure == {}  # an unsure ceiling counts on its own day only


def test_the_digest_counts_a_purchase_once(make_harness):
    # review 4
    h = make_harness(ceo_email="ceo@u-tax.dev")
    h.gw.ports.toolbuy = monid()
    buy(h.gw)
    period = h.gw._period(0)
    spend = next(v for k, v in period.items() if "spend" in k)
    assert Decimal(str(spend).lstrip("$")) == Decimal("0.0013")


def test_buy_tool_respects_the_firm_budget(make_harness):
    # review 11
    h = make_harness(spend_cap_usd=Decimal("0.001"))
    h.gw.ports.toolbuy = monid()
    assert buy(h.gw).status == "done"  # 0.0013 now spent, over the 0.001 budget
    over = buy(h.gw, key="k2")
    assert over.status == "refused" and "budget" in over.error["message"]


def test_a_repeat_after_a_restart_gets_its_result_back(make_harness):
    # review 12
    h = make_harness()
    h.gw.ports.toolbuy = monid()
    first = buy(h.gw)
    h.ports.toolbuy = monid()
    gw = h.restart()
    again = buy(gw)
    assert again.data["result"] == first.data["result"] and again.data["duplicate"] is True


@pytest.mark.parametrize("query", ["ｊane＠client.dev", "call ４１５ 555 0100",
                                   "EIN 12-3456789", "x" * 3000])
def test_fullwidth_ids_and_huge_inputs_are_refused(h, query):
    # review 14
    port = monid()
    h.gw.ports.toolbuy = port
    assert buy(h.gw, query=query).error["code"] == "bad_input" and port.calls == []


# ---------------------------------------------------------------- outbox


def test_one_job_that_keeps_hitting_a_limit_never_stalls_the_rest(h):
    # review 6
    h.gw.OUTBOX_BACKOFF = (0, 30, 30, 30)
    h.to_sent()
    h.to_sent(h.new_case(sender="two@team8.dev"))
    real = h.ports.crm.upsert_contact
    calls = []

    def upsert(contact):
        calls.append(contact.key)
        if contact.key == "case_1":
            raise RateLimited("per item", retry_after=1)
        return real(contact)

    h.ports.crm.upsert_contact = upsert
    for _ in range(4):
        h.gw._outbox_paused_until = 0
        h.gw.drain_outbox()
    assert "case_2" in calls and len(h.events("crm.synced")) == 1


def test_a_stage_that_keeps_failing_releases_the_later_one(h):
    # review 7
    h.gw.OUTBOX_BACKOFF = (0, 0, 0, 0)
    case_id = h.to_sent()
    slot = h.ok("get_case", case_id=case_id)["offered_slots"][0]
    h.approve_pending(h.do("book_call", case_id=case_id, start=slot))
    h.ok("book_call", case_id=case_id, start=slot)
    real = h.ports.crm.set_stage
    sent = []

    def set_stage(deal, stage):
        if stage == "memo_sent":
            raise ValueError("bad stage map")
        sent.append(stage)
        return real(deal, stage)

    h.ports.crm.set_stage = set_stage
    for _ in range(5):
        h.gw.drain_outbox()
    assert "call_booked" in sent


def test_an_outage_stops_the_batch_at_the_first_timeout(h):
    # review 8
    for i in range(4):
        h.to_sent(h.new_case(sender=f"c{i}@team{i}.dev"))
    calls = []

    def upsert(contact):
        calls.append(contact.key)
        raise Unavailable("timeout")

    h.ports.crm.upsert_contact = upsert
    h.gw.drain_outbox()
    assert len(calls) == 1


def test_claimed_jobs_are_released_whatever_happens(h):
    # review 9
    h.to_sent()
    h.to_sent(h.new_case(sender="two@team8.dev"))

    def boom(contact):
        raise KeyboardInterrupt  # not an Exception

    h.ports.crm.upsert_contact = boom
    with pytest.raises(KeyboardInterrupt):
        h.gw.drain_outbox()
    assert h.gw._outbox_inflight == set()


def test_a_restart_with_an_old_digest_on_the_log_waits_a_period(make_harness):
    # review 13
    h = make_harness(ceo_email="ceo@u-tax.dev", ceo_digest_hours=3)
    h.clock.advance(hours=3, minutes=1)
    assert h.ok("ceo_digest")["sent"] is True
    h.clock.advance(hours=5)  # the last digest is long past...
    gw = h.restart()  # ...but a restart never mails the CEO right away
    assert gw.perform("ceo_digest", {}).data["sent"] is False
    h.clock.advance(hours=3, minutes=1)
    assert gw.perform("ceo_digest", {}).data["sent"] is True


def test_resolve_purchase_is_admin_only_over_http(make_harness):
    from fastapi.testclient import TestClient

    from app.main import create_app

    def lost(request):
        raise httpx.ReadTimeout("gone", request=request)

    h = make_harness(admin_token="admin-x", theater_token="theater-x")
    h.gw.ports.toolbuy = monid(lost)
    buy(h.gw)
    with TestClient(create_app(h.gw, start_background=False)) as client:
        body = {"key": "k1", "charged": False}
        assert client.post("/api/dev/resolve-purchase", json=body).status_code == 401
        ok = client.post("/api/dev/resolve-purchase", json=body,
                         headers={"Authorization": "Bearer admin-x"})
    assert ok.status_code == 200 and ok.json()["charged"] is False
