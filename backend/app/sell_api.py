"""D38 (task 47d): the sell side, contract v1.19 §9.4. Other agents pay U-Tax 0.50 pathUSD
(Tempo Moderato testnet) for a Delaware franchise-tax check.

- GET /agent/llms.txt: what U-Tax sells to agents, the price and the input schema.
- POST /agent/franchise-check {authorized_shares, issued_shares, gross_assets_cents,
  stated_par?}: the input is checked and the answer computed by app/engine FIRST (bad input
  -> 422, never a challenge: never charge for bad input), then an MPP 402 challenge pays
  SELL_PRICE_USD to the firm's own wallet; once paid: the answer + `agent.sale`.
- No case, no email, no PII: the payer is shown masked (0x12…ab).
- On only with MARKET_ENABLED=1, SELL_ENABLED=1, ADAPTER_WALLET=tempo (the firm's wallet
  receives) and MPP_SECRET_KEY; otherwise 404 sell_off. The replay store is SQLite.
- STRIPE_PROFILE_ID (Stripe-recorded payments) isn't wired: the runbook marks it UNVERIFIED
  and its fallback is this plain Tempo payment to the firm's wallet.
"""

# no `from __future__ import annotations`: the MPP pay decorator and FastAPI read real types
import asyncio
import logging
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from app.errors import APIError

log = logging.getLogger("utax.sell")
NOTE = "Educational estimate, not tax advice"

LLMS_TXT = """# U-Tax for agents (test network)

U-Tax checks a Delaware corporation's 2026 franchise tax both ways (the authorized
shares method and the assumed par value capital method) and answers the lower one.
Price: {price} pathUSD on the Tempo Moderato TESTNET (chain 42431), paid by MPP (HTTP
402). The tokens have no real value. Educational estimate, not tax advice.

POST /agent/franchise-check  (JSON)
  authorized_shares   integer >= 1
  issued_shares       integer >= 1
  gross_assets_cents  integer >= 0   (total gross assets, in cents)
  stated_par          optional decimal string (default "0.00001")
-> 402 Payment challenge (WWW-Authenticate: Payment ...); pay it and retry
-> 200 {{authorized_method_cents, assumed_par_method_cents, total_cents, due_date, note,
        receipt_tx}}
Bad input answers 422 before any challenge: you're never charged for it.
"""


def _check(body: Any) -> dict:
    """The engine's own validation, before any challenge: the computed answer, or 422."""
    from app.engine.errors import EngineError
    from app.engine.franchise import DEFAULT_STATED_PAR, compute_franchise_tax

    if not isinstance(body, dict):
        raise APIError(422, "bad_input", "send a JSON object")
    fields = {}
    for name, minimum in (("authorized_shares", 1), ("issued_shares", 1),
                          ("gross_assets_cents", 0)):
        value = body.get(name)
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise APIError(422, "bad_input", f"{name} must be a whole number >= {minimum}")
        fields[name] = value
    par = body.get("stated_par")
    try:
        stated = DEFAULT_STATED_PAR if par in (None, "") else Decimal(str(par))
    except (InvalidOperation, ValueError) as err:
        raise APIError(422, "bad_input", "stated_par must be a decimal") from err
    if not stated.is_finite() or stated <= 0:
        raise APIError(422, "bad_input", "stated_par must be above 0")
    return {**fields, "stated_par": stated}


def build_sell_router(get_gateway: Callable[[], Any]) -> APIRouter:
    router = APIRouter()
    state: dict[str, Any] = {}
    lock = asyncio.Lock()

    def gw():
        return get_gateway()

    def on() -> bool:
        g = gw()
        s = g.settings
        return bool(s.market_enabled and s.sell_enabled and s.mpp_secret_key
                    and getattr(g.ports.wallet, "provider", None) == "tempo")

    def require_on():
        if not on():
            raise APIError(404, "sell_off", "U-Tax isn't selling to agents right now")

    async def server():
        """The MPP server, built once (its SQLite replay store needs the event loop)."""
        async with lock:
            if "mpp" not in state:
                from mpp.methods.tempo import server as tempo
                from mpp.server import Mpp
                from mpp.stores import SQLiteStore

                from app.gateway.adapters.tempo_wallet import PATH_USD, TESTNET_CHAIN_ID

                g = gw()
                g.settings.data_dir.mkdir(parents=True, exist_ok=True)
                store = await SQLiteStore.create(str(g.settings.data_dir / "sell-mpp.db"))
                state["mpp"] = Mpp(methods=tempo.charge(
                    recipient=g.ports.wallet.address, chain_id=TESTNET_CHAIN_ID,
                    currencies=[PATH_USD], store=store),
                    realm="u-tax", secret_key=g.settings.mpp_secret_key)
            return state["mpp"]

    @router.get("/agent/llms.txt", response_class=PlainTextResponse)
    def llms_txt():
        require_on()
        price = format(gw().settings.sell_price_usd.quantize(Decimal("0.01")), "f")
        return LLMS_TXT.format(price=price)

    @router.post("/agent/franchise-check")
    async def franchise_check(request: Request):
        from mpp import Credential, Receipt
        from mpp.server import pay

        from app.engine.franchise import compute_franchise_tax

        require_on()
        try:
            body = await request.json()
        except ValueError as err:
            raise APIError(422, "bad_input", "send a JSON object") from err
        facts = _check(body)
        g = gw()
        fee = await asyncio.to_thread(g._fee)
        try:
            result = compute_franchise_tax(annual_report_fee=fee, **facts)
        except Exception as err:  # noqa: BLE001 - the engine's own refusal: still never charged
            raise APIError(422, "bad_input", str(err)[:200]) from err
        price = format(g.settings.sell_price_usd.quantize(Decimal("0.01")), "f")
        mpp = await server()

        # G2: the pay decorator calls the endpoint with (request, credential, receipt) only
        @pay(mpp.charge(amount=price, description="U-Tax Delaware franchise-tax check"))
        async def _paid(request: Request, credential: Credential, receipt: Receipt):
            tx = str(receipt.reference or "")
            payer = str(getattr(receipt, "payer", None) or getattr(credential, "source", "")
                        or "")
            masked = f"{payer[:4]}…{payer[-2:]}" if payer.startswith("0x") and len(payer) > 8 \
                else "agent"
            await asyncio.to_thread(g.record_agent_sale, price, tx, masked)
            return JSONResponse({
                "authorized_method_cents": result.authorized_method_cents,
                "assumed_par_method_cents": result.assumed_par_method_cents,
                "total_cents": result.total_cents, "due_date": result.due_date,
                "note": NOTE, "receipt_tx": tx})

        return await _paid(request)

    return router
