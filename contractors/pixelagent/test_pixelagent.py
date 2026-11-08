"""PixelAgent (D38, task 47c). Run from this folder:
    ..\\..\\backend\\.venv\\Scripts\\python -m pytest test_pixelagent.py -q
(or with this folder's own .venv after `pip install -r requirements.txt`)."""

import importlib
import json
import sys

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def pa(tmp_path, monkeypatch):
    from eth_account import Account

    monkeypatch.setenv("PIXELAGENT_KEY", Account.create().key.hex())
    monkeypatch.setenv("MPP_SECRET_KEY", "ab" * 32)
    monkeypatch.setenv("MARKET_TOKEN", "t" * 32)
    monkeypatch.setenv("PIXELAGENT_STATE", str(tmp_path / "state.json"))
    monkeypatch.setenv("PIXELAGENT_WORKER", "0")
    sys.modules.pop("pixelagent", None)
    module = importlib.import_module("pixelagent")
    with TestClient(module.app) as client:
        module.client = client
        yield module


def test_decide_takes_only_work_that_covers_its_cost(pa):
    assert pa.decide({"template": "share_card", "price_usd": "0.50"}) == "take"
    assert pa.decide({"template": "share_card", "price_usd": "0.30"}) == "take"
    assert pa.decide({"template": "share_card", "price_usd": "0.10"}) == "skip"
    assert pa.decide({"template": "logo", "price_usd": "5.00"}) == "skip"
    assert pa.decide({"template": "share_card", "price_usd": "lots"}) == "skip"


def test_status_of_an_unknown_job_is_404(pa):
    r = pa.client.get("/invoice/job_9/status")
    assert r.status_code == 404 and r.json() == {"job_id": "job_9", "known": False}


def test_an_invoice_for_a_job_not_submitted_is_404(pa):
    assert pa.client.post("/invoice/job_9").status_code == 404
    pa._set("job_1", state="claimed", price_usd="0.50")
    assert pa.client.post("/invoice/job_1").status_code == 404


def test_a_submitted_job_answers_a_402_payment_challenge(pa):
    pa._set("job_1", state="submitted", price_usd="0.50")
    r = pa.client.post("/invoice/job_1")
    assert r.status_code == 402
    assert r.headers.get("www-authenticate", "").lower().startswith("payment")


def test_a_paid_jobs_second_invoice_is_a_duplicate_with_the_same_tx(pa):
    pa._set("job_1", state="submitted", price_usd="0.50", tx="0xabc")
    r = pa.client.post("/invoice/job_1")
    assert r.status_code == 200 and r.json() == {"job_id": "job_1", "paid": True,
                                                 "tx": "0xabc", "amount_usd": "0.50",
                                                 "duplicate": True}
    status = pa.client.get("/invoice/job_1/status").json()
    assert status["paid"] is True and status["tx"] == "0xabc" and status["wallet"] == pa.WALLET


def test_the_card_it_renders_passes_its_own_checks(pa, tmp_path):
    headline = "Delaware franchise tax: $85,165 → $450"
    out = pa.share_card.render(headline, str(tmp_path / "c.png"))
    assert pa.share_card.passed(pa.share_card.verify(open(out, "rb").read(), headline))


def test_the_key_is_never_in_the_status_or_state(pa, tmp_path):
    pa._set("job_1", state="submitted", price_usd="0.50")
    text = json.dumps(pa.client.get("/invoice/job_1/status").json()) + \
        (tmp_path / "state.json").read_text(encoding="utf-8")
    key = __import__("os").environ["PIXELAGENT_KEY"]
    assert key not in text and key.removeprefix("0x") not in text
