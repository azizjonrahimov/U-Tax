"""D38 (task 47b): the agent market in the gateway, contract v1.19 §9. LocalWallet and a
fake contractor (httpx.MockTransport); the Tempo wallet itself is tested in
tests/adapters/test_wallet*.py."""

from __future__ import annotations

import io
import json
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from PIL.PngImagePlugin import PngInfo

from app.gateway.ports import PaidInvoice, Unsure
from app.main import create_app

ADMIN = "admin-" + "a" * 30
TOKEN = "tok_" + "p" * 28
WALLET = "0x" + "5f" * 20
BASE = "http://pixel.test"
HEADLINE = "Delaware franchise tax: $85,165 → $450"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def card(headline=HEADLINE, size=(1200, 630)) -> bytes:
    img = Image.new("RGB", size, (7, 7, 11))
    d = ImageDraw.Draw(img)
    for i in range(0, size[0], 40):
        d.rectangle([i, 0, i + 20, size[1]], fill=(255, 77, 61))
    meta = PngInfo()
    meta.add_text("utax:headline", headline)
    out = io.BytesIO()
    img.save(out, "PNG", pnginfo=meta)
    return out.getvalue()


class Contractor:
    """The contractor's invoice status endpoint (GET {base}/invoice/{id}/status)."""

    def __init__(self):
        self.paid: dict[str, str] = {}
        self.down = False
        self.calls: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(f"{request.method} {request.url.path}")
        if self.down:
            raise httpx.ConnectError("down")
        job_id = request.url.path.split("/")[2]
        tx = self.paid.get(job_id)
        return httpx.Response(200, json={"job_id": job_id, "known": True, "paid": bool(tx),
                                         "tx": tx})


class CountingWallet:
    """LocalWallet's shape, counting payments; `fail` raises these first."""

    provider, token, address = "local", "ledger token", "0x" + "de" * 20

    def __init__(self, *fail):
        self.fail = list(fail)
        self.paid: list[tuple] = []

    def balance_usd(self, address=None):
        return Decimal("1000")

    def tx_ok(self, tx):
        return True if str(tx).startswith("local:") else None

    def explorer_url(self, tx):
        return ""

    def pay_invoice(self, url, *, max_usd, recipient):
        if self.fail:
            raise self.fail.pop(0)
        self.paid.append((url, max_usd, recipient))
        return PaidInvoice(tx_hash=f"local:{len(self.paid)}", amount_usd=max_usd,
                           explorer_url="", paid=False)


@pytest.fixture
def m(make_harness):
    h = make_harness(admin_token=ADMIN, market_enabled=True, public_url="https://gw.test",
                     market_contractors_raw=f"pixelagent|{TOKEN}|{WALLET}|{BASE}")
    h.contractor = Contractor()
    h.gw.market_http = httpx.Client(transport=httpx.MockTransport(h.contractor))
    h.wallet = h.ports.wallet = CountingWallet()
    h.gw.market_kick = False  # the tests run the tick themselves
    return h


@pytest.fixture
def c(m):
    with TestClient(create_app(m.gw, start_background=False)) as client:
        yield client


def hire(h, key="k1", headline=HEADLINE):
    return h.gw.perform("hire_contractor", {"template": "share_card", "headline": headline,
                                            "key": key}, actor="mkt")


def claim(c, job_id="job_1", wallet=WALLET, url=None):
    return c.post(f"/market/jobs/{job_id}/claim", headers=AUTH,
                  json={"wallet": wallet, "invoice_url": url or f"{BASE}/invoice/{job_id}"})


def submit(c, job_id="job_1", data=None):
    return c.post(f"/market/jobs/{job_id}/submit", headers=AUTH,
                  files={"file": ("card.png", data or card(), "image/png")})


def to_verified(h, c, key="k1"):
    job_id = hire(h, key).data["job_id"]
    assert claim(c, job_id).status_code == 200
    assert submit(c, job_id).json()["verified"] is True
    return job_id


def approve_first_payment(h, job_id):
    h.gw.advance_market_jobs()
    view = h.ok("get_job", job_id=job_id)
    h.ok("approve", approval_id=view["approval_id"], decision="allow", note="ok")


# ---------------------------------------------------------------- hire


