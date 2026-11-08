"""D38 (task 47b): the agent market inside the gateway. Contract v1.19 §9.

Marketing hires (`hire_contractor`), an outside contractor agent claims and submits through
/market/* (app/market_api.py), the gateway checks the work IN CODE (share_card.verify), and
only then pays the contractor's MPP 402 invoice from the firm's wallet, from its own tick.

Money-safe, like buy_tool:
- The trust gate first (`contractor_pay`: ASK until the CEO's first approval, then AUTO;
  over MARKET_MAX_JOB_USD or past MARKET_MAX_DAY_USD always asks).
- An intent is written (fsync'd) before any payment; the log is the memory. A job whose
  intent is already there may have been paid: the contractor's status and the chain
  (`tx_ok`) are checked first, and the gateway never pays blind while it can't tell.
- The wallet pays at most the job's price, to exactly the registry wallet.
- No agent ever holds a key or moves money; contractors are never MCP actors.

Everything here is refused while MARKET_ENABLED is off.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
from datetime import timedelta
from decimal import Decimal
from typing import Any

import httpx

from app.gateway.events import NewEvent, iso_ts, parse_ts
from app.gateway.ports import PaidInvoice, PortError, Unsure

log = logging.getLogger("utax.market")

TEMPLATES = ("share_card",)
FINAL = ("delivered", "failed", "expired")
MAX_BYTES = 2 * 1024 * 1024
PAY_TRIES = 3
WAIT_STEPS, WAIT_STEP = 40, 0.5  # get_job waits up to ~20 s for a change (agents have no clock)


class MarketRefused(Exception):
    """A contractor route's refusal: HTTP status + code (app/market_api.py)."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def _price(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.01")), "f")


def on(gw: Any) -> bool:
    return bool(gw.settings.market_enabled) and gw.ports.wallet is not None


def _require_on(gw: Any) -> None:
    from app.gateway.perform import Refused

    if not on(gw):
        raise Refused("market_off", "the agent market is off (MARKET_ENABLED=0)")


def contractors(gw: Any) -> dict:
    return {c.name: c for c in gw.settings.market_contractors}


def contractor_for_token(gw: Any, token: str | None):
    import hmac

    if not token:
        return None
    found = None
    for c in gw.settings.market_contractors:  # constant time against every token
        if hmac.compare_digest(c.token.encode(), token.encode()):
            found = c
    return found


def _expired(gw: Any, job: dict) -> bool:
    return bool(job.get("expires_at")) and gw._now() >= parse_ts(job["expires_at"])


# ------------------------------------------------------------------ the agent tools


def hire(gw: Any, args: dict) -> dict:
    """hire_contractor (mkt): post a job. The price is the gateway's (MARKET_PRICE_USD)."""
    from app.gateway.perform import Refused, _one_of, _str, post_problem

    _require_on(gw)
    template = _one_of(args, "template", TEMPLATES)
    headline = " ".join(_str(args, "headline", max_len=400).split())
    if not headline or len(headline) > gw.AD_HEADLINE_MAX:
        raise Refused("bad_input", f"headline must be 1-{gw.AD_HEADLINE_MAX} characters")
    problem = post_problem(headline, allowed_url=gw.settings.public_talk_url) \
        or gw._names_a_client(headline)
    if problem:
        raise Refused("content_guard", f"headline refused: {problem}")
    key = _str(args, "key", max_len=120)
    done = gw._done_from(f"job:{key}")
    if done is not None:
        job = gw.state.jobs.get(done.payload["job_id"], {})
        return {"job_id": done.payload["job_id"], "status": job.get("status", "open"),
                "price_usd": done.payload["price_usd"], "duplicate": True}
    job_id = f"job_{len(gw.state.jobs) + 1}"
    price = _price(gw.settings.market_price_usd)
    expires = iso_ts(gw._now() + timedelta(minutes=gw.settings.market_job_ttl_minutes))
    gw._emit([NewEvent(type="job.posted", actor="mkt",
                       payload={"job_id": job_id, "template": template, "price_usd": price,
                                "headline": headline},
                       idempotency_key=f"job:{key}", private={"expires_at": expires})])
    return {"job_id": job_id, "status": "open", "price_usd": price}


