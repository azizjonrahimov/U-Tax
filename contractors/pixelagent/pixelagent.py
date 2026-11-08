"""PixelAgent: an independent contractor agent with its own wallet (D38, task 47c).

From docs/designs/agent-market/pixelagent.py (tested on Tempo Moderato on 2026-11-08).

- Worker loop: polls the U-Tax market for open share_card jobs, decides take/skip from its
  own cost model (`decide`), claims, renders the card, checks it, submits it.
- Invoice server: POST /invoice/{job_id} answers 402 (MPP, Tempo pathUSD, chain 42431) at
  the job's agreed price and gets paid to this agent's own wallet; GET
  /invoice/{job_id}/status is free and tells the payer whether that job is already paid
  (U-Tax checks it before paying, so a job is never paid twice).
- The MPP replay store is SQLite (gotcha G4), so a restart never accepts a replayed payment.

Env (see .env.example): PIXELAGENT_KEY (0x... private key, never printed), MARKET_URL,
MARKET_TOKEN, MPP_SECRET_KEY, PIXELAGENT_PORT (8020), PIXELAGENT_PUBLIC_URL (what U-Tax
reaches; default http://127.0.0.1:<port>), PIXELAGENT_MIN_USD (0.30),
PIXELAGENT_POLL_SECONDS (3), PIXELAGENT_STATE (data/pixelagent-state.json),
PIXELAGENT_WORKER (1; 0 = the invoice server only).

Run:  .venv\\Scripts\\python -m uvicorn pixelagent:app --host 127.0.0.1 --port 8020
Testnet tokens only: they have no value.
"""

# no `from __future__ import annotations`: the MPP pay decorator and FastAPI read real types
import json
import os
import threading
import time
from contextlib import asynccontextmanager
from decimal import Decimal, InvalidOperation
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from mpp import Credential, Receipt
from mpp.server import pay

import share_card

CHAIN, PATH_USD = 42431, "0x20c0000000000000000000000000000000000000"
HERE = Path(__file__).resolve().parent


def _load_env() -> None:
    """contractors/pixelagent/.env (written by `python -m app.devtools market-wallets`),
    under the process's own environment."""
    path = HERE / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            name, value = line.split("=", 1)
            os.environ.setdefault(name.strip(), value.strip().strip('"'))


_load_env()
KEY = os.environ["PIXELAGENT_KEY"]
MARKET = os.environ.get("MARKET_URL", "http://127.0.0.1:8000").rstrip("/")
TOKEN = os.environ.get("MARKET_TOKEN", "")
MIN_USD = Decimal(os.environ.get("PIXELAGENT_MIN_USD", "0.30"))
PORT = os.environ.get("PIXELAGENT_PORT", "8020")
PUBLIC = os.environ.get("PIXELAGENT_PUBLIC_URL", f"http://127.0.0.1:{PORT}").rstrip("/")
STATE = Path(os.environ.get("PIXELAGENT_STATE", str(HERE / "data" / "pixelagent-state.json")))
_lock = threading.Lock()


def _wallet() -> str:
    from mpp.methods.tempo import TempoAccount

    return TempoAccount.from_key(KEY if KEY.startswith("0x") else "0x" + KEY).address


WALLET = _wallet()


def _load() -> dict:
    return json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {"jobs": {}}


def _save(s: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, indent=1), encoding="utf-8")
    tmp.replace(STATE)


def _job(job_id: str) -> dict | None:
    with _lock:
        return _load()["jobs"].get(job_id)


def _set(job_id: str, **fields) -> None:
    with _lock:
        s = _load()
        s["jobs"].setdefault(job_id, {}).update(fields)
        _save(s)


def decide(job: dict) -> str:
    """PixelAgent's own judgement: take only share_card work that covers its cost."""
    try:
        price = Decimal(str(job.get("price_usd")))
    except (InvalidOperation, ValueError):
        return "skip"
    return "take" if job.get("template") == "share_card" and price >= MIN_USD else "skip"


