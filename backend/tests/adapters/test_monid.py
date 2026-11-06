"""MonidToolBuy (ToolBuyPort) against a mocked Monid API: no live calls."""

from __future__ import annotations

import json
from decimal import Decimal

import httpx
import pytest

from app.gateway.adapters.monid import MonidToolBuy, cost_usd
from app.gateway.ports import AuthExpired, FaultPlan, NotConfigured, PortError, RateLimited, Rejected, Unavailable

KEY = "monid_live_test_0123456789"
TOOLS = {"enrichment": {"provider": "exa", "endpoint": "search"}}


def completed(cost=(1300, "MICRO_DOLLAR"), output=None, status="COMPLETED"):
    return {"runId": "run_1", "provider": "exa", "endpoint": "search", "status": status,
            "output": output if output is not None else {"results": [{"title": "Team #7"}]},
            "providerResponse": {"httpStatus": 200},
            "price": {"type": "PER_CALL", "amount": {"value": 0.0013, "currency": "USD"}},
            "billing": {"reportedCost": {"currency": "USD", "value": cost[0], "unit": cost[1]}},
            "createdAt": "2026-11-07T00:00:00Z"}


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def make(handler, **kwargs) -> MonidToolBuy:
    clock = Clock()
    kwargs.setdefault("tools", TOOLS)
    return MonidToolBuy(KEY, transport=httpx.MockTransport(handler), sleep=clock.sleep, clock=clock,
                        today=kwargs.pop("today", lambda: "2026-11-07"), **kwargs)


def test_buy_runs_the_allowlisted_endpoint_and_books_the_reported_cost():
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=completed())

    purchase = make(handler).buy("enrichment", {"query": "delaware startups"}, key="buy-1")
    assert purchase.bought is True and purchase.cost_usd == "0.0013"
    assert purchase.result == {"results": [{"title": "Team #7"}]}
    request = seen[0]
    assert request.headers["authorization"] == f"Bearer {KEY}" and request.url.path == "/v1/run"
    assert json.loads(request.content) == {"provider": "exa", "endpoint": "search",
                                           "input": {"body": {"query": "delaware startups"}}}


def test_composite_input_passes_through():
    seen: list[httpx.Request] = []
    make(lambda r: (seen.append(r), httpx.Response(200, json=completed()))[1]).buy(
        "enrichment", {"queryParams": {"q": "x"}}, key="k")
    assert json.loads(seen[0].content)["input"] == {"queryParams": {"q": "x"}}


@pytest.mark.parametrize("value,unit,expected", [(1300, "MICRO_DOLLAR", "0.0013"), (1, "MICRO_DOLLAR", "0.000001"),
                                                 (25, "CENT", "0.2500"), (2, "DOLLAR", "2.0000"), (0, "CENT", "0.0000")])
def test_cost_is_converted_in_code_to_a_decimal_string(value, unit, expected):
    assert cost_usd({"billing": {"reportedCost": {"currency": "USD", "value": value, "unit": unit}}}) == expected
    assert "E" not in expected and Decimal(expected) >= 0


def test_unbilled_run_costs_zero_and_odd_cost_is_unavailable():
    assert cost_usd({"status": "COMPLETED"}) == "0.0000"
    with pytest.raises(Unavailable):
        cost_usd({"billing": {"reportedCost": {"currency": "USD", "value": "x", "unit": "CENT"}}})


def test_unknown_tool_is_rejected_without_a_call_and_nothing_is_allowlisted_by_default(monkeypatch):
    seen: list[httpx.Request] = []
    with pytest.raises(Rejected, match="not an allowlisted tool"):
        make(lambda r: (seen.append(r), httpx.Response(200, json=completed()))[1]).buy("crypto-trade", {}, key="k")
    assert seen == []
    monkeypatch.delenv("MONID_TOOLS", raising=False)
    with pytest.raises(Rejected):
        MonidToolBuy(KEY, transport=httpx.MockTransport(lambda r: httpx.Response(200, json=completed()))).buy(
            "enrichment", {}, key="k")


def test_same_key_is_bought_once():
    seen: list[httpx.Request] = []
    buy = make(lambda r: (seen.append(r), httpx.Response(200, json=completed()))[1])
    first = buy.buy("enrichment", {"q": 1}, key="buy-1")
    again = buy.buy("enrichment", {"q": 1}, key="buy-1")
    other = buy.buy("enrichment", {"q": 1}, key="buy-2")
    assert first == again and len(seen) == 2 and other.bought


def test_a_timed_out_run_is_never_retried_blindly():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        raise httpx.ReadTimeout("slow", request=request)

    buy = make(handler)
    with pytest.raises(Unavailable, match="may still be charged"):
        buy.buy("enrichment", {}, key="buy-1")
    with pytest.raises(Rejected, match="check GET /v1/runs"):
        buy.buy("enrichment", {}, key="buy-1")
    assert calls["n"] == 1


