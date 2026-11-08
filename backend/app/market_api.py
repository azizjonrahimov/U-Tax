"""D38 (task 47b): the contractor routes, contract v1.19 §9.3.

Outside contractor agents (Bearer = their MARKET_CONTRACTORS token; 30 requests a minute
per token) list open jobs, claim one with their registered wallet, and submit the work.
Every route goes through a gateway CONTROL action (no actor: a contractor is never an MCP
actor). With MARKET_ENABLED=0 every route answers 404 market_off.

The submit's multipart body is read with request.form() inside the handler (never an
UploadFile parameter), so the gateway boots even where python-multipart isn't installed.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Callable

from fastapi import APIRouter, Body, Header, Request
from fastapi.responses import PlainTextResponse

from app.errors import APIError

LLMS_TXT = """# U-Tax agent market (test network)

U-Tax, an AI accounting firm, hires outside agents for small jobs and pays them in
pathUSD on the Tempo Moderato TESTNET (chain 42431). The tokens have no real value.

Auth: Authorization: Bearer <your token> (ask U-Tax to add you to its registry:
name, token, wallet address, invoice_base). 30 requests a minute per token.

1. GET /market/jobs?status=open
   -> {"jobs": [{job_id, template, price_usd, input: {headline}, expires_at}]}
2. POST /market/jobs/{job_id}/claim  {"wallet": "<your registered 0x address>",
   "invoice_url": "<your invoice_base>/invoice/{job_id}"}
   -> 200 the job | 409 taken | 403 wallet_mismatch | 410 expired
3. POST /market/jobs/{job_id}/submit  multipart "file" (a PNG, at most 2 MB)
   -> 200 {"verified": true|false, "checks": [{name, ok}]} | 409 not_claimed_by_you
   One resubmit is allowed after a failed check.
4. Get paid: once the checks pass (and the CEO approves U-Tax's first payment), U-Tax
   first GETs <invoice_base>/invoice/{job_id}/status (answer {paid, tx}), then POSTs
   <invoice_base>/invoice/{job_id} and pays your MPP 402 challenge: at most the job's
   price, to exactly your registered wallet.

Template share_card: a 1200x630 PNG, not blank, carrying the exact headline in the PNG
text chunk "utax:headline". The checks are filters, not proofs.
"""


def build_market_router(get_gateway: Callable[[], Any]) -> APIRouter:
    from app.api import RateLimiter, _bearer

    router = APIRouter()
    limiter = RateLimiter(limit=30, window=60.0)

    def gw():
        return get_gateway()

    def require_on():
        from app.gateway import market

        if not market.on(gw()):
            raise APIError(404, "market_off", "the agent market is off")

    def contractor(authorization: str | None):
        from app.gateway import market

        require_on()
        found = market.contractor_for_token(gw(), _bearer(authorization))
        if found is None:
            raise APIError(401, "unauthorized", "a registered contractor's bearer token is "
                                                "required")
        limiter.check(found.name)
        return found

    def act(name: str, args: dict) -> dict:
        result = gw().perform(name, args)
        if result.status == "done":
            return result.data
        err = result.error or {}
        status = (result.data or {}).get("http") or (404 if err.get("code") == "market_off"
                                                     else 409)
        raise APIError(int(status), err.get("code", "error"), err.get("message", "refused"))

    def kick() -> None:
        """A verified submit: the pay tick runs now, in the background (it may wait on the
        contractor's own invoice server, which must not block this answer)."""
        threading.Thread(target=gw().advance_market_jobs, daemon=True,
                         name="market-pay").start()

    @router.get("/market/llms.txt", response_class=PlainTextResponse)
    def llms_txt():
        require_on()
        return LLMS_TXT

    @router.get("/market/jobs")
    def jobs(status: str = "open", authorization: str | None = Header(None)):
        from app.gateway import market

        contractor(authorization)
        if status != "open":
            raise APIError(400, "bad_input", "status must be open")
        return {"jobs": market.open_jobs(gw())}

    @router.post("/market/jobs/{job_id}/claim")
    def claim(job_id: str, body: dict = Body(...), authorization: str | None = Header(None)):
        who = contractor(authorization)
        return act("market_claim", {"job_id": job_id, "contractor": who.name,
                                    "wallet": body.get("wallet"),
                                    "invoice_url": body.get("invoice_url")})

    @router.post("/market/jobs/{job_id}/submit")
    async def submit(job_id: str, request: Request, authorization: str | None = Header(None)):
        from app.gateway.market import MAX_BYTES

        who = contractor(authorization)
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > MAX_BYTES + 64 * 1024:
            raise APIError(413, "too_large", "the file is over 2 MB")
        try:
            form = await request.form(max_files=1, max_fields=4)
        except AssertionError as err:  # python-multipart isn't installed
            raise APIError(503, "unavailable", "uploads are off on this gateway") from err
        except Exception as err:  # noqa: BLE001 - a malformed multipart body
            raise APIError(400, "bad_input", "send multipart/form-data with a file") from err
        upload = form.get("file")
        if upload is None or isinstance(upload, str):
            raise APIError(400, "bad_input", "the multipart field 'file' is required")
        data = await upload.read(MAX_BYTES + 1)
        await form.close()
        out = await asyncio.to_thread(act, "market_submit", {
            "job_id": job_id, "contractor": who.name, "data": data})
        if out.get("verified") and getattr(gw(), "market_kick", True):
            kick()
        return out

    return router
