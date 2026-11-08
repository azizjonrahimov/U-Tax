"""Tested 2026-11-08 on Tempo Moderato: fake market + PixelAgent + TempoWallet, full loop.
Run from this folder: python e2e.py  (needs: pip install "pympp[tempo,sqlite]==0.20.0" fastapi uvicorn httpx pillow)
"""
import os, sys, time, json, subprocess, secrets, httpx
from eth_account import Account
import tempo_wallet as tw, share_card
firm, con = Account.create(), Account.create()
tw.faucet(firm.address)
env = {**os.environ, "MARKET_TOKEN": "tok_pixel", "MARKET_URL": "http://127.0.0.1:8031", "PIXELAGENT_KEY": con.key.hex(),
       "MPP_SECRET_KEY": secrets.token_hex(32), "PIXELAGENT_PORT": "8032", "PIXELAGENT_POLL_SECONDS": "1",
       "PIXELAGENT_STATE": "pa-state.json"}
if os.path.exists("pa-state.json"): os.remove("pa-state.json")
m = subprocess.Popen([sys.executable, "-m", "uvicorn", "fake_market:app", "--port", "8031", "--log-level", "warning"], env=env)
p = subprocess.Popen([sys.executable, "-m", "uvicorn", "pixelagent:app", "--port", "8032", "--log-level", "warning"], env=env)
try:
    time.sleep(4)
    h = "Delaware franchise tax: $85,165 → $450"
    httpx.post("http://127.0.0.1:8031/test/post", json={"job_id": "job_1", "template": "share_card", "price_usd": "0.50", "input": {"headline": h}})
    for _ in range(30):
        j = httpx.get("http://127.0.0.1:8031/test/job/job_1").json()
        if j["status"] == "submitted": break
        time.sleep(1)
    print("job status:", j["status"], "| contractor wallet:", j.get("wallet"))
    checks = share_card.verify(bytes.fromhex(j["artifact"]), h); print("verify:", share_card.passed(checks))
    st = httpx.get("http://127.0.0.1:8032/invoice/job_1/status").json(); print("status before pay:", st["paid"])
    w = tw.TempoWallet(firm.key.hex())
    b0 = w.balance_usd(j["wallet"])
    paid = w.pay_invoice(j["invoice_url"], max_usd="0.50", recipient=j["wallet"])
    print("paid:", paid.tx_hash[:18], "...", paid.amount_usd, paid.explorer_url)
    time.sleep(2); print("contractor got:", w.balance_usd(j["wallet"]) - b0, "| tx ok:", w.tx_ok(paid.tx_hash))
    again = httpx.post("http://127.0.0.1:8032/invoice/job_1").json(); print("second call duplicate:", again.get("duplicate"), "same tx:", again.get("tx") == paid.tx_hash)
    # overcharge guard: a contractor asking more than max_usd
    httpx.post("http://127.0.0.1:8031/test/post", json={"job_id": "job_2", "template": "share_card", "price_usd": "0.90", "input": {"headline": h}})
    for _ in range(30):
        j2 = httpx.get("http://127.0.0.1:8031/test/job/job_2").json()
        if j2["status"] == "submitted": break
        time.sleep(1)
    try:
        w.pay_invoice(j2["invoice_url"], max_usd="0.50", recipient=j2["wallet"]); print("OVERCHARGE PAID - BAD")
    except tw.WalletError as e: print("overcharge refused:", type(e).__name__, str(e)[:90])
    try:
        w.pay_invoice(j2["invoice_url"], max_usd="0.90", recipient=firm.address); print("WRONG RECIPIENT PAID - BAD")
    except tw.WalletError as e: print("wrong recipient refused:", type(e).__name__)
finally:
    m.terminate(); p.terminate()
