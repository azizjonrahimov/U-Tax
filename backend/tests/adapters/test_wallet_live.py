"""D38 gate G1: a real testnet payment (Tempo Moderato, chain 42431). Skipped unless
UTAX_LIVE=1 (it calls the testnet faucet and RPC):

    UTAX_LIVE=1 python -m pytest tests/adapters/test_wallet_live.py -q

A fresh firm wallet is funded from the faucet and pays a local MPP 402 invoice server (a
fresh payee wallet) 0.50 pathUSD; an overcharge and a wrong recipient are refused before
signing. Test tokens only: they have no value.
"""

# no `from __future__ import annotations`: FastAPI must resolve the route's Request type

import os
import secrets
import socket
import threading
import time
from decimal import Decimal

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("UTAX_LIVE") != "1",
                                reason="live testnet test: set UTAX_LIVE=1")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _invoice_server(payee: str, prices: dict[str, str]):
    import uvicorn
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse
    from mpp import Credential, Receipt
    from mpp.methods.tempo import server as tempo
    from mpp.server import Mpp, pay

    from app.gateway.adapters.tempo_wallet import PATH_USD, TESTNET_CHAIN_ID

    app = FastAPI()
    server = Mpp(methods=tempo.charge(recipient=payee, chain_id=TESTNET_CHAIN_ID,
                                      currencies=[PATH_USD]),
                 realm="utax-live-test", secret_key=secrets.token_hex(32))

    @app.post("/invoice/{job_id}")
    async def invoice(request: Request):
        price = prices[request.path_params["job_id"]]

        @pay(server.charge(amount=price, description="live test"))
        async def _paid(request: Request, credential: Credential, receipt: Receipt):  # G2
            return JSONResponse({"paid": True, "tx": receipt.reference, "amount_usd": price})

        return await _paid(request)

    port = _free_port()
    uv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=uv.run, daemon=True).start()
    for _ in range(50):
        if uv.started:
            break
        time.sleep(0.1)
    return uv, f"http://127.0.0.1:{port}"


def test_a_real_testnet_payment_and_the_payers_guards():
    from app.gateway.adapters import tempo_wallet as tw
    from app.gateway.ports import Rejected

    firm_key, firm = tw.new_key()
    _, payee = tw.new_key()
    tw.faucet(firm)
    for _ in range(20):
        if tw.balance_of(firm) > 0:
            break
        time.sleep(1)
    wallet = tw.TempoWallet(firm_key)
    assert wallet.balance_usd() > Decimal("1")
    uv, base = _invoice_server(payee, {"job_1": "0.50", "job_2": "0.90"})
    try:
        paid = wallet.pay_invoice(f"{base}/invoice/job_1", max_usd="0.50", recipient=payee)
        assert paid.paid and paid.tx_hash.startswith("0x") and paid.amount_usd == "0.50"
        for _ in range(20):
            if wallet.tx_ok(paid.tx_hash):
                break
            time.sleep(1)
        assert wallet.tx_ok(paid.tx_hash) is True
        assert tw.balance_of(payee) == Decimal("0.5")
        print("G1 tx:", paid.explorer_url)
        with pytest.raises(Rejected):  # asks 0.90, the cap is 0.50: refused before signing
            wallet.pay_invoice(f"{base}/invoice/job_2", max_usd="0.50", recipient=payee)
        with pytest.raises(Rejected):  # someone else's wallet: refused before signing
            wallet.pay_invoice(f"{base}/invoice/job_2", max_usd="0.90", recipient=firm)
        assert tw.balance_of(payee) == Decimal("0.5")  # nothing more moved
    finally:
        uv.should_exit = True
