"""B53 (D41 §E): each Tempo contractor payment gets one labelled test-mode Stripe record (a
mirror, never revenue): sk_test_ keys only, one per tick outside the lock, 3 failures give up."""

from __future__ import annotations

import urllib.parse

import httpx
import pytest
from fastapi.testclient import TestClient

from app.gateway.adapters.stripe_pay import StripePay
from app.gateway.ports import NotConfigured
from app.main import create_app

from .test_payments_b52 import TXS, emit, paid
from .test_task40b import ADMIN, SK, WHSEC, hook

LIVE = "sk_live_" + "l" * 24


class FakeStripe:
    """POST /v1/payment_intents: a succeeded test-mode PaymentIntent per Idempotency-Key."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.fail = 0
        self.inside_lock: list[bool] = []
        self.gw = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.gw is not None:  # the mirror never holds the gateway lock on the network
            self.inside_lock.append(self.gw._lock._is_owned())
        form = dict(urllib.parse.parse_qsl(request.content.decode()))
        self.calls.append({"path": request.url.path, "form": form,
                           "key": request.headers.get("idempotency-key")})
        if self.fail:
            self.fail -= 1
            return httpx.Response(500, json={"error": {"message": "down"}})
        n = len(self.calls)
        return httpx.Response(200, json={"id": f"pi_{n}", "status": "succeeded"})


@pytest.fixture
def m(make_harness):
    h = make_harness(admin_token=ADMIN, market_enabled=True, stripe_webhook_secret=WHSEC)
    h.fake = FakeStripe()
    h.ports.pay = StripePay(SK, transport=httpx.MockTransport(h.fake))
    h.fake.gw = h.gw
    return h


def test_the_adapter_posts_a_labelled_test_mode_payment_intent():
    fake = FakeStripe()
    pay = StripePay(SK, transport=httpx.MockTransport(fake))
    pi = pay.mirror_contractor_payment(tx_hash=TXS[0], amount_usd="0.50", token="pathUSD",
                                       job_id="job_2", contractor="PixelAgent",
                                       explorer_url="https://explore.tempo.xyz/tx/" + TXS[0],
                                       order_id="order_406")
    assert pi == "pi_1"
    [call] = fake.calls
    form = call["form"]
    assert call["path"] == "/v1/payment_intents" and call["key"] == f"utax-mirror-{TXS[0]}"
    assert form["amount"] == "50" and form["currency"] == "usd" and form["confirm"] == "true"
    assert form["payment_method"] == "pm_card_visa"
    # B53b: live Stripe refuses payment_method_types on this account's API version
    assert "payment_method_types[]" not in form and "payment_method_types" not in form
    assert form["automatic_payment_methods[enabled]"] == "true"
    assert form["automatic_payment_methods[allow_redirects]"] == "never"
    assert form["description"] == ("U-Tax paid PixelAgent for job_2: MIRROR of Tempo testnet "
                                   f"tx {TXS[0][:10]}…")
    assert form["metadata[utax_kind]"] == "contractor_payment_mirror"
    assert form["metadata[tempo_tx]"] == TXS[0] and form["metadata[order_id]"] == "order_406"
    assert form["metadata[amount_token]"] == "0.50 pathUSD"
    # under Stripe's card minimum: still $0.50
    pay.mirror_contractor_payment(tx_hash="0x1", amount_usd="0.10", token="pathUSD",
                                  job_id="job_3", contractor="PixelAgent", explorer_url="",
                                  order_id=None)
    assert fake.calls[-1]["form"]["amount"] == "50"
    assert "metadata[order_id]" not in fake.calls[-1]["form"]


def test_a_live_key_never_mirrors():
    fake = FakeStripe()
    pay = StripePay(LIVE, transport=httpx.MockTransport(fake))
    with pytest.raises(NotConfigured):
        pay.mirror_contractor_payment(tx_hash="0x1", amount_usd="0.50", token="pathUSD",
                                      job_id="job_1", contractor="PixelAgent", explorer_url="",
                                      order_id=None)
    assert fake.calls == []


def test_the_tick_backfills_one_payment_at_a_time_outside_the_lock(m):
    emit(m, *(paid(f"job_{i}", tx) for i, tx in enumerate(TXS, 1)),
         paid("job_4", "", status="failed"))
    assert m.gw.mirror_payments() == TXS[0]
    assert len(m.fake.calls) == 1 and m.fake.inside_lock == [False]
    [rec] = m.events("payment.recorded")
    assert rec.provider == "stripe" and rec.idempotency_key == f"stripe_mirror:{TXS[0]}"
    assert rec.payload == {"job_id": "job_1", "tx_hash": TXS[0], "stripe_id": "pi_1",
                           "mode": "mirror",
                           "dashboard_url": "https://dashboard.stripe.com/test/payments/pi_1"}
    assert m.gw.mirror_payments() == TXS[1] and m.gw.mirror_payments() == TXS[2]
    assert m.gw.mirror_payments() is None  # all three recorded; the failed one never
    assert len(m.fake.calls) == 3
    m.restart()
    m.gw.mirror_payments()
    assert len(m.events("payment.recorded")) == 3  # a restart re-reads the log: no repeat
    rows = {r["job_id"]: r for r in m.gw.payments()["data"]}
    assert rows["job_1"]["stripe"]["mode"] == "mirror" and "stripe" not in rows["job_4"]


def test_three_failures_give_up_for_this_process(m):
    emit(m, paid("job_1", TXS[0]), paid("job_2", TXS[1]))
    m.fake.fail = 3
    for _ in range(3):
        assert m.gw.mirror_payments() is None
    assert m.gw.mirror_payments() == TXS[1]  # the next one goes on
    assert m.gw.mirror_payments() is None  # TXS[0] given up
    assert [e.payload["tx_hash"] for e in m.events("payment.recorded")] == [TXS[1]]


@pytest.mark.parametrize("kw", [{"stripe_mirror": False}, {"market_enabled": False}])
def test_off_switches(make_harness, kw):
    h = make_harness(**{"market_enabled": True, **kw})
    h.fake = FakeStripe()
    h.ports.pay = StripePay(SK, transport=httpx.MockTransport(h.fake))
    emit(h, paid("job_1", TXS[0]))
    assert h.gw.mirror_payments() is None and h.fake.calls == []


def test_no_stripe_or_a_live_key_does_nothing(make_harness):
    h = make_harness(market_enabled=True)
    emit(h, paid("job_1", TXS[0]))
    assert h.gw.mirror_payments() is None  # the local pay port has no mirror
    fake = FakeStripe()
    h.ports.pay = StripePay(LIVE, transport=httpx.MockTransport(fake))
    assert h.gw.mirror_payments() is None and fake.calls == []


def test_the_setting_reads_stripe_mirror(monkeypatch):
    from app.gateway.settings import Settings

    monkeypatch.setenv("STRIPE_MIRROR", "0")
    assert Settings.from_env().stripe_mirror is False
    monkeypatch.delenv("STRIPE_MIRROR")
    assert Settings.from_env().stripe_mirror is True


def test_a_mirror_never_becomes_revenue(m):
    with TestClient(create_app(m.gw, start_background=False)) as c:
        for kind, obj in (("payment_intent.succeeded", "payment_intent"),
                          ("checkout.session.completed", "checkout.session")):
            event = {"id": f"evt_{obj}", "type": kind, "livemode": False, "data": {"object": {
                "id": "pi_1" if obj == "payment_intent" else "cs_1", "object": obj,
                "payment_status": "paid", "amount_total": 50, "amount": 50, "currency": "usd",
                "client_reference_id": "case_1",
                "metadata": {"utax_kind": "contractor_payment_mirror", "case_id": "case_1"}}}}
            r = hook(c, event)
            assert r.status_code == 200 and r.json()["ignored"] == "mirror", r.json()
    assert m.events("payment.received") == [] and m.gw.payments()["totals"][
        "clients_paid_usd"] == "0.00"


def test_the_tick_runs_it(monkeypatch):
    from app import main

    called = []

    class Gw:
        def __getattr__(self, name):
            return lambda *a, **k: called.append(name)

    main.tick(Gw())
    assert "mirror_payments" in called and "advance_orders" in called
