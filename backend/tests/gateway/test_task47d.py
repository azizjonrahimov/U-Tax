"""D38 (task 47d): the sell side, contract v1.19 §9.4. The paid path is live-only
(UTAX_LIVE=1: a fresh buyer agent pays the in-process gateway on the testnet)."""

# no `from __future__ import annotations` (FastAPI routes in the live server)
import os
import socket
import threading
import time
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.gateway.adapters.tempo_wallet import TempoWallet, new_key
from app.main import create_app

FACTS = {"authorized_shares": 10_000_000, "issued_shares": 8_000_000,
         "gross_assets_cents": 50_000_000}


def selling(make_harness, **kw):
    h = make_harness(market_enabled=True, sell_enabled=True, mpp_secret_key="ab" * 32, **kw)
    h.ports.wallet = TempoWallet(new_key()[0])  # receives only: no funds, no network here
    return h


def test_off_unless_market_sell_tempo_and_a_secret(make_harness):
    for kw in ({}, {"market_enabled": True}, {"market_enabled": True, "sell_enabled": True}):
        h = make_harness(**kw)
        with TestClient(create_app(h.gw, start_background=False)) as c:
            r = c.post("/agent/franchise-check", json=FACTS)
            assert r.status_code == 404 and r.json()["error"]["code"] == "sell_off"
            assert c.get("/agent/llms.txt").status_code == 404


def test_bad_input_is_422_before_any_challenge(make_harness):
    h = selling(make_harness)
    with TestClient(create_app(h.gw, start_background=False)) as c:
        for bad in ({**FACTS, "authorized_shares": 0}, {**FACTS, "issued_shares": "8m"},
                    {**FACTS, "gross_assets_cents": True}, {**FACTS, "stated_par": "abc"},
                    {"authorized_shares": 5}, []):
            r = c.post("/agent/franchise-check", json=bad)
            assert r.status_code == 422, bad
            assert "www-authenticate" not in {k.lower() for k in r.headers}
        assert c.post("/agent/franchise-check", content=b"not json",
                      headers={"Content-Type": "application/json"}).status_code == 422
    assert h.events("agent.sale") == []


def test_good_input_unpaid_answers_a_402_payment_challenge(make_harness):
    h = selling(make_harness)
    with TestClient(create_app(h.gw, start_background=False)) as c:
        r = c.post("/agent/franchise-check", json=FACTS)
        assert r.status_code == 402
        assert r.headers["www-authenticate"].lower().startswith("payment")
        assert "0.50" in c.get("/agent/llms.txt").text
    assert h.events("agent.sale") == []


def test_a_sale_is_recorded_once_and_counted(make_harness):
    h = selling(make_harness)
    tx = "0x" + "ab" * 32
    h.gw.record_agent_sale("0.50", tx, "0x12…ab")
    h.gw.record_agent_sale("0.50", tx, "0x12…ab")  # a replayed receipt
    [sale] = h.events("agent.sale")
    assert sale.payload == {"amount_usd": "0.50", "token": "pathUSD", "tx_hash": tx,
                            "explorer_url": f"https://explore.tempo.xyz/tx/{tx}",
                            "payer_masked": "0x12…ab"}
    assert sale.provider == "tempo" and sale.actor == "system"
    money = h.gw.public_state(admin=True)["money"]
    assert money["agent_sales_usd"] == "0.50" and money["spent_usd"] == "0.00"


@pytest.mark.skipif(os.environ.get("UTAX_LIVE") != "1", reason="live testnet: UTAX_LIVE=1")
def test_live_a_buyer_agent_pays_for_a_franchise_check(make_harness):
    import uvicorn

    from app import devtools

    h = selling(make_harness)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(h.gw, start_background=False),
                                           host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(50):
        if server.started:
            break
        time.sleep(0.1)
    try:
        lines = []
        assert devtools.sell_demo(f"http://127.0.0.1:{port}", out=lines.append) == 0, lines
        print("\n".join(lines))
        [sale] = h.events("agent.sale")
        assert sale.payload["tx_hash"].startswith("0x") and sale.payload["amount_usd"] == "0.50"
        assert h.gw.state.agent_sales_usd == Decimal("0.50")
    finally:
        server.should_exit = True