def test_hire_requires_mkt_and_guards_headline(m):
    assert m.gw.perform("hire_contractor", {"template": "share_card", "headline": HEADLINE,
                                            "key": "k"}, actor="intake").error["code"] == \
        "tool_not_allowed"
    out = hire(m, headline="Team #7 saved $85,165 → $450")
    assert out.status == "refused" and out.error["code"] == "content_guard"
    out = hire(m)
    assert out.status == "done" and out.data == {"job_id": "job_1", "status": "open",
                                                 "price_usd": "0.50"}
    assert hire(m).data["duplicate"] is True and len(m.events("job.posted")) == 1
    [posted] = m.events("job.posted")
    assert posted.actor == "mkt" and posted.payload == {
        "job_id": "job_1", "template": "share_card", "price_usd": "0.50", "headline": HEADLINE}


def test_market_off_refuses_everything(make_harness):
    h = make_harness(admin_token=ADMIN)
    out = h.gw.perform("hire_contractor", {"template": "share_card", "headline": HEADLINE,
                                           "key": "k"}, actor="mkt")
    assert out.error["code"] == "market_off"
    assert "contractor_pay" not in h.gw.state.trust and h.ports.wallet is None
    with TestClient(create_app(h.gw, start_background=False)) as client:
        for r in (client.get("/market/llms.txt"), client.get("/market/jobs", headers=AUTH),
                  client.post("/market/jobs/job_1/claim", headers=AUTH, json={})):
            assert r.status_code == 404 and r.json()["error"]["code"] == "market_off"
        state = client.get("/api/state", headers={"Authorization": f"Bearer {ADMIN}"}).json()
    assert "contractor_paid_usd" not in state["money"]
    assert set(state["trust"]) == {"email", "social_post", "calendar_invite", "voice_screen"}
    assert h.gw.advance_market_jobs() == []


# ---------------------------------------------------------------- claim and submit


def test_claim_auth_wallet_mismatch_403_taken_409(m, c):
    hire(m)
    assert c.get("/market/jobs").status_code == 401
    jobs = c.get("/market/jobs", headers=AUTH).json()["jobs"]
    assert jobs[0]["input"] == {"headline": HEADLINE} and jobs[0]["price_usd"] == "0.50"
    r = claim(c, wallet="0x" + "11" * 20)
    assert r.status_code == 403 and r.json()["error"]["code"] == "wallet_mismatch"
    r = claim(c, url="http://evil.test/invoice/job_1")
    assert r.status_code == 403
    assert claim(c).status_code == 200
    r = claim(c)
    assert r.status_code == 409 and r.json()["error"]["code"] == "taken"
    assert c.get("/market/jobs", headers=AUTH).json()["jobs"] == []
    [claimed] = m.events("job.claimed")
    assert claimed.payload == {"job_id": "job_1", "contractor": "pixelagent"}


def test_claim_expired_410(m, c):
    hire(m)
    m.clock.advance(minutes=11)
    r = claim(c)
    assert r.status_code == 410 and r.json()["error"]["code"] == "expired"
    m.gw.advance_market_jobs()
    [failed] = m.events("job.failed")
    assert failed.payload["reason"] == "expired"
    assert m.ok("get_job", job_id="job_1")["status"] == "expired"


def test_submit_verify_pass_and_fail_with_one_resubmit(m, c):
    hire(m)
    assert submit(c).status_code == 409  # not claimed yet
    claim(c)
    bad = submit(c, data=card(size=(800, 600))).json()
    assert bad["verified"] is False and bad["resubmit"] is True
    assert {"name": "dimensions_1200x630", "ok": False} in bad["checks"]
    assert m.ok("get_job", job_id="job_1")["status"] == "claimed"
    worse = submit(c, data=card(headline="Something else")).json()
    assert worse["verified"] is False and worse["resubmit"] is False
    assert m.ok("get_job", job_id="job_1")["status"] == "failed"
    assert submit(c).status_code == 409  # no third try
    assert [e.payload["reason"] for e in m.events("job.failed")] == ["checks_failed"] * 2


def test_no_payment_without_verified(m, c):
    hire(m)
    claim(c)
    m.gw.advance_market_jobs()
    assert m.events("payment.sent") == [] and m.wallet.paid == []


