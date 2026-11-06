"""MonidToolBuy (ToolBuyPort) against a mocked Monid API: no live calls."""

from __future__ import annotations

import json
import threading
from decimal import Decimal

import httpx
import pytest

from app.gateway.adapters.monid import MonidToolBuy, Unreadable, cost_usd
from app.gateway.ports import AuthExpired, FaultPlan, NotConfigured, PortError, RateLimited, Rejected, Unavailable

KEY = "monid_live_test_0123456789"
TOOLS = {"enrichment": {"provider": "exa", "endpoint": "search", "max_usd": "0.05"}}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ("MONID_TOOLS", "MONID_DAILY_CAP_USD"):
        monkeypatch.delenv(name, raising=False)


def completed(cost=(1300, "MICRO_DOLLAR"), output=None, status="COMPLETED"):
    run = {"runId": "run_1", "provider": "exa", "endpoint": "search", "status": status,
           "output": output if output is not None else {"results": [{"title": "Team #7"}]},
           "providerResponse": {"httpStatus": 200},
           "price": {"type": "PER_CALL", "amount": {"value": 0.0013, "currency": "USD"}},
           "createdAt": "2026-11-07T00:00:00Z"}
    if cost is not None:
        run["billing"] = {"reportedCost": {"currency": "USD", "value": cost[0], "unit": cost[1]}}
    return run


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def make(handler, today=lambda: "2026-11-07", **kwargs) -> MonidToolBuy:
    clock = Clock()
    kwargs.setdefault("tools", TOOLS)
    return MonidToolBuy(KEY, transport=httpx.MockTransport(handler), sleep=clock.sleep, clock=clock,
                        today=today, **kwargs)


def counting(response_for):
    calls: list[httpx.Request] = []

    def handler(request):
        calls.append(request)
        return response_for(request, len(calls))

    return calls, handler


# ------------------------------------------------------------------ the happy path


def test_buy_runs_the_allowlisted_endpoint_and_books_the_reported_cost():
    calls, handler = counting(lambda r, n: httpx.Response(200, json=completed()))
    purchase = make(handler).buy("enrichment", {"query": "delaware startups"}, key="buy-1")
    assert purchase.bought is True and purchase.cost_usd == "0.0013"
    assert purchase.result == {"results": [{"title": "Team #7"}]}
    request = calls[0]
    assert request.headers["authorization"] == f"Bearer {KEY}" and request.url.path == "/v1/run"
    assert json.loads(request.content) == {"provider": "exa", "endpoint": "search",
                                           "input": {"body": {"query": "delaware startups"}}}


@pytest.mark.parametrize("value,unit,expected", [
    (1300, "MICRO_DOLLAR", "0.0013"), (1, "MICRO_DOLLAR", "0.000001"), ("1300", "MICRO_DOLLAR", "0.0013"),
    (25, "CENT", "0.2500"), (2, "DOLLAR", "2.0000"), (1.5, "DOLLAR", "1.5000"), (0, "CENT", "0.0000")])
def test_cost_is_converted_exactly_in_code(value, unit, expected):
    assert cost_usd({"billing": {"reportedCost": {"currency": "USD", "value": value, "unit": unit}}}) == expected
    assert "E" not in expected and Decimal(expected) >= 0


@pytest.mark.parametrize("reported", [{"value": "x", "unit": "CENT"}, {"value": 5, "unit": "SATOSHI"},
                                      {"value": -1, "unit": "CENT"}, {"value": True, "unit": "DOLLAR"},
                                      {"value": "NaN", "unit": "CENT"}, {"unit": "CENT"}])
def test_unreadable_cost_is_flagged(reported):
    with pytest.raises(Unreadable):
        cost_usd({"billing": {"reportedCost": reported}})
    assert cost_usd({"status": "COMPLETED"}) is None


# ------------------------------------------------------------------ what may be bought


def test_unknown_tool_is_rejected_without_a_call_and_nothing_is_allowlisted_by_default():
    calls, handler = counting(lambda r, n: httpx.Response(200, json=completed()))
    with pytest.raises(Rejected, match="not an allowlisted tool"):
        make(handler).buy("crypto-trade", {}, key="k")
    with pytest.raises(Rejected):
        MonidToolBuy(KEY, transport=httpx.MockTransport(handler)).buy("enrichment", {}, key="k")
    assert calls == []


