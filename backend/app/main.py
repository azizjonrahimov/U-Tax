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
                gw.retry_decision_mails, gw.advance_phone_jobs, gw.refresh_fee,
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


def create_app(gateway: Gateway | None = None, *, start_background: bool = True) -> FastAPI:
    state: dict[str, object] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        gw = gateway
        if gw is None:
            from app.gateway.registry import build_gateway

            gw = build_gateway()
        state["gw"] = gw
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
            waker = NaiveWaker(gw.settings.naive_api_key, gw.settings.naive_api_base,
                               trigger_of=gw.wake_trigger)
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

            confirmer = NaiveClient(gw.settings.naive_api_key, gw.settings.naive_api_base)
            confirmer_task = asyncio.create_task(NaiveConfirmer(gw, confirmer).run())
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
    app.state.utax = state
    return app


app = create_app()
