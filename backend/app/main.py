"""U-Tax gateway (FastAPI). `uvicorn app.main:app --port 8000 --env-file .env`

One process, one writer (design): the gateway, its timer and the optional local crew all
live here. `create_app()` takes a ready Gateway for tests.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app import __version__
from app.api import HEARTBEAT_SECONDS, build_router
from app.mcp import build_mcp_router
from app.errors import install_error_handlers
from app.gateway.perform import Gateway

FRONTEND_ORIGIN = "http://localhost:3000"
TICK_SECONDS = 15.0
log = logging.getLogger("utax")


class Health(BaseModel):
    status: str
    version: str


def tick(gw: Gateway) -> None:
    """Background upkeep: CRM mirror, failed wakes, the 6 h stall sweep, the CEO digest."""
    for job in (gw.drain_outbox, gw.retry_wakes, gw.retry_questionnaires,
                gw.retry_signup_welcomes, gw.retry_candidate_invites,
                gw.retry_decision_mails, gw.advance_phone_jobs, gw.refresh_fee,
                gw.advance_market_jobs,  # D38: pays verified jobs (off: does nothing)
                gw.advance_orders,  # D41: the next CEO order task starts (or one times out)
                gw.mirror_payments,  # D41 B53: a test-mode Stripe record per contractor payment
                lambda: gw.perform("stall_sweep", {}),
                lambda: gw.perform("ceo_digest", {})):
        try:
            job()
        except Exception:  # noqa: BLE001 - upkeep never takes the server down
            log.exception("tick job failed")


def _show_utax_logs() -> None:
    """uvicorn configures only its own loggers: give "utax" an INFO handler so WAKE lines
    (and the other gateway notes) show in the server log."""
    utax = logging.getLogger("utax")
    if not any(getattr(h, "_utax", False) for h in utax.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s:     %(name)s %(message)s"))
        handler._utax = True  # type: ignore[attr-defined]
        utax.addHandler(handler)
    utax.setLevel(logging.INFO)


def _ensure_ceo_tool_token(gw: Gateway) -> None:
    """v1.14: UTAX_TOOL_TOKEN_CEO unset: generate one into backend/.env (never printed), so
    `naive up` can hand it to the ceo-assistant agent; the seat opens now."""
    if gw.settings.tool_tokens.get("ceo") or os.environ.get("UTAX_TOOL_TOKEN_CEO"):
        return
    import secrets
    from pathlib import Path

    from app.devtools import set_env_values

    token = secrets.token_urlsafe(36)
    os.environ["UTAX_TOOL_TOKEN_CEO"] = token
    gw.settings.tool_tokens["ceo"] = token
    try:
        set_env_values(Path(__file__).resolve().parents[1] / ".env",
                       {"UTAX_TOOL_TOKEN_CEO": token})
        log.info("UTAX_TOOL_TOKEN_CEO generated and saved to backend/.env")
    except OSError:
        log.exception("UTAX_TOOL_TOKEN_CEO not saved: the ceo seat's token changes at restart")


def _ensure_session_secret(gw: Gateway) -> None:
    """v1.13: UTAX_SESSION_SECRET unset at first boot: generate one, write it into
    backend/.env (never printed) and use it now, so sessions outlive a restart."""
    current = gw.settings.session_secret or os.environ.get("UTAX_SESSION_SECRET", "")
    if current:
        if len(current) < 32:  # fail at boot, never as a 500 on every theater read
            raise RuntimeError("UTAX_SESSION_SECRET must be at least 32 characters")
        return
    import secrets
    from pathlib import Path

    from app.devtools import set_env_values

    secret = secrets.token_urlsafe(48)
    os.environ["UTAX_SESSION_SECRET"] = secret
    try:
        set_env_values(Path(__file__).resolve().parents[1] / ".env",
                       {"UTAX_SESSION_SECRET": secret})
        log.info("UTAX_SESSION_SECRET generated and saved to backend/.env")
    except OSError:
        log.exception("UTAX_SESSION_SECRET not saved: CEO sessions end at the next restart")


def create_app(gateway: Gateway | None = None, *, start_background: bool = True) -> FastAPI:
    state: dict[str, object] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        gw = gateway
        if gw is None:
            from app.gateway.registry import build_gateway

            gw = build_gateway()
        state["gw"] = gw
        if start_background:
            _ensure_session_secret(gw)  # v1.13: the CEO's sessions need a key that persists
            _ensure_ceo_tool_token(gw)  # v1.14: the /mcp/ceo seat
        crew = None
        if gw.settings.driver == "local":
            from app.driver import LocalCrew

            crew = LocalCrew(gw, pace=gw.settings.driver_pace_seconds).start()
        state["crew"] = crew
        waker = unwake = None
        if start_background and gw.settings.naive_api_key and not gw.settings.driver:
            # §6b: the real Chief of Staff in Naïve gets every wake (before the boot wakes)
            from app.naive_waker import NaiveWaker

            _show_utax_logs()
            from app.naive.backoff import SHARED

            waker = NaiveWaker(gw.settings.naive_api_key, gw.settings.naive_api_base,
                               trigger_of=gw.wake_trigger, backoff=SHARED)  # task 35
            unwake = gw.on_wake(waker)
            log.info("WAKE: the Naïve Chief of Staff receives the gateway's wakes")
        state["waker"] = waker
        task = None
        if start_background:
            await asyncio.to_thread(gw.boot_wakes)  # wakes live in memory: re-derive them
            # v1.7 §6f: warm the fee cache now, so compute_tax never fetches under the lock
            try:
                await asyncio.to_thread(gw.refresh_fee, force=True)
            except Exception:  # noqa: BLE001 - the snapshot serves until the tick retries
                log.exception("fee warm-up failed at boot")

            async def loop() -> None:
                while True:
                    await asyncio.sleep(TICK_SECONDS)
                    await asyncio.to_thread(tick, gw)

            task = asyncio.create_task(loop())
        confirmer = confirmer_task = None
        if start_background and gw.settings.naive_api_key and gw.settings.naive_confirm:
            # v1.9 §6b-bis (D29): the gateway answers Naïve's data-leak confirms
            from app.naive import NaiveClient
            from app.naive_confirm import NaiveConfirmer

            from app.naive.backoff import SHARED

            confirmer = NaiveClient(gw.settings.naive_api_key, gw.settings.naive_api_base,
                                    backoff=SHARED)  # task 35: one gate with the waker
            resolver = NaiveConfirmer(gw, confirmer)
            from app.naive_activity import ActivityRelay

            relay = ActivityRelay(gw, confirmer)  # task 42 (D37): live agent activity
            resolver.on_rows = relay.observe
            state["activity"] = relay
            state["confirmer"] = resolver  # v1.14: /api/ceo/talk answers its confirms at once
            confirmer_task = asyncio.create_task(resolver.run())
            from app.ceo_talk import CeoTalk

            gw.ceo_talk = CeoTalk(gw, confirmer, confirmer=resolver)  # v1.14 (D34)
        try:
            yield
        finally:
            for running in (task, confirmer_task):
                if running:
                    running.cancel()
                    with suppress(asyncio.CancelledError):
                        await running
            if confirmer:
                await confirmer.aclose()
            if unwake:
                unwake()
            if waker:
                waker.close()
            if crew:
                crew.stop()

    app = FastAPI(title="U-Tax gateway", version=__version__, lifespan=lifespan)
    app.state.sse_heartbeat = float(os.environ.get("UTAX_SSE_HEARTBEAT") or HEARTBEAT_SECONDS)
    # THEATER_ORIGIN may list several origins: "http://localhost:3200,https://u-tax.vercel.app"
    origins = [FRONTEND_ORIGIN] + [o.strip().rstrip("/") for o in
                                   os.environ.get("THEATER_ORIGIN", "").split(",") if o.strip()]
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["*"],
                       allow_headers=["*"])
    install_error_handlers(app)

    @app.get("/api/health", response_model=Health)
    async def health() -> Health:  # async: never waits behind a busy thread pool
        return Health(status="ok", version=__version__)

    app.include_router(build_router(lambda: state["gw"]))  # type: ignore[arg-type,return-value]
    app.include_router(build_mcp_router(lambda: state["gw"]))  # type: ignore[arg-type,return-value]
    from app.market_api import build_market_router  # D38: /market/* (off: 404 market_off)

    app.include_router(build_market_router(lambda: state["gw"]))  # type: ignore[arg-type,return-value]
    from app.sell_api import build_sell_router  # D38 §9.4: /agent/* (off: 404 sell_off)

    app.include_router(build_sell_router(lambda: state["gw"]))  # type: ignore[arg-type,return-value]
    app.state.utax = state
    return app


app = create_app()