def view(gw: Any, job_id: str) -> dict | None:
    """get_job's answer (read under the lock): never the artifact or a private field."""
    job = gw.state.jobs.get(job_id)
    if job is None:
        return None
    out: dict[str, Any] = {"job_id": job_id, "status": job["status"]}
    for field in ("contractor", "image_url", "tx_hash", "explorer_url", "checks"):
        if job.get(field):
            out[field] = job[field]
    approval_id = gw.state.approval_by_key.get(f"pay:{job_id}")
    if approval_id and gw.state.approvals[approval_id].decision is None \
            and job["status"] == "verified":
        out["approval_id"] = approval_id  # the payment waits on the CEO
    return out


def get_job(gw: Any, args: dict) -> dict:
    from app.gateway.perform import Refused, _str

    _require_on(gw)
    job_id = _str(args, "job_id", max_len=40)
    out = view(gw, job_id)
    if out is None:
        raise Refused("not_found", f"no job {job_id}")
    return out


def wait_job(gw: Any, args: dict) -> dict:
    """get_job's slow read, BEFORE the lock: while the job isn't final, wait up to ~20 s
    for its status (or its pending approval) to change."""
    job_id = args.get("job_id")
    if not isinstance(job_id, str) or not on(gw):
        return {}
    first = None
    for step in range(WAIT_STEPS):
        with gw._lock:
            now = view(gw, job_id)
        if now is None or now["status"] in FINAL:
            break
        mark = (now["status"], now.get("approval_id"))
        if first is None:
            first = mark
        elif mark != first:
            break
        if step < WAIT_STEPS - 1:
            gw.sleep(WAIT_STEP)
    return {}


# ------------------------------------------------------------------ the contractor routes


def open_jobs(gw: Any) -> list[dict]:
    with gw._lock:
        return [{"job_id": j["job_id"], "template": j["template"], "price_usd": j["price_usd"],
                 "input": {"headline": j["headline"]}, "expires_at": j.get("expires_at")}
                for j in gw.state.jobs.values()
                if j["status"] == "open" and not _expired(gw, j)]


def claim(gw: Any, args: dict) -> dict:
    """market_claim (control action, no actor): a registered contractor takes a job."""
    _require_on(gw)
    job = gw.state.jobs.get(str(args.get("job_id")))
    c = contractors(gw).get(str(args.get("contractor")))
    if job is None or c is None:
        raise MarketRefused(404, "not_found", "no such job")
    if _expired(gw, job) or job["status"] == "expired":
        raise MarketRefused(410, "expired", "the job expired")
    if job["status"] != "open":
        raise MarketRefused(409, "taken", "the job is taken")
    wallet, url = str(args.get("wallet") or ""), str(args.get("invoice_url") or "")
    if wallet.lower() != c.wallet.lower() or not url.startswith(c.invoice_base + "/"):
        raise MarketRefused(403, "wallet_mismatch", "the wallet must be your registered one "
                                                    "and invoice_url start with your "
                                                    "registered invoice_base")
    gw._emit([NewEvent(type="job.claimed", actor="system",
                       payload={"job_id": job["job_id"], "contractor": c.name},
                       private={"wallet": wallet, "invoice_url": url})])
    return {"job_id": job["job_id"], "template": job["template"],
            "price_usd": job["price_usd"], "input": {"headline": job["headline"]},
            "expires_at": job.get("expires_at"), "status": "claimed"}


def submit(gw: Any, args: dict) -> dict:
    """market_submit: the contractor's work, checked in code. One resubmit after a fail."""
    from app.gateway import share_card

    _require_on(gw)
    job = gw.state.jobs.get(str(args.get("job_id")))
    contractor = str(args.get("contractor"))
    data = args.get("data")
    if job is None:
        raise MarketRefused(404, "not_found", "no such job")
    if job["status"] != "claimed" or job.get("contractor") != contractor:
        raise MarketRefused(409, "not_claimed_by_you", "this job isn't claimed by you (or "
                                                       "it was already submitted)")
    if _expired(gw, job):
        raise MarketRefused(410, "expired", "the job expired")
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise MarketRefused(400, "bad_input", "send the file")
    if len(data) > MAX_BYTES:
        raise MarketRefused(413, "too_large", "the file is over 2 MB")
    job_id = job["job_id"]
    folder = gw.settings.data_dir / "market"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{job_id}.png").write_bytes(bytes(data))  # private until paid
    sha = hashlib.sha256(data).hexdigest()
    checks = share_card.verify(bytes(data), job["headline"])
    ok = share_card.passed(checks)
    gw._emit([
        NewEvent(type="job.submitted", actor="system",
                 payload={"job_id": job_id, "contractor": contractor, "sha256": sha}),
        NewEvent(type="job.verified", actor="system",
                 payload={"job_id": job_id, "checks": checks}) if ok else
        NewEvent(type="job.failed", actor="system",
                 payload={"job_id": job_id, "checks": checks, "reason": "checks_failed"}),
    ])
    out = {"verified": ok, "checks": checks}
    if not ok:
        out["resubmit"] = gw.state.jobs[job_id]["status"] == "claimed"
    return out