def test_daily_cap_stops_purchases():
    buy = make(lambda r: httpx.Response(200, json=completed(cost=(60, "CENT"))), daily_cap_usd="1.00")
    buy.buy("enrichment", {}, key="a")
    buy.buy("enrichment", {}, key="b")  # 1.20 booked: over the cap now
    with pytest.raises(Rejected, match="cap"):
        buy.buy("enrichment", {}, key="c")


def test_blocked_run_is_rejected():
    with pytest.raises(Rejected, match="blocked"):
        make(lambda r: httpx.Response(200, json=completed(status="BLOCKED"))).buy("enrichment", {}, key="k")


def test_async_run_is_polled_until_complete():
    states = iter(["RUNNING", "COMPLETED"])

    def handler(request):
        if request.method == "POST":
            return httpx.Response(202, json={"runId": "run_9", "status": "READY"})
        state = next(states)
        return httpx.Response(200, json=completed() | {"runId": "run_9", "status": state})

    assert make(handler).buy("enrichment", {}, key="k").cost_usd == "0.0013"


def test_async_run_that_never_finishes_is_unavailable_and_marked_unsure():
    def handler(request):
        if request.method == "POST":
            return httpx.Response(202, json={"runId": "run_9", "status": "READY"})
        return httpx.Response(200, json={"runId": "run_9", "status": "RUNNING"})

    buy = make(handler, max_wait=6)
    with pytest.raises(Unavailable, match="still running"):
        buy.buy("enrichment", {}, key="k")
    with pytest.raises(Rejected, match="may already be charged"):
        buy.buy("enrichment", {}, key="k")


def test_bad_run_id_is_unavailable():
    with pytest.raises(Unavailable, match="runId"):
        make(lambda r: httpx.Response(202, json={"runId": "../wallet", "status": "READY"})).buy("enrichment", {}, key="k")


def test_429_maps_to_rate_limited():
    buy = make(lambda r: httpx.Response(429, headers={"retry-after": "4"}, json={"code": 429, "message": "Rate limited. Retry after 4s."}))
    with pytest.raises(RateLimited) as err:
        buy.buy("enrichment", {}, key="k")
    assert err.value.retry_after == 4.0


@pytest.mark.parametrize("status,error", [(401, AuthExpired), (403, AuthExpired), (402, Rejected), (400, Rejected),
                                          (404, Rejected), (408, Unavailable), (500, Unavailable), (502, Unavailable),
                                          (504, Unavailable)])
def test_errors_map(status, error):
    with pytest.raises(error):
        make(lambda r: httpx.Response(status, json={"code": status, "message": "m"})).buy("enrichment", {}, key="k")


@pytest.mark.parametrize("handler", [
    lambda r: httpx.Response(200, text="<html>proxy</html>"),
    lambda r: httpx.Response(200, json=["a", "list"]),
    lambda r: httpx.Response(200, json={"status": "WEIRD"}),
    lambda r: (_ for _ in ()).throw(httpx.ConnectError("down", request=r)),
])
def test_malformed_replies_and_http_errors_are_port_errors(handler):
    with pytest.raises(PortError):
        make(handler).buy("enrichment", {}, key="k")


def test_config_errors_and_repr_hides_key(monkeypatch):
    with pytest.raises(NotConfigured):
        MonidToolBuy("")
    monkeypatch.setenv("MONID_TOOLS", "not json")
    with pytest.raises(NotConfigured):
        MonidToolBuy(KEY)
    monkeypatch.setenv("MONID_TOOLS", json.dumps(TOOLS))
    assert set(MonidToolBuy(KEY).tools) == {"enrichment"}
    assert KEY not in repr(MonidToolBuy(KEY))


def contract_factory(tmp_path, faults: FaultPlan) -> MonidToolBuy:
    """How tests/contract/test_ports.py TOOLBUY can list this adapter."""

    def handler(request: httpx.Request) -> httpx.Response:
        try:
            faults.check()
        except RateLimited:
            return httpx.Response(429, headers={"retry-after": "1"}, json={"code": 429, "message": "x"})
        except AuthExpired:
            return httpx.Response(401, json={"code": 401, "message": "x"})
        except PortError:
            return httpx.Response(503, json={"code": 503, "message": "x"})
        return httpx.Response(200, json=completed())

    return make(handler)


def test_contract_style_toolbuy_check(tmp_path):
    port = contract_factory(tmp_path, FaultPlan())
    purchase = port.buy("enrichment", {"domain": "team7.dev"}, key="buy-1")
    assert Decimal(purchase.cost_usd) >= 0
    assert isinstance(purchase.bought, bool)
