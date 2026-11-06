"""Task 10: HubSpot + Monid wiring, the CRM outbox rules, buy_tool (contract v1.7.3) and
the Monid boot replay."""

from __future__ import annotations

import threading
from dataclasses import replace
from decimal import Decimal

import httpx
import pytest

from app.gateway.adapters.monid import MonidToolBuy
from app.gateway.ports import RateLimited

from ..adapters.test_monid import completed

TOOLS = {"enrichment": {"provider": "exa", "endpoint": "search", "max_usd": "0.05",
                        "fields": ["query"]}}


def monid(handler=None, **kw) -> MonidToolBuy:
    calls = []

    def default(request):
        calls.append(request)
        return httpx.Response(200, json=completed())

    port = MonidToolBuy("mk_test", tools=TOOLS, daily_cap_usd=kw.pop("cap", "1.00"),
                        transport=httpx.MockTransport(handler or default),
                        today=lambda: "2026-11-07", **kw)
    port.calls = calls
    return port


# ---------------------------------------------------------------- registry


def test_registry_builds_hubspot_and_monid_only_with_their_keys(tmp_path):
    from app.gateway.registry import AdapterNotReady, build_port
    from app.gateway.settings import Settings

    base = replace(Settings(), data_dir=tmp_path, adapters={"crm": "hubspot", "toolbuy": "monid"})
    for port, env in (("crm", "HUBSPOT_TOKEN"), ("toolbuy", "MONID_API_KEY")):
        with pytest.raises(AdapterNotReady, match=env):
            build_port(port, base)
    full = replace(base, hubspot_token="pat-x", monid_api_key="mk-x")
    assert build_port("crm", full).provider == "hubspot"
    assert build_port("toolbuy", full).provider == "monid"


# ---------------------------------------------------------------- outbox


def test_a_crm_rate_limit_stops_the_batch_and_pauses_the_outbox(h):
    h.gw.OUTBOX_BACKOFF = (0, 0, 0, 0)
    h.to_sent()
    h.to_sent(h.new_case(sender="two@team8.dev"))
    calls = []
    real = h.ports.crm.upsert_contact

    def upsert(contact):
        calls.append(contact.key)
        if len(calls) == 1:
            raise RateLimited("slow down", retry_after=2)
        return real(contact)

    h.ports.crm.upsert_contact = upsert
    h.gw.drain_outbox()
    assert calls == ["case_1"]  # the batch stopped at the first rate limit
    assert "crm.synced" not in h.types()  # a rate limit is not a failure
    h.gw.drain_outbox()
    assert calls == ["case_1"]  # paused for Retry-After
    h.gw._outbox_paused_until = 0
    h.gw.drain_outbox()
    assert sorted(calls[1:]) == ["case_1", "case_2"]
    assert len(h.events("crm.synced")) == 2


def test_a_cases_stages_reach_the_crm_in_order(h):
    h.gw.OUTBOX_BACKOFF = (0, 0, 0, 0)
    case_id = h.to_sent()
    slot = h.ok("get_case", case_id=case_id)["offered_slots"][0]
    h.approve_pending(h.do("book_call", case_id=case_id, start=slot))
    h.ok("book_call", case_id=case_id, start=slot)
    stages = []
    real = h.ports.crm.set_stage

    def set_stage(deal, stage):
        stages.append(stage)
        if stage == "memo_sent" and stages.count("memo_sent") == 1:
            raise RuntimeError("crm down")
        return real(deal, stage)

    h.ports.crm.set_stage = set_stage
    h.gw.drain_outbox()
    assert stages == ["memo_sent"]  # call_booked waits for memo_sent
    h.gw.drain_outbox()
    h.gw.drain_outbox()
    assert stages == ["memo_sent", "memo_sent", "call_booked"]


def test_no_write_drains_the_outbox_inline(h):
    h.to_sent()
    assert "crm.synced" not in h.types() and h.gw.state.crm_pending


# ---------------------------------------------------------------- buy_tool