# ------------------------------------------------------------------ the pay tick


def _http(gw: Any) -> httpx.Client:
    client = getattr(gw, "market_http", None)
    if client is None:
        client = gw.market_http = httpx.Client(timeout=10.0,
                                               headers={"User-Agent": "utax-gateway"})
    return client


def _contractor_status(gw: Any, c, job_id: str) -> dict | None:
    """The contractor's free GET /invoice/{id}/status: {paid, tx, ...}; {} unknown job;
    None unreachable (the gateway can't tell: it never pays blind then)."""
    try:
        r = _http(gw).get(f"{c.invoice_base}/invoice/{job_id}/status")
    except httpx.HTTPError:
        return None
    if r.status_code == 404:
        return {}
    if not r.is_success:
        return None
    try:
        body = r.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def _payment_event(gw: Any, job: dict, c, paid: PaidInvoice | None, *, ok: bool,
                   key: str | None) -> NewEvent:
    wallet = gw.ports.wallet
    return NewEvent(type="payment.sent", actor="system", provider=wallet.provider,
                    status="ok" if ok else "failed",
                    payload={"job_id": job["job_id"], "contractor": c.name,
                             "amount_usd": job["price_usd"], "token": wallet.token,
                             "tx_hash": paid.tx_hash if paid else "",
                             "explorer_url": paid.explorer_url if paid else ""},
                    cost_usd=job["price_usd"] if ok else None, idempotency_key=key,
                    private=None if ok else {"unsure": True})


def _fail(gw: Any, job: dict, reason: str) -> None:
    with gw._lock:
        gw._emit([NewEvent(type="job.failed", actor="system",
                           payload={"job_id": job["job_id"], "checks": job.get("checks") or [],
                                    "reason": reason})])


def expire(gw: Any) -> list[str]:
    with gw._lock:
        stale = [j for j in gw.state.jobs.values()
                 if j["status"] in ("open", "claimed") and _expired(gw, j)]
        if stale:
            gw._emit([NewEvent(type="job.failed", actor="system",
                               payload={"job_id": j["job_id"], "checks": j.get("checks") or [],
                                        "reason": "expired"}) for j in stale])
    return [j["job_id"] for j in stale]


def advance(gw: Any) -> list[str]:
    """The tick (and right after a verified submit): expire stale jobs, then pay each
    verified one. Returns the jobs delivered."""
    if not on(gw) or not gw._market_tick.acquire(blocking=False):
        return []
    try:
        expire(gw)
        with gw._lock:
            verified = [dict(j) for j in gw.state.jobs.values() if j["status"] == "verified"]
        delivered = []
        for job in verified:
            try:
                if pay_one(gw, job):
                    delivered.append(job["job_id"])
            except Exception:  # noqa: BLE001 - one job never stops the others
                log.exception("market: paying %s failed", job["job_id"])
        return delivered
    finally:
        gw._market_tick.release()
        with gw._lock:
            wakes, gw._market_wakes = gw._market_wakes, []
        gw._deliver_wakes(wakes)