def test_input_fields_and_emails_are_checked_before_a_call():
    calls, handler = counting(lambda r, n: httpx.Response(200, json=completed()))
    buy = make(handler, tools={"enrichment": TOOLS["enrichment"] | {"fields": ["query"]}})
    with pytest.raises(Rejected, match="takes only"):
        buy.buy("enrichment", {"query": "x", "pathParams": "../admin"}, key="a")
    with pytest.raises(Rejected, match="email"):
        buy.buy("enrichment", {"query": "founder jane@acme.com"}, key="b")
    with pytest.raises(Rejected, match="email"):
        make(handler).buy("enrichment", {"people": [{"contact": "jane@acme.com"}]}, key="c")
    assert calls == []


# ------------------------------------------------------------------ never pay twice


def test_same_key_is_bought_once_and_cannot_be_reused_for_another_purchase():
    calls, handler = counting(lambda r, n: httpx.Response(200, json=completed()))
    buy = make(handler)
    first = buy.buy("enrichment", {"q": 1}, key="buy-1")
    assert buy.buy("enrichment", {"q": 1}, key="buy-1") == first
    with pytest.raises(Rejected, match="different purchase"):
        buy.buy("enrichment", {"q": 2}, key="buy-1")
    assert buy.buy("enrichment", {"q": 1}, key="buy-2").bought and len(calls) == 2


UNSURE = [
    ("read timeout", lambda r, n: (_ for _ in ()).throw(httpx.ReadTimeout("slow", request=r))),
    ("reset after send", lambda r, n: (_ for _ in ()).throw(httpx.ReadError("reset", request=r))),
    ("protocol error", lambda r, n: (_ for _ in ()).throw(httpx.RemoteProtocolError("bad", request=r))),
    ("500", lambda r, n: httpx.Response(500, json={"code": 500, "message": "m"})),
    ("502 provider", lambda r, n: httpx.Response(502, json={"code": 502, "message": "m"})),
    ("504", lambda r, n: httpx.Response(504, json={"code": 504, "message": "m"})),
    ("200 not json", lambda r, n: httpx.Response(200, text="<html>proxy</html>")),
    ("200 wrapped", lambda r, n: httpx.Response(200, json={"run": completed()})),
    ("200 FAILED", lambda r, n: httpx.Response(200, json=completed(status="FAILED"))),
    ("202 bad runId", lambda r, n: httpx.Response(202, json={"runId": "../wallet", "status": "READY"})),
]


@pytest.mark.parametrize("name,respond", UNSURE, ids=[u[0] for u in UNSURE])
def test_a_maybe_charged_run_is_never_bought_again(name, respond):
    calls, handler = counting(respond)
    buy = make(handler)
    with pytest.raises(Unavailable, match="may"):
        buy.buy("enrichment", {}, key="buy-1")
    with pytest.raises(Rejected, match="may already be charged"):
        buy.buy("enrichment", {}, key="buy-1")
    assert len(calls) == 1
    buy.resolve("buy-1", "0.0013")  # the owner checked GET /v1/runs
    assert buy._spent[1] == Decimal("0.0013")


@pytest.mark.parametrize("respond", [
    lambda r, n: (_ for _ in ()).throw(httpx.ConnectError("down", request=r)) if n == 1 else httpx.Response(200, json=completed()),
    lambda r, n: httpx.Response(429, headers={"retry-after": "4"}, json={"code": 429, "message": "Rate limited."})
    if n == 1 else httpx.Response(200, json=completed()),
    lambda r, n: httpx.Response(408, json=completed(status="TIMED_OUT")) if n == 1 else httpx.Response(200, json=completed()),
])
def test_a_failure_that_proves_no_charge_can_be_retried(respond):
    calls, handler = counting(respond)
    buy = make(handler)
    with pytest.raises(PortError) as err:
        buy.buy("enrichment", {}, key="k")
    assert err.value.retryable
    assert buy.buy("enrichment", {}, key="k").bought and len(calls) == 2


def test_completed_without_readable_billing_books_the_ceiling():
    for cost in (None, (5, "SATOSHI")):
        calls, handler = counting(lambda r, n: httpx.Response(200, json=completed(cost=cost)))
        buy = make(handler)
        assert buy.buy("enrichment", {}, key="k").cost_usd == "0.0500"
        assert buy.buy("enrichment", {}, key="k").cost_usd == "0.0500" and len(calls) == 1


def test_concurrent_buys_of_one_key_send_one_run():
    gate = threading.Event()
    calls, inner = counting(lambda r, n: httpx.Response(200, json=completed()))

    def handler(request):
        gate.wait(2)
        return inner(request)

    buy = make(handler)
    results: list[object] = []

    def worker():
        try:
            results.append(buy.buy("enrichment", {}, key="k"))
        except PortError as err:
            results.append(err)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    gate.set()
    for t in threads:
        t.join()
    assert len(calls) == 1
    assert sum(isinstance(r, Rejected) for r in results) == 3