# ---------------------------------------------------------------- paying


def test_first_payment_asks_ceo_then_auto(m, c):
    job_id = to_verified(m, c)
    m.gw.advance_market_jobs()
    view = m.ok("get_job", job_id=job_id)
    assert view["status"] == "verified" and view["approval_id"].startswith("apr_")
    assert m.wallet.paid == []
    m.ok("approve", approval_id=view["approval_id"], decision="allow", note="ok")
    assert m.gw.advance_market_jobs() == [job_id]
    view = m.ok("get_job", job_id=job_id)
    assert view["status"] == "delivered" and "approval_id" not in view
    assert m.wallet.paid == [(f"{BASE}/invoice/{job_id}", "0.50", WALLET)]
    assert m.gw.state.trust["contractor_pay"] == "AUTO"
    second = to_verified(m, c, key="k2")
    assert m.gw.advance_market_jobs() == [second]  # AUTO now: no approval


def test_caps_per_job_and_per_day_ask(make_harness):
    h = make_harness(admin_token=ADMIN, market_enabled=True, public_url="https://gw.test",
                     market_price_usd=Decimal("0.80"), market_max_job_usd=Decimal("0.50"),
                     market_max_day_usd=Decimal("1.00"),
                     market_contractors_raw=f"pixelagent|{TOKEN}|{WALLET}|{BASE}")
    h.contractor = Contractor()
    h.gw.market_http = httpx.Client(transport=httpx.MockTransport(h.contractor))
    h.wallet = h.ports.wallet = CountingWallet()
    h.gw.market_kick = False
    with TestClient(create_app(h.gw, start_background=False)) as c:
        first = to_verified(h, c)
        approve_first_payment(h, first)
        h.gw.advance_market_jobs()
        assert h.gw.state.trust["contractor_pay"] == "AUTO"
        second = to_verified(h, c, key="k2")
        h.gw.advance_market_jobs()  # 0.80 > the 0.50 per-job cap: asks even on AUTO
        assert "approval_id" in h.ok("get_job", job_id=second)
        assert len(h.wallet.paid) == 1


def test_pay_is_idempotent(m, c):
    job_id = to_verified(m, c)
    approve_first_payment(m, job_id)
    m.gw.advance_market_jobs()
    m.gw.advance_market_jobs()
    assert len(m.events("payment.sent")) == 1 and len(m.wallet.paid) == 1
    assert len(m.events("job.delivered")) == 1


def test_unsure_resolves_from_contractor_status_without_second_payment(m, c):
    m.wallet.fail = [Unsure("timeout after signing")]
    job_id = to_verified(m, c)
    approve_first_payment(m, job_id)
    m.gw.advance_market_jobs()
    [unsure] = m.events("payment.sent")
    assert unsure.status == "failed" and unsure.private == {"unsure": True}
    assert m.ok("get_job", job_id=job_id)["status"] == "verified"
    # the contractor was paid after all: its status says so, and the chain agrees
    m.contractor.paid[job_id] = "local:from-the-chain"
    m.contractor.down = True
    m.gw.advance_market_jobs()  # can't ask the contractor: never pays blind
    assert m.wallet.paid == [] and m.ok("get_job", job_id=job_id)["status"] == "verified"
    m.contractor.down = False
    m.gw.advance_market_jobs()
    assert m.wallet.paid == []  # booked from the status, no second payment
    ok = [e for e in m.events("payment.sent") if e.status == "ok"]
    assert len(ok) == 1 and ok[0].payload["tx_hash"] == "local:from-the-chain"
    assert m.ok("get_job", job_id=job_id)["status"] == "delivered"


def test_a_refusing_contractor_fails_after_three_tries(m, c):
    from app.gateway.ports import Rejected

    m.wallet.fail = [Rejected("asks 0.90")] * 3
    job_id = to_verified(m, c)
    approve_first_payment(m, job_id)
    for _ in range(3):
        m.gw.advance_market_jobs()
    assert m.ok("get_job", job_id=job_id)["status"] == "failed"
    assert m.events("job.failed")[-1].payload["reason"] == "contractor_error"
    assert m.gw._pay_intents.get(f"pay:{job_id}") is None