def pay_one(gw: Any, job: dict) -> bool:
    from app.gateway.perform import NeedsApproval, Refused
    from app.gateway.ports import Rejected, Unavailable
    from app.gateway.ports.store import EventWriteError

    job_id, key = job["job_id"], f"pay:{job['job_id']}"
    c = contractors(gw).get(job.get("contractor") or "")
    if c is None:
        _fail(gw, job, "contractor_error")  # removed from the registry: nobody to pay
        return False
    price = Decimal(job["price_usd"])
    s = gw.settings
    with gw._lock:
        if gw._done_from(key) is not None:  # paid, delivery not written: finish it
            return _deliver(gw, job_id)
        day = iso_ts(gw._now())[:10]
        today = gw.state.contractor_paid_by_day.get(day, Decimal(0))
        always = price > s.market_max_job_usd or today + price > s.market_max_day_usd
        summary = (f"{gw._first('contractor_pay')}contractor payment: {c.name} "
                   f"{job['price_usd']} {gw.ports.wallet.token} for {job_id}")
        try:
            gw._gate("contractor_pay", key, summary, None, always_ask=always)
        except NeedsApproval:
            return False  # the CEO's approval is the training touch; get_job shows it
        except Refused:
            return False  # paused or denied: never paid
        try:
            gw.store.ensure_writable()
        except EventWriteError:
            return False  # the payment couldn't be recorded: never pay
        intent = gw._pay_intents.get(key)
        had_intent = intent is not None
        if not had_intent:
            intent = {"job_id": job_id, "contractor": c.name, "price_usd": job["price_usd"],
                      "recipient": c.wallet, "tries": 0, "ts": iso_ts(gw._now())}
            gw._pay_intents.put(key, intent)  # durable before anything is paid
    wallet = gw.ports.wallet
    status = _contractor_status(gw, c, job_id)  # ask the contractor first
    paid = None
    if status and status.get("paid") and status.get("tx"):
        try:
            ok = wallet.tx_ok(str(status["tx"]))
        except PortError:
            return False  # can't tell yet: the next tick looks again
        if ok is None:
            return False  # not mined yet
        if ok:
            tx = str(status["tx"])
            paid = PaidInvoice(tx_hash=tx, amount_usd=job["price_usd"],
                               explorer_url=getattr(wallet, "explorer_url", lambda t: "")(tx),
                               paid=wallet.provider != "local")
    if paid is None:
        if had_intent and status is None:
            return False  # it may have been paid and the contractor can't say: wait
        try:
            paid = wallet.pay_invoice(f"{c.invoice_base}/invoice/{job_id}",
                                      max_usd=job["price_usd"], recipient=c.wallet)
        except Unsure as err:
            log.warning("market: payment for %s unsure: %s", job_id, err)
            with gw._lock:
                tries = int(intent.get("tries", 0)) + 1
                gw._pay_intents.put(key, {**intent, "tries": tries, "unsure": True})
                gw._emit([_payment_event(gw, job, c, None, ok=False,
                                         key=f"pay:unsure:{job_id}:{tries}")])
            return False
        except (Rejected, Unavailable) as err:
            log.warning("market: payment for %s refused or failed: %s", job_id, err)
            with gw._lock:
                tries = int(intent.get("tries", 0)) + 1
                if tries >= PAY_TRIES:
                    gw._pay_intents.put(key, None)
                    gw._emit([NewEvent(type="job.failed", actor="system",
                                       payload={"job_id": job_id,
                                                "checks": job.get("checks") or [],
                                                "reason": "contractor_error"})])
                else:
                    gw._pay_intents.put(key, {**intent, "tries": tries})
            return False
    with gw._lock:
        gw._emit([_payment_event(gw, job, c, paid, ok=True, key=key),
                  NewEvent(type="cost.booked", actor="system", provider=wallet.provider,
                           payload={"provider": wallet.provider,
                                    "model": f"contractor:{job['template']}",
                                    "estimated": False},
                           cost_usd=job["price_usd"], idempotency_key=f"cost:{key}")])
        gw._pay_intents.put(key, None)
        return _deliver(gw, job_id)


def _deliver(gw: Any, job_id: str) -> bool:
    """Under the lock: the paid card becomes the firm's asset ad_<n>.png (what post_social
    accepts, G7), job.delivered, and the Chief of Staff is woken to have it posted."""
    if gw.state.jobs[job_id]["status"] == "delivered":
        return True
    source = gw.settings.data_dir / "market" / f"{job_id}.png"
    if not source.is_file():
        log.error("market: %s is paid but its file is gone", job_id)
        return False
    number = gw.state.ads_created + 1
    name = f"ad_{number}.png"
    target = gw.settings.data_dir / "assets" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    image_url = f"{gw.settings.public_url}/assets/{name}"
    gw._emit([NewEvent(type="job.delivered", actor="system",
                       payload={"job_id": job_id, "image_url": image_url},
                       idempotency_key=f"delivered:{job_id}")])
    gw._market_wakes.append(f"Job {job_id} delivered: Marketing posts it with "
                            f"post_social, image_url {image_url}")  # sent off the lock
    return True