# ------------------------------------------------------------------ the daily cap


def test_daily_cap_reserves_each_tools_ceiling():
    _, handler = counting(lambda r, n: httpx.Response(200, json=completed(cost=(40, "CENT"))))
    tools = {"enrichment": TOOLS["enrichment"] | {"max_usd": "0.50"}}
    buy = make(handler, tools=tools, daily_cap_usd="1.00")
    buy.buy("enrichment", {}, key="a")  # 0.40 booked; 0.40 + 0.50 fits
    buy.buy("enrichment", {}, key="b")  # 0.80 booked
    with pytest.raises(Rejected, match="cap"):  # 0.80 + a 0.50 ceiling would not
        buy.buy("enrichment", {}, key="c")
    assert buy._spent[1] == Decimal("0.80")


def test_daily_cap_resets_on_a_new_day():
    day = {"d": "2026-11-07"}
    _, handler = counting(lambda r, n: httpx.Response(200, json=completed(cost=(5, "CENT"))))
    buy = make(handler, today=lambda: day["d"], daily_cap_usd="0.06")
    buy.buy("enrichment", {}, key="a")
    with pytest.raises(Rejected, match="cap"):
        buy.buy("enrichment", {}, key="b")
    day["d"] = "2026-11-08"
    assert buy.buy("enrichment", {}, key="b").bought


# ------------------------------------------------------------------ async runs


def test_async_run_is_polled_until_complete():
    states = iter(["RUNNING", "COMPLETED"])

    def handler(request):
        if request.method == "POST":
            return httpx.Response(202, json={"runId": "run_9", "status": "READY"})
        return httpx.Response(200, json=completed() | {"runId": "run_9", "status": next(states)})

    assert make(handler).buy("enrichment", {}, key="k").cost_usd == "0.0013"


@pytest.mark.parametrize("poll", [
    lambda r: httpx.Response(200, json={"runId": "run_9", "status": "RUNNING"}),
    lambda r: (_ for _ in ()).throw(httpx.ReadTimeout("slow", request=r)),
    lambda r: httpx.Response(500, json={"code": 500, "message": "m"}),
    lambda r: httpx.Response(429, json={"code": 429, "message": "m"}),
    lambda r: httpx.Response(200, json={"runId": "run_9", "status": "STOPPED"}),
])
def test_a_poll_that_goes_wrong_leaves_the_key_unsure(poll):
    posts = []

    def handler(request):
        if request.method == "POST":
            posts.append(request)
            return httpx.Response(202, json={"runId": "run_9", "status": "READY"})
        return poll(request)

    buy = make(handler, max_wait=6)
    with pytest.raises(Unavailable, match="may"):
        buy.buy("enrichment", {}, key="k")
    with pytest.raises(Rejected, match="may already be charged"):
        buy.buy("enrichment", {}, key="k")
    assert len(posts) == 1


# ------------------------------------------------------------------ errors and config


@pytest.mark.parametrize("status,error", [(401, NotConfigured), (403, NotConfigured), (402, Rejected),
                                          (400, Rejected), (404, Rejected), (408, Unavailable)])
def test_errors_that_prove_no_charge_map(status, error):
    with pytest.raises(error):
        make(lambda r: httpx.Response(status, json={"code": status, "message": "m"})).buy("enrichment", {}, key="k")


def test_429_maps_to_rate_limited():
    buy = make(lambda r: httpx.Response(429, headers={"retry-after": "4"}, json={"code": 429, "message": "Rate limited."}))
    with pytest.raises(RateLimited) as err:
        buy.buy("enrichment", {}, key="k")
    assert err.value.retry_after == 4.0


def test_blocked_run_is_rejected_and_not_unsure():
    calls, handler = counting(lambda r, n: httpx.Response(200, json=completed(status="BLOCKED")))
    buy = make(handler)
    for _ in range(2):
        with pytest.raises(Rejected, match="blocked"):
            buy.buy("enrichment", {}, key="k")
    assert len(calls) == 2


@pytest.mark.parametrize("tools,cap", [
    ("not json", None), ("[]", None), ('"x"', None),
    ('{"e": {"provider": "exa", "endpoint": "search"}}', None),
    ('{"e": {"provider": "exa", "endpoint": "search", "max_usd": "-1"}}', None),
    ('{"e": {"provider": "exa", "max_usd": "0.1"}}', None),
    ('{"e": {"provider": "exa", "endpoint": "s", "max_usd": "0.1", "fields": "q"}}', None),
    ("{}", "$1"), ("{}", "Infinity"), ("{}", "NaN"), ("{}", "-1"),
])
def test_bad_config_is_not_configured(monkeypatch, tools, cap):
    monkeypatch.setenv("MONID_TOOLS", tools)
    if cap is not None:
        monkeypatch.setenv("MONID_DAILY_CAP_USD", cap)
    with pytest.raises(NotConfigured):
        MonidToolBuy(KEY)