def test_delivered_asset_passes_asset_url_ok_and_post_social(m, c):
    job_id = to_verified(m, c)
    approve_first_payment(m, job_id)
    m.gw.advance_market_jobs()
    image_url = m.ok("get_job", job_id=job_id)["image_url"]
    assert image_url == "https://gw.test/assets/ad_1.png"
    assert (m.settings.data_dir / "assets" / "ad_1.png").read_bytes() == card()
    assert m.gw._asset_url_ok(image_url)
    m.gw.state.trust["social_post"] = "AUTO"
    out = m.gw.perform("post_social", {"channel": "linkedin", "text": HEADLINE,
                                       "image_url": image_url, "kind": "client"}, actor="mkt")
    assert out.status == "done", out.as_dict()
    wakes = []
    m.gw.on_wake(wakes.append)
    second = to_verified(m, c, key="k2")
    m.gw.advance_market_jobs()
    assert m.ok("get_job", job_id=second)["image_url"].endswith("/ad_2.png")  # next free
    assert wakes and wakes[-1].startswith(f"Job {second} delivered")


def test_money_panel_counts_contractor_paid(m, c):
    job_id = to_verified(m, c)
    approve_first_payment(m, job_id)
    m.gw.advance_market_jobs()
    money = m.gw.public_state(admin=True)["money"]
    assert money["contractor_paid_usd"] == "0.50" and money["agent_sales_usd"] == "0.00"
    assert money["spent_by_provider"]["local"] == "0.50"
    [booked] = [e for e in m.events("cost.booked") if e.payload["model"].startswith("contractor")]
    assert booked.payload == {"provider": "local", "model": "contractor:share_card",
                              "estimated": False} and booked.cost_usd == "0.50"


def test_events_payload_keys_match_contract_v1_19(m, c):
    job_id = to_verified(m, c)
    approve_first_payment(m, job_id)
    m.gw.advance_market_jobs()
    keys = {e.type: set(e.payload) for e in m.store.since(0)}
    assert keys["job.posted"] == {"job_id", "template", "price_usd", "headline"}
    assert keys["job.claimed"] == {"job_id", "contractor"}
    assert keys["job.submitted"] == {"job_id", "contractor", "sha256"}
    assert keys["job.verified"] == {"job_id", "checks"}
    assert keys["payment.sent"] == {"job_id", "contractor", "amount_usd", "token", "tx_hash",
                                    "explorer_url"}
    assert keys["job.delivered"] == {"job_id", "image_url"}
    [paid] = m.events("payment.sent")
    assert paid.provider == "local" and paid.cost_usd == "0.50"
    assert TOKEN not in json.dumps([e.record() for e in m.store.since(0)])


# ---------------------------------------------------------------- get_job (agents A41)


def test_get_job_waits_for_a_change_outside_the_lock(m, c):
    job_id = hire(m).data["job_id"]
    m.slept.clear()
    out = m.ok("get_job", job_id=job_id)  # nothing changes: waits the whole ~20 s
    assert out["status"] == "open" and len(m.slept) == 39 and set(m.slept) == {0.5}

    def claim_on_third_sleep(seconds):  # the contractor claims while get_job waits
        m.slept.append(seconds)
        if len(m.slept) == 3:
            assert m.gw._lock.acquire(blocking=False)  # the wait holds no lock
            m.gw._lock.release()
            assert claim(c, job_id).status_code == 200

    m.slept.clear()
    m.gw.sleep = claim_on_third_sleep
    out = m.ok("get_job", job_id=job_id)
    assert out["status"] == "claimed" and len(m.slept) == 3  # answered at the change


def test_get_job_returns_at_once_when_final_and_shows_approval_id(m, c):
    job_id = to_verified(m, c)
    m.gw.advance_market_jobs()
    m.slept.clear()
    view = m.ok("get_job", job_id=job_id)
    assert view["approval_id"] == m.gw.state.approval_by_key[f"pay:{job_id}"]
    m.ok("approve", approval_id=view["approval_id"], decision="allow", note="ok")
    m.gw.advance_market_jobs()
    m.slept.clear()
    assert m.ok("get_job", job_id=job_id)["status"] == "delivered" and m.slept == []