def test_buy_tool_runs_outside_the_lock_and_books_once(h):
    seen = []
    port = monid()
    real = port.buy

    def buy(tool, data, key):
        got = h.gw._lock.acquire(blocking=False)
        if got:
            h.gw._lock.release()
        seen.append(got)
        return real(tool, data, key)

    port.buy = buy
    h.gw.ports.toolbuy = port
    out = h.gw.perform("buy_tool", {"tool": "enrichment", "input": {"query": "delaware startups"},
                                    "key": "lead-1"}, actor="mkt").as_dict()
    assert out["ok"] and out["data"]["bought"] is True
    assert seen == [True]
    again = h.gw.perform("buy_tool", {"tool": "enrichment", "input": {"query": "delaware startups"},
                                      "key": "lead-1"}, actor="mkt")
    assert again.status == "done" and len(port.calls) == 1  # never paid twice
    bought = h.events("tool.bought")
    assert len(bought) == 1 and bought[0].payload == {"tool": "enrichment", "provider": "monid"}
    assert bought[0].private["key"] == "buy:lead-1" and bought[0].private["fingerprint"]
    cost = [e for e in h.events("cost.booked") if e.provider == "monid"]
    assert len(cost) == 1
    assert h.gw.state.spent_by_provider["monid"] == Decimal(cost[0].cost_usd)  # counted once


@pytest.mark.parametrize("args,reason", [
    ({"tool": "crypto", "input": {"query": "x"}, "key": "k"}, "allowlisted"),
    ({"tool": "enrichment", "input": {"query": "jane@client.dev"}, "key": "k"}, "email"),
    ({"tool": "enrichment", "input": {"query": "call +1 415 555 0100"}, "key": "k"}, "phone"),
    ({"tool": "enrichment", "input": {"query": "Team #3 details"}, "key": "k"}, "case id"),
    ({"tool": "enrichment", "input": "x", "key": "k"}, "object"),
    ({"tool": "enrichment", "input": {"query": "x"}, "key": "bad key!"}, "key"),
])
def test_buy_tool_refusals_never_reach_monid(h, args, reason):
    port = monid()
    h.gw.ports.toolbuy = port
    r = h.gw.perform("buy_tool", args, actor="mkt")
    assert r.status in ("refused", "error") and reason in r.error["message"]
    assert port.calls == [] and "tool.bought" not in h.types()


def test_only_mkt_may_buy(h):
    r = h.gw.perform("buy_tool", {"tool": "enrichment", "input": {"query": "x"}, "key": "k"},
                     actor="cos")
    assert r.error["code"] == "tool_not_allowed"


def test_the_daily_cap_and_spend_survive_a_restart(make_harness, tmp_path):
    # cap 0.051, ceiling 0.05, each run costs 0.0013: a second buy fits only if the first
    # buy's spend was forgotten
    h = make_harness()
    h.gw.ports.toolbuy = monid(cap="0.051")
    assert h.gw.perform("buy_tool", {"tool": "enrichment", "input": {"query": "q0"},
                                     "key": "k0"}, actor="mkt").status == "done"
    restarted_port = monid(cap="0.051")
    h.ports.toolbuy = restarted_port  # the restarted gateway gets a fresh adapter
    gw = h.restart()
    assert restarted_port._spent_today() == Decimal("0.0013")  # replayed from tool.bought
    repeat = gw.perform("buy_tool", {"tool": "enrichment", "input": {"query": "q0"},
                                     "key": "k0"}, actor="mkt")
    assert repeat.status == "done" and restarted_port.calls == []  # remembered: not re-bought
    over = gw.perform("buy_tool", {"tool": "enrichment", "input": {"query": "q9"}, "key": "k9"},
                      actor="mkt")
    assert over.status in ("refused", "error") and "cap" in over.error["message"]


def test_an_unsure_purchase_stays_reserved_across_a_restart(make_harness):
    def lost(request):
        raise httpx.ReadTimeout("gone", request=request)

    h = make_harness()
    h.gw.ports.toolbuy = monid(lost)
    r = h.gw.perform("buy_tool", {"tool": "enrichment", "input": {"query": "q"}, "key": "u1"},
                     actor="mkt")
    assert r.status in ("refused", "error")
    unsure = h.events("tool.bought")
    assert unsure and unsure[0].status == "failed" and unsure[0].private["unsure"] is True
    fresh = monid()
    h.ports.toolbuy = fresh
    gw = h.restart()
    assert "buy:u1" in fresh._unsure
    again = gw.perform("buy_tool", {"tool": "enrichment", "input": {"query": "q"}, "key": "u1"},
                       actor="mkt")
    assert again.status in ("refused", "error") and fresh.calls == []  # never paid twice


def test_buy_tool_is_on_mcp_for_mkt_only():
    from app.mcp import TOOLS
    from app.gateway.perform import TOOL_ACTORS

    assert TOOL_ACTORS["buy_tool"] == ("mkt",) and "buy_tool" in TOOLS
    assert TOOLS["buy_tool"]["inputSchema"]["required"] == ["tool", "input", "key"]
