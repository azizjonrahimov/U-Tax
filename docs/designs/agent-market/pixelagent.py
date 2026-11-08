"""PixelAgent: an independent contractor agent with its own wallet.

- Worker loop: polls the U-Tax market for open share_card jobs, decides take/skip from its cost
  model, claims, renders the card, submits it.
- Invoice server: POST /invoice/{job_id} answers 402 (MPP, Tempo pathUSD) at the job's agreed
  price and gets paid to PIXELAGENT_WALLET; GET /invoice/{job_id}/status is free and tells the
  payer whether that job is already paid (never pay twice).

Env: PIXELAGENT_KEY (0x... private key), MARKET_URL (the U-Tax gateway), MARKET_TOKEN (this
contractor's bearer token), MPP_SECRET_KEY (any random hex), PIXELAGENT_PORT (default 8020),
PIXELAGENT_MIN_USD (default 0.30), PIXELAGENT_STATE (default pixelagent-state.json).
Run: uvicorn pixelagent:app --port 8020   (the worker starts with the app)
"""
from __future__ import annotations

import asyncio, json, os, threading, time
from decimal import Decimal
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from mpp import Credential, Receipt
from mpp.server import Mpp, pay
from mpp.methods.tempo import server as tempo, TempoAccount

import share_card

CHAIN, PATH_USD = 42431, "0x20c0000000000000000000000000000000000000"
KEY = os.environ["PIXELAGENT_KEY"]
WALLET = TempoAccount.from_key(KEY if KEY.startswith("0x") else "0x" + KEY).address
MARKET = os.environ["MARKET_URL"].rstrip("/")
TOKEN = os.environ["MARKET_TOKEN"]
MIN_USD = Decimal(os.environ.get("PIXELAGENT_MIN_USD", "0.30"))
STATE = Path(os.environ.get("PIXELAGENT_STATE", "pixelagent-state.json"))
_lock = threading.Lock()


def _load() -> dict:
    return json.loads(STATE.read_text()) if STATE.exists() else {"jobs": {}}


def _save(s: dict) -> None:
    tmp = STATE.with_suffix(".tmp"); tmp.write_text(json.dumps(s, indent=1)); tmp.replace(STATE)


def _job(job_id: str) -> dict | None:
    with _lock:
        return _load()["jobs"].get(job_id)


def _set(job_id: str, **fields) -> None:
    with _lock:
        s = _load(); s["jobs"].setdefault(job_id, {}).update(fields); _save(s)


app = FastAPI(title="PixelAgent")
# one charge method; each route call passes the job's own agreed amount
mpp_server = Mpp(methods=tempo.charge(recipient=WALLET, chain_id=CHAIN, currencies=[PATH_USD]),
                 realm="pixelagent", secret_key=os.environ["MPP_SECRET_KEY"])


@app.get("/invoice/{job_id}/status")
async def status(job_id: str):
    j = _job(job_id)
    if not j:
        return JSONResponse({"job_id": job_id, "known": False}, status_code=404)
    return {"job_id": job_id, "known": True, "paid": bool(j.get("tx")), "tx": j.get("tx"),
            "price_usd": j.get("price_usd"), "wallet": WALLET}


@app.post("/invoice/{job_id}")
async def invoice(request: Request):
    job_id = request.path_params["job_id"]
    j = _job(job_id)
    if not j or j.get("state") != "submitted":
        return JSONResponse({"error": "no submitted job with this id"}, status_code=404)
    if j.get("tx"):  # already paid: answer the same receipt, never charge twice
        return {"job_id": job_id, "paid": True, "tx": j["tx"], "amount_usd": j["price_usd"], "duplicate": True}

    # G2: the pay decorator calls the endpoint with (request, credential, receipt) only
    @pay(mpp_server.charge(amount=j["price_usd"], description=f"U-Tax job {job_id}"))
    async def _paid(request: Request, credential: Credential, receipt: Receipt):
        _set(job_id, tx=receipt.reference, paid_at=time.time())
        return JSONResponse({"job_id": job_id, "paid": True, "tx": receipt.reference,
                             "amount_usd": j["price_usd"]})
    return await _paid(request)


def decide(job: dict) -> str:
    """The contractor's own judgement: take only work that covers its cost."""
    return "take" if Decimal(job["price_usd"]) >= MIN_USD and job["template"] == "share_card" else "skip"


def work_once(client: httpx.Client) -> int:
    done = 0
    r = client.get(f"{MARKET}/market/jobs", params={"status": "open"})
    r.raise_for_status()
    for job in r.json().get("jobs", []):
        jid = job["job_id"]
        if _job(jid) or decide(job) != "take":
            continue
        c = client.post(f"{MARKET}/market/jobs/{jid}/claim",
                        json={"wallet": WALLET, "invoice_url": f"{os.environ.get('PIXELAGENT_PUBLIC_URL', 'http://127.0.0.1:' + os.environ.get('PIXELAGENT_PORT', '8020'))}/invoice/{jid}"})
        if c.status_code != 200:
            continue
        _set(jid, state="claimed", price_usd=job["price_usd"])
        out = STATE.parent / f"{jid}.png"
        share_card.render(job["input"]["headline"], str(out))
        s = client.post(f"{MARKET}/market/jobs/{jid}/submit", files={"file": (out.name, out.read_bytes(), "image/png")})
        if s.status_code == 200:
            _set(jid, state="submitted"); done += 1
    return done


def _worker() -> None:
    with httpx.Client(headers={"Authorization": f"Bearer {TOKEN}"}, timeout=30) as client:
        while True:
            try:
                work_once(client)
            except Exception as exc:  # keep polling; a market restart must not kill the agent
                print("pixelagent: poll failed:", exc)
            time.sleep(float(os.environ.get("PIXELAGENT_POLL_SECONDS", "3")))


@app.on_event("startup")
def _start() -> None:
    if os.environ.get("PIXELAGENT_WORKER", "1") == "1":
        threading.Thread(target=_worker, daemon=True).start()