def test_config_from_env_and_repr_hides_key(monkeypatch):
    with pytest.raises(NotConfigured):
        MonidToolBuy("")
    monkeypatch.setenv("MONID_TOOLS", json.dumps(TOOLS))
    buy = MonidToolBuy(KEY)
    assert set(buy.tools) == {"enrichment"} and buy.daily_cap == Decimal("1.00")
    assert KEY not in repr(buy)


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
    faults = FaultPlan()
    port = contract_factory(tmp_path, faults)
    purchase = port.buy("enrichment", {"domain": "team7.dev"}, key="buy-1")
    assert Decimal(purchase.cost_usd) >= 0 and isinstance(purchase.bought, bool)
    faults.add(RateLimited())
    with pytest.raises(RateLimited):
        port.buy("enrichment", {"domain": "team8.dev"}, key="buy-2")


# ------------------------------------------------------------------ fix-pass regressions


def test_unsure_runs_keep_their_ceiling_reserved_against_the_cap():
    calls, handler = counting(lambda r, n: (_ for _ in ()).throw(httpx.ReadTimeout("slow", request=r)))
    tools = {"enrichment": TOOLS["enrichment"] | {"max_usd": "0.40"}}
    buy = make(handler, tools=tools, daily_cap_usd="1.00")
    for key in ("a", "b"):
        with pytest.raises(Unavailable):
            buy.buy("enrichment", {}, key=key)
    with pytest.raises(Rejected, match="cap"):  # 0.80 may be charged; another 0.40 would not fit
        buy.buy("enrichment", {}, key="c")
    assert len(calls) == 2
    buy.resolve("a", None)  # checked: never charged, so its ceiling is free again
    with pytest.raises(Unavailable):
        buy.buy("enrichment", {}, key="c")
    assert len(calls) == 3


def test_resolving_a_charged_run_remembers_it():
    calls, handler = counting(lambda r, n: (_ for _ in ()).throw(httpx.ReadTimeout("slow", request=r)))
    buy = make(handler)
    with pytest.raises(Unavailable):
        buy.buy("enrichment", {"q": 1}, key="k")
    with pytest.raises(NotConfigured):
        buy.resolve("k", "$0.40")  # a bad cost leaves the key unsure
    with pytest.raises(Rejected, match="may already be charged"):
        buy.buy("enrichment", {"q": 1}, key="k")
    buy.resolve("k", "0.0400", {"results": []})
    purchase = buy.buy("enrichment", {"q": 1}, key="k")
    assert purchase.cost_usd == "0.0400" and purchase.result == {"results": []} and len(calls) == 1


@pytest.mark.parametrize("respond", [
    lambda r, n: httpx.Response(200, json=completed(cost=(10**40, "DOLLAR"))),
    lambda r, n: httpx.Response(200, content=b"[" * 100000 + b"]" * 100000),
])
def test_odd_replies_after_sending_never_pay_twice(respond):
    """A huge cost books the ceiling; an undecodable body leaves the key unsure. Either way the
    second attempt sends nothing."""
    calls, handler = counting(respond)
    buy = make(handler)
    for _ in range(2):
        try:
            buy.buy("enrichment", {}, key="k")
        except PortError:
            pass
    assert len(calls) == 1


@pytest.mark.parametrize("value", [{"q": {1, 2}}, {"q": float("nan")}, {"q": ("bob@client.com",)}])
def test_input_must_be_plain_json_without_emails(value):
    calls, handler = counting(lambda r, n: httpx.Response(200, json=completed()))
    with pytest.raises(Rejected):
        make(handler).buy("enrichment", value, key="k")
    assert calls == []


def test_a_zero_ceiling_is_not_configured():
    with pytest.raises(NotConfigured, match="above 0"):
        MonidToolBuy(KEY, tools={"e": {"provider": "exa", "endpoint": "s", "max_usd": "0"}})


@pytest.mark.parametrize("status", [409, 422])
def test_validation_answers_do_not_leave_the_key_unsure(status):
    calls, handler = counting(lambda r, n: httpx.Response(status, json={"code": status, "message": "m"}))
    buy = make(handler)
    for _ in range(2):
        with pytest.raises(Rejected):
            buy.buy("enrichment", {}, key="k")
    assert len(calls) == 2
