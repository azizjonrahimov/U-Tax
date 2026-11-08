"""Task 40b (design D36): the $49 filing-help Stripe link in the memo, the
signed /hooks/stripe webhook, and the revenue in the books and /api/state."""

from __future__ import annotations

import json
import time
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from app.gateway.adapters.stripe_pay import StripePay, sign, verify_stripe
from app.gateway.registry import AdapterNotReady, build_ports
from app.gateway.settings import Settings
from app.main import create_app

ADMIN = "admin-" + "a" * 30
WHSEC = "whsec_" + "s" * 32
SK = "sk_test_" + "k" * 24


class FakeStripe:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.down = False
        self.links = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        self.calls.append({"path": request.url.path, "form": form,
                           "idem": request.headers.get("idempotency-key"),
                           "auth": request.headers.get("authorization")})
        if self.down:
            return httpx.Response(503, json={"error": {"message": "down"}})
        if request.url.path == "/v1/prices":
            return httpx.Response(200, json={"id": "price_1"})
        if request.url.path == "/v1/payment_links":
            self.links += 1
            return httpx.Response(200, json={"id": f"plink_{self.links}",
                                             "url": f"https://buy.stripe.com/test_{self.links}"})
        return httpx.Response(404, json={"error": {"message": "no"}})

    def paths(self) -> list[str]:
        return [c["path"] for c in self.calls]


@pytest.fixture
def h(make_harness):
    harness = make_harness(admin_token=ADMIN, stripe_webhook_secret=WHSEC)
    harness.fake = FakeStripe()
    harness.ports.pay = StripePay(SK, transport=httpx.MockTransport(harness.fake))
    return harness


@pytest.fixture
def c(h):
    with TestClient(create_app(h.gw, start_background=False)) as test_client:
        yield test_client


def books(h):
    wb = load_workbook(h.settings.data_dir / "docs" / "U-Tax_Books.xlsx")
    rows = [[cell.value for cell in row] for row in wb.active.iter_rows(min_row=2)]
    wb.close()
    return rows


def paid_event(case_id="case_1", session="cs_test_1", event="evt_1", status="paid",
               kind="checkout.session.completed", livemode=False, amount=4900,
               metadata=True, link="plink_1"):
    return {"id": event, "type": kind, "livemode": livemode, "data": {"object": {
        "id": session, "object": "checkout.session", "payment_status": status,
        "amount_total": amount, "currency": "usd", "payment_link": link,
        "client_reference_id": case_id,
        "metadata": {"case_id": case_id} if metadata else {},
        "customer_details": {"email": "founder@team7.dev", "name": "Jo Founder"}}}}


def hook(c, event, secret=WHSEC, stamp=None):
    raw = json.dumps(event).encode()
    return c.post("/hooks/stripe", content=raw,
                  headers={"Stripe-Signature": sign(secret, raw, stamp or int(time.time())),
                           "Content-Type": "application/json"})


# ---------------------------------------------------------------- the adapter


def test_one_price_then_a_link_per_case_with_the_case_id_three_ways():
    fake = FakeStripe()
    pay = StripePay(SK, transport=httpx.MockTransport(fake))
    link = pay.create_link(4900, "U-Tax filing help", "pay:case_1:UTX-1", case_id="case_1")
    pay.create_link(4900, "U-Tax filing help", "pay:case_2:UTX-2", case_id="case_2")
    assert fake.paths() == ["/v1/prices", "/v1/payment_links", "/v1/payment_links"]
    price, first = fake.calls[0], fake.calls[1]
    assert price["form"] == {"currency": "usd", "unit_amount": "4900",
                             "product_data[name]": "U-Tax filing help"}
    assert first["form"]["line_items[0][price]"] == "price_1"
    assert first["form"]["metadata[case_id]"] == "case_1"
    assert first["form"]["payment_intent_data[metadata][case_id]"] == "case_1"
    assert first["idem"] == "utax-pay:case_1:UTX-1" and first["auth"] == f"Bearer {SK}"
    assert link.url == "https://buy.stripe.com/test_1?client_reference_id=case_1"
    assert link.link_id == "plink_1" and SK not in repr(pay)


def test_a_live_key_needs_stripe_live_and_a_test_key_boots(tmp_path):
    def ports(**kw):
        return build_ports(Settings(data_dir=tmp_path, adapters={"pay": "stripe"}, **kw))

    with pytest.raises(AdapterNotReady, match="STRIPE_LIVE=1"):
        ports(stripe_secret_key="sk_live_" + "x" * 20)
    with pytest.raises(AdapterNotReady, match="sk_ or rk_"):
        ports(stripe_secret_key="pk_test_" + "x" * 20)
    with pytest.raises(AdapterNotReady, match="STRIPE_SECRET_KEY"):
        ports()
    assert ports(stripe_secret_key=SK).pay.provider == "stripe"
    assert ports(stripe_secret_key="sk_live_" + "x" * 20, stripe_live=True).pay.live