mpp_server = None  # built in the lifespan (its replay store is SQLite)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global mpp_server
    from mpp.methods.tempo import server as tempo
    from mpp.server import Mpp
    from mpp.stores import SQLiteStore

    STATE.parent.mkdir(parents=True, exist_ok=True)
    store = await SQLiteStore.create(str(STATE.parent / "pixelagent-mpp.db"))
    mpp_server = Mpp(methods=tempo.charge(recipient=WALLET, chain_id=CHAIN,
                                          currencies=[PATH_USD], store=store),
                     realm="pixelagent", secret_key=os.environ["MPP_SECRET_KEY"])
    if os.environ.get("PIXELAGENT_WORKER", "1") == "1":
        threading.Thread(target=_worker, daemon=True, name="pixelagent-worker").start()
    print(f"pixelagent: wallet {WALLET}, market {MARKET}, invoices at {PUBLIC}")
    yield


app = FastAPI(title="PixelAgent", lifespan=lifespan)


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
    if j.get("tx"):  # already paid: the same receipt, never a second charge
        return {"job_id": job_id, "paid": True, "tx": j["tx"], "amount_usd": j["price_usd"],
                "duplicate": True}

    # G2: the pay decorator calls the endpoint with (request, credential, receipt) only
    @pay(mpp_server.charge(amount=j["price_usd"], description=f"U-Tax job {job_id}"))
    async def _paid(request: Request, credential: Credential, receipt: Receipt):
        _set(job_id, tx=receipt.reference, paid_at=time.time())
        print(f"pixelagent: {job_id} paid {j['price_usd']} pathUSD, tx {receipt.reference}")
        return JSONResponse({"job_id": job_id, "paid": True, "tx": receipt.reference,
                             "amount_usd": j["price_usd"]})

    return await _paid(request)


def work_once(client: httpx.Client) -> int:
    done = 0
    r = client.get(f"{MARKET}/market/jobs", params={"status": "open"})
    r.raise_for_status()
    for job in r.json().get("jobs", []):
        jid = job["job_id"]
        if _job(jid):
            continue
        verdict = decide(job)
        print(f"pixelagent: {jid} at {job.get('price_usd')} pathUSD: {verdict} "
              f"(my minimum is {MIN_USD})")
        if verdict != "take":
            continue
        c = client.post(f"{MARKET}/market/jobs/{jid}/claim",
                        json={"wallet": WALLET, "invoice_url": f"{PUBLIC}/invoice/{jid}"})
        if c.status_code != 200:
            print(f"pixelagent: claim {jid} answered {c.status_code}")
            continue
        _set(jid, state="claimed", price_usd=job["price_usd"])
        out = STATE.parent / f"{jid}.png"
        headline = job["input"]["headline"]
        share_card.render(headline, str(out))
        if not share_card.passed(share_card.verify(out.read_bytes(), headline)):
            print(f"pixelagent: my own card for {jid} fails the checks; not submitted")
            continue
        # "submitted" BEFORE the upload: U-Tax may call the invoice the moment it verifies
        _set(jid, state="submitted")
        s = client.post(f"{MARKET}/market/jobs/{jid}/submit",
                        files={"file": (out.name, out.read_bytes(), "image/png")})
        if s.status_code == 200 and s.json().get("verified"):
            done += 1
        else:
            _set(jid, state="claimed")  # not accepted: nothing to invoice
        print(f"pixelagent: submitted {jid}: {s.status_code} {s.text[:120]}")
    return done


def _worker() -> None:
    with httpx.Client(headers={"Authorization": f"Bearer {TOKEN}"}, timeout=30) as client:
        while True:
            try:
                work_once(client)
            except Exception as exc:  # noqa: BLE001 - a market restart must not kill the agent
                print("pixelagent: poll failed:", type(exc).__name__, str(exc)[:120])
            time.sleep(float(os.environ.get("PIXELAGENT_POLL_SECONDS", "3")))