def test_the_signature_check():
    raw, now = b'{"id": "evt_1"}', 1_800_000_000
    good = sign(WHSEC, raw, now)
    assert verify_stripe(WHSEC, good, raw, now=now + 10)
    assert verify_stripe(WHSEC, f"t={now},v1={'0' * 64},v1={good.split('v1=')[1]}", raw,
                         now=now)  # a rotated secret: either v1 may match
    assert not verify_stripe(WHSEC, good, raw + b" ", now=now)
    assert not verify_stripe("whsec_other", good, raw, now=now)
    assert not verify_stripe(WHSEC, good, raw, now=now + 301)  # a replayed old delivery
    assert not verify_stripe(WHSEC, None, raw, now=now)
    assert not verify_stripe(WHSEC, "v1=abc", raw, now=now)


# ---------------------------------------------------------------- the memo


def test_the_memo_carries_the_link_and_the_theater_never_sees_it(h):
    case_id = h.to_sent()
    [made] = h.events("payment.link_created")
    assert made.payload == {"link_id": "plink_1", "amount_cents": 4900, "currency": "usd"}
    assert made.private["url"].startswith("https://buy.stripe.com/test_1")
    assert h.fake.calls[0]["form"]["product_data[name]"] == h.gw.OFFER_LABEL
    [mail] = [json.loads(p.read_text(encoding="utf-8")) for p in h.outbox()
              if "franchise" in json.loads(p.read_text(encoding="utf-8"))["subject"].lower()]
    assert ("on a 20-minute call: https://buy.stripe.com/test_1?client_reference_id="
            + case_id) in mail["body"]
    [sent] = h.events("memo.sent")
    assert "buy.stripe.com" not in sent.payload["body"]
    assert "Want hands-on help" not in sent.payload["body"]  # the whole line, not "...call:"
    assert "buy.stripe.com" not in json.dumps([e.payload for e in h.store.since(0)])


def test_the_agents_memo_line_drops_whole_without_a_link(tmp_path):
    from app.gateway.templates import Templates

    memo = tmp_path / "memo.md"
    memo.write_text(
        "Hi {{greeting_name}}, your total is {{total}}.\n"
        "Want hands-on help? For $49, U-Tax prepares your Delaware annual report worksheet "
        "and walks you through filing it yourself on a 20-minute call: {{payment_link}}\n"
        "Educational estimate — not tax advice.\n\nReference: {{memo_ref}}\n",
        encoding="utf-8")
    t = Templates(tmp_path, memo)
    values = {"greeting_name": "Jo", "total": "$450", "memo_ref": "UTX-ABCDEF123456"}
    without = t.render("memo", values)
    assert "hands-on" not in without and ":\n" not in without
    with_link = t.render("memo", {**values, "payment_link": "https://buy.stripe.com/x"})
    assert "20-minute call: https://buy.stripe.com/x\n" in with_link


def test_a_second_memo_reuses_the_cases_link(h):
    case_id = h.to_sent()
    h.gw._payment_link(h.gw.state.cases[case_id])
    assert h.fake.paths().count("/v1/payment_links") == 1
    assert len(h.events("payment.link_created")) == 1


def test_stripe_down_never_holds_the_memo(h):
    h.fake.down = True
    h.to_sent()
    assert h.events("payment.link_created") == []
    [sent] = h.events("memo.sent")
    assert "Stripe" not in sent.payload["body"]


def test_without_stripe_the_memo_has_no_payment_line(make_harness):
    h = make_harness()
    h.to_sent()
    [sent] = h.events("memo.sent")
    assert "Stripe" not in sent.payload["body"] and "\n\n\n" not in sent.payload["body"]


# ---------------------------------------------------------------- the webhook


def test_a_paid_checkout_books_revenue_once(h, c):
    case_id = h.to_sent()
    out = hook(c, paid_event(case_id))
    assert out.status_code == 200 and out.json()["amount_cents"] == 4900
    [paid] = h.events("payment.received")
    assert paid.payload == {"case_id": case_id, "amount_cents": 4900, "currency": "usd"}
    assert paid.provider == "stripe" and paid.actor == "acct"
    assert "founder" not in json.dumps(paid.payload) + json.dumps(paid.private)  # no PII
    [row] = [r for r in books(h) if r[0] == f"{case_id}:filing"]
    assert row[4] == 49 and "Stripe" in row[3]
    state = c.get("/api/state", headers={"Authorization": f"Bearer {ADMIN}"}).json()
    assert state["money"]["revenue_cents"] == 4900
    # a redelivery, and the async-succeeded twin of the same session: still once
    assert hook(c, paid_event(case_id, event="evt_2")).json()["duplicate"] is True
    hook(c, paid_event(case_id, event="evt_3", kind="checkout.session.async_payment_succeeded"))
    assert len(h.events("payment.received")) == 1
    assert len([r for r in books(h) if r[0] == f"{case_id}:filing"]) == 1
    assert h.gw.state.revenue_cents == 4900


def test_the_case_is_found_by_the_link_when_metadata_is_missing(h, c):
    case_id = h.to_sent()
    event = paid_event("", metadata=False, link="plink_1")
    assert hook(c, event).json()["case_id"] == case_id


@pytest.mark.parametrize("event, ignored", [
    (paid_event(status="unpaid"), "payment_status unpaid"),
    (paid_event(livemode=True), "livemode"),
    (paid_event("case_99", link="plink_99"), "not_found"),
    ({"id": "evt_9", "type": "payment_intent.succeeded", "data": {"object": {}}},
     "payment_intent.succeeded"),
])
def test_what_is_acknowledged_but_not_booked(h, c, event, ignored):
    h.to_sent()
    out = hook(c, event)
    assert out.status_code == 200 and out.json()["ignored"] == ignored
    assert h.events("payment.received") == []


def test_signatures_and_setup(h, c):
    h.to_sent()
    assert hook(c, paid_event(), secret="whsec_wrong").status_code == 401
    assert hook(c, paid_event(), stamp=int(time.time()) - 3600).status_code == 401
    assert c.post("/hooks/stripe", content=b"{}").status_code == 401
    object.__setattr__(h.settings, "stripe_webhook_secret", "")
    assert hook(c, paid_event()).status_code == 503
    assert h.events("payment.received") == []


def test_locked_books_answer_503_so_stripe_retries(h, c, monkeypatch):
    case_id = h.to_sent()
    from app.gateway import documents

    real = documents.append_revenue

    def locked(*a, **kw):
        raise PermissionError("open in Excel")

    monkeypatch.setattr(documents, "append_revenue", locked)
    assert hook(c, paid_event(case_id)).status_code == 503
    assert h.events("payment.received") == []
    monkeypatch.setattr(documents, "append_revenue", real)
    assert hook(c, paid_event(case_id)).status_code == 200  # Stripe's retry
    assert len(h.events("payment.received")) == 1


# ---------------------------------------------------------------- devtools


class FakeEndpoints:
    def __init__(self, existing=None):
        self.existing = existing or []
        self.calls: list[tuple[str, str, dict]] = []

    def __call__(self, request):
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        self.calls.append((request.method, request.url.path, form))
        if request.method == "GET":
            return httpx.Response(200, json={"data": self.existing})
        if request.url.path == "/v1/webhook_endpoints":
            return httpx.Response(200, json={"id": "we_1", "secret": WHSEC, **form})
        return httpx.Response(200, json={"id": "we_1"})


def test_devtools_creates_updates_and_never_prints_the_secret(tmp_path, monkeypatch, capsys):
    from app import devtools

    for name in ("STRIPE_SECRET_KEY", "STRIPE_LIVE"):
        monkeypatch.delenv(name, raising=False)
    env = tmp_path / ".env"
    env.write_text(f"STRIPE_SECRET_KEY={SK}\n", encoding="utf-8")
    fake = FakeEndpoints()
    assert devtools.stripe_webhook("https://demo.u-tax.test", env,
                                   transport=httpx.MockTransport(fake)) == "created"
    _, path, form = fake.calls[-1]
    assert form["url"] == "https://demo.u-tax.test/hooks/stripe"
    assert form["enabled_events[0]"] == "checkout.session.completed"
    assert form["metadata[utax]"] == "gateway"
    assert f"STRIPE_WEBHOOK_SECRET={WHSEC}" in env.read_text(encoding="utf-8")
    assert WHSEC not in capsys.readouterr().out
    ours = {"id": "we_1", "url": "https://demo.u-tax.test/hooks/stripe",
            "metadata": {"utax": "gateway"}, "enabled_events": list(devtools.STRIPE_EVENTS)}
    assert devtools.stripe_webhook("https://demo.u-tax.test", env, transport=httpx.MockTransport(
        FakeEndpoints([ours]))) == "unchanged"
    moved = FakeEndpoints([ours])
    assert devtools.stripe_webhook("https://new.u-tax.test", env,
                                   transport=httpx.MockTransport(moved)) == "updated"
    assert moved.calls[-1][1] == "/v1/webhook_endpoints/we_1"
    env.write_text("STRIPE_SECRET_KEY=sk_live_" + "x" * 20 + "\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="STRIPE_LIVE=1"):
        devtools.stripe_webhook("https://demo.u-tax.test", env,
                                transport=httpx.MockTransport(FakeEndpoints()))
