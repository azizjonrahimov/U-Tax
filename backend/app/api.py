"""HTTP surface (contract §4 theater, §5 control). Every write goes through perform().

Auth (contract §1): theater token as `?token=` on reads, admin token as a Bearer on
control routes; an empty configured token disables that check (local dev only). The
public `/talk` routes (customer voice, voice session) are rate-limited per IP instead.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import re
import secrets
import threading
import time
from datetime import datetime, timezone
from collections import defaultdict, deque
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Body, Header, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

from app.auth import Sessions, verify_password
from app.errors import APIError
from app.gateway.events import Event, iso_ts
from app.gateway.perform import Gateway, ToolResult
from app.gateway.ports import NotConfigured, PortError

log = logging.getLogger("utax.api")

HEARTBEAT_SECONDS = 15.0
VOICE_MAX_BYTES = 1_000_000  # task 13: POST /api/voice body cap (200 turns of CJK fit)
# (per-IP, global) per minute, keyed by PUBLIC_MODE == "event"
MINT_LIMITS = {False: (20, 120), True: (60, 360)}  # POST /api/voice/session (mints)
SEND_LIMITS = {False: (30, 180), True: (90, 540)}  # POST /api/voice (customer Send)
# task 13: Sends the gateway turns away cheaply (bad input, a retried request_id) give back
# their Send hit, up to this many a minute per IP; past it they count like any Send
FREE_LIMITS = {False: (30, None), True: (90, None)}  # = Send: one IP's global share x2
SSE_QUEUE_MAX = 2000

# refused codes -> HTTP status (everything else refused is 409)
_REFUSED_STATUS = {
    "not_found": 404, "bad_input": 400, "unknown_action": 404, "not_local": 403,
    "tool_not_allowed": 403, "wrong_status": 409, "paused": 409, "denied": 409, "superseded": 409,
    "qa_gate": 409, "at_capacity": 429, "too_long": 400, "rate_limited": 429,
    # v1.8 §5b: the CEO console
    "unknown_tool": 400, "bad_args": 400, "not_a_confirm": 400, "nothing_pending": 409,
    "used": 409, "expired": 409, "flagged": 409, "console_only": 409,
}
_ERROR_STATUS = {"store_unavailable": 503, "internal_error": 500}
# refusal data the contract puts in the HTTP error object (the rest stays agent-only)
_REFUSED_DETAILS = {"superseded": ("approval_id", "superseded_by")}  # v1.7.8


def result_or_raise(result: ToolResult) -> dict[str, Any]:
    """Map a perform() result onto the contract's HTTP shapes."""
    if result.status == "done":
        return {"ok": True, **result.data}
    if result.status == "needs_approval":
        return {"ok": True, "status": "needs_approval", "approval_id": result.approval_id,
                **result.data}
    err = result.error or {"code": "error", "message": "failed"}
    if result.status == "refused":
        named = _REFUSED_DETAILS.get(err["code"], ())
        details = {k: (result.data or {}).get(k) for k in named}
        raise APIError(_REFUSED_STATUS.get(err["code"], 409), err["code"], err["message"],
                       details)
    raise APIError(_ERROR_STATUS.get(err["code"], 502), err["code"], err["message"])


class RateLimiter:
    """Sliding window per key (client IP): `limit` calls per `window` seconds, plus a
    `global_limit` across all keys, so forged or rotating IPs can't multiply the budget."""

    def __init__(self, limit: int, window: float = 60.0, global_limit: int | None = None) -> None:
        self.limit, self.window, self.global_limit = limit, window, global_limit
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._all: deque[float] = deque()
        self._lock = threading.Lock()

    def refund(self, key: str, stamp: float) -> None:
        """Task 13: give back the per-key hit that `check` counted (a retried request_id, bad
        input). The global count keeps it: free requests from rotating IPs still stop at
        `global_limit`, so nothing reaches the gateway lock unbounded."""
        with self._lock:
            hits = self._hits.get(key)
            if hits is not None and stamp in hits:
                hits.remove(stamp)

    def check(self, key: str) -> float:
        """Count one hit for `key`, or raise 429; returns the hit's stamp for `refund`."""
        now = time.monotonic()
        with self._lock:
            while self._all and now - self._all[0] > self.window:
                self._all.popleft()
            if self.global_limit is not None and len(self._all) >= self.global_limit:
                raise APIError(429, "rate_limited", "the firm is busy; try again in a minute")
            hits = self._hits[key]
            while hits and now - hits[0] > self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                raise APIError(429, "rate_limited", "too many requests; try again in a minute")
            hits.append(now)
            self._all.append(now)
            if len(self._hits) > 1000:  # prune idle keys: no unbounded growth
                for k in [k for k, v in self._hits.items() if not v or now - v[-1] > self.window]:
                    del self._hits[k]
            return now


def _same(given: str | None, expected: str) -> bool:
    return given is not None and hmac.compare_digest(given.encode(), expected.encode())


def sse_frame(event: Event, view: Callable[[Event], dict] | None = None) -> str:
    data = view(event) if view is not None else event.public()
    return f"id: {event.id}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def sse_events(gw: Gateway, after_id: int, *, is_disconnected: Callable[[], Awaitable[bool]],
                     heartbeat: float = HEARTBEAT_SECONDS,
                     view: Callable[[Event], dict] | None = None) -> AsyncIterator[str]:
    """Replay everything after `after_id`, then stream live, with `: ping` heartbeats.

    Subscribes before replaying, so nothing written in between is lost; ids already sent
    are skipped, so nothing is sent twice. Ids may have gaps (contract §2): never wait
    for a "missing" id.
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=SSE_QUEUE_MAX)
    overflow = asyncio.Event()

    def push(event: Event) -> None:
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:  # a stalled reader: end its stream, it resumes by id
            overflow.set()

    unsubscribe = gw.subscribe(lambda e: loop.call_soon_threadsafe(push, e))
    last = after_id
    try:
        while True:  # replay in pages: a reconnect never copies the whole log at once
            page = await asyncio.to_thread(lambda: list(gw.store.since(last, limit=500)))
            for event in page:
                yield sse_frame(event, view)
                last = event.id
            if len(page) < 500:
                break
        while not await is_disconnected() and not overflow.is_set():
            try:
                event = await asyncio.wait_for(queue.get(), timeout=heartbeat)
            except TimeoutError:
                yield ": ping\n\n"
                continue
            if event.id <= last:
                continue
            yield sse_frame(event, view)
            last = event.id
    finally:
        unsubscribe()


def build_router(get_gateway: Callable[[], Gateway]) -> APIRouter:
    router = APIRouter()
    # Task 12: /talk Send and the voice-session mint have separate buckets, so a burst of
    # voice tries at the venue can't 429 the judge's Send; Send gets 1.5x the mint budget.
    # Values here are the normal mode; _talk_limit raises both in PUBLIC_MODE=event.
    mint_limiter = RateLimiter(limit=MINT_LIMITS[False][0], global_limit=MINT_LIMITS[False][1])
    send_limiter = RateLimiter(limit=SEND_LIMITS[False][0], global_limit=SEND_LIMITS[False][1])
    free_limiter = RateLimiter(limit=FREE_LIMITS[False][0])
    audio_limiter = RateLimiter(limit=30, global_limit=120)
    ceo_mint_limiter = RateLimiter(limit=10, window=3600.0)  # v1.8: the CEO's 10 an hour
    # v1.13: 10 sign-ins per IP and email per 15 min; 200 in all, so rotating keys stop too
    login_limiter = RateLimiter(limit=10, window=900.0, global_limit=200)
    auth_state: dict[str, Any] = {}  # v1.13: the Sessions object for this app
    # v1.12 §5a: the public sign-up has /talk's per-IP limits, in its own bucket
    signup_limiter = RateLimiter(limit=SEND_LIMITS[False][0], global_limit=SEND_LIMITS[False][1])
    # one IP can't use up the public daily case cap (shared with /talk) or relay mail
    signup_daily = RateLimiter(limit=10, window=86400.0)
    # v1.10 §5c: /talk's TTS has the per-IP limits of a /talk Send, in its own bucket
    tts_limiter = RateLimiter(limit=SEND_LIMITS[False][0], global_limit=SEND_LIMITS[False][1])
    phrases_cache: dict[str, Any] = {"stamp": None, "phrases": None}
    audio_cache: dict[int, bytes] = {}  # brief text never changes: one TTS call per brief

    def gw() -> Gateway:
        return get_gateway()

    def sessions() -> Sessions:
        """v1.13: the CEO's signed sessions. UTAX_SESSION_SECRET (the lifespan generates and
        persists one at first boot); a test app without one gets a random in-memory key."""
        secret = gw().settings.session_secret or os.environ.get("UTAX_SESSION_SECRET", "")
        if auth_state.get("secret") != (secret or auth_state.get("secret")):
            auth_state.clear()
        if "sessions" not in auth_state:
            key = secret or secrets.token_urlsafe(48)
            auth_state.update(secret=key, sessions=Sessions(key))
        return auth_state["sessions"]

    def is_admin(authorization: str | None, token: str | None = None) -> bool:
        """The CEO: ADMIN_TOKEN as a bearer (scripts), or a signed-in session as a bearer or
        as ?token= (EventSource can't send headers). v1.7.1 hiding: with no ADMIN_TOKEN and
        no session, nobody is admin (the safe default for injection text)."""
        settings = gw().settings
        bearer = _bearer(authorization)
        if settings.admin_token and _same(bearer, settings.admin_token):
            return True
        return sessions().valid(bearer) or sessions().valid(token)

    def require_theater(token: str | None, authorization: str | None) -> None:
        settings = gw().settings
        if not settings.theater_token:
            return
        if _same(token, settings.theater_token):
            return
        if is_admin(authorization, token):
            return  # the CEO console may read what the theater reads
        raise APIError(401, "unauthorized", "theater token required")

    def auth_configured() -> bool:
        """Admin routes are closed once an ADMIN_TOKEN or a CEO password exists (open only
        in bare local dev with neither): a blank ADMIN_TOKEN never undoes the CEO login."""
        settings = gw().settings
        return bool(settings.admin_token or settings.ceo_password_hash)

    def require_admin(authorization: str | None) -> None:
        if auth_configured() and not is_admin(authorization):
            raise APIError(401, "unauthorized", "admin token or a CEO session required")

    def _talk_limit(limiter: RateLimiter, limits: dict[bool, tuple[int, int | None]]) -> None:
        # PUBLIC_MODE=event: the finale crowd shares one venue IP, so the per-IP cap rises
        # (the global cap keeps its 6x headroom over it)
        limiter.limit, limiter.global_limit = limits[gw().settings.public_mode == "event"]

    def act(name: str, args: dict[str, Any]) -> dict[str, Any]:
        return result_or_raise(gw().perform(name, args))

    # ------------------------------------------------------------------ §4 theater

    @router.get("/api/state")
    def state(token: str | None = Query(None), authorization: str | None = Header(None)):
        require_theater(token, authorization)
        settings = gw().settings
        admin = not auth_configured() or is_admin(authorization, token)
        return gw().public_state(admin=admin)

    @router.get("/api/events")
    def events(after_id: int = Query(0, ge=0), limit: int = Query(500, ge=1, le=2000),
               token: str | None = Query(None), authorization: str | None = Header(None)):
        require_theater(token, authorization)
        batch = list(gw().store.since(after_id, limit=limit + 1))
        has_more = len(batch) > limit
        batch = batch[:limit]
        admin = is_admin(authorization, token)
        return {"data": [gw().public_event(e, admin=admin) for e in batch],
                "next_after_id": batch[-1].id if batch else after_id, "has_more": has_more}

    @router.get("/api/events/stream")
    async def stream(request: Request, after_id: int = Query(0, ge=0),
                     token: str | None = Query(None), authorization: str | None = Header(None),
                     last_event_id: str | None = Header(None)):
        require_theater(token, authorization)
        start = after_id
        if last_event_id and last_event_id.strip().isdigit():
            start = max(start, int(last_event_id.strip()))
        admin = is_admin(authorization, token)
        body = sse_events(gw(), start, is_disconnected=request.is_disconnected,
                          heartbeat=request.app.state.sse_heartbeat,
                          view=lambda e: gw().public_event(e, admin=admin))
        return StreamingResponse(body, media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # ------------------------------------------------------------------ §5 control

    # ------------------------------------------------------------------ v1.13 §5a-bis

    @router.post("/api/auth/login")
    def login(request: Request, body: dict = Body(...)):
        """The CEO signs in: CEO_EMAIL (any case) and the password behind CEO_PASSWORD_HASH.
        One answer for a wrong email or a wrong password; 10 tries per IP per 15 minutes."""
        settings = gw().settings
        email, password = body.get("email"), body.get("password")
        # per IP and email: a crowd sharing the venue's IP can't lock the CEO out
        login_limiter.check(f"{_client_ip(request)}|{str(email or '').strip().lower()[:320]}")
        given = email.strip().lower() if isinstance(email, str) else ""
        expected = settings.ceo_email.strip().lower()
        email_ok = bool(expected) and hmac.compare_digest(given.encode(), expected.encode())
        # always a full scrypt check, so the reply time never tells which field was wrong
        password_ok = verify_password(password if isinstance(password, str) else "",
                                      settings.ceo_password_hash)
        if not (email_ok and password_ok):
            raise APIError(401, "bad_login", "wrong email or password")
        token, expires = sessions().issue()
        return {"session": token, "expires_at": iso_ts(
            datetime.fromtimestamp(expires, tz=timezone.utc))}

    @router.post("/api/auth/logout")
    def logout(authorization: str | None = Header(None)):
        sessions().revoke(_bearer(authorization))
        return {"ok": True}

    @router.post("/api/approve")
    def approve(body: dict = Body(...), authorization: str | None = Header(None)):
        require_admin(authorization)
        return act("approve", _pick(body, "approval_id", "decision", "note"))

    @router.post("/api/signup")
    def signup(request: Request, body: dict = Body(...)):
        """v1.12 §5a (D32): public. Opens a case like an inbound email and sends one welcome
        email asking for the numbers. Returns {case_id, masked_name}."""
        _talk_limit(signup_limiter, SEND_LIMITS)
        ip = _client_ip(request)
        signup_limiter.check(ip)
        signup_daily.check(ip)
        # v1.12.2: always the same answer, new address or existing client (no way to tell
        # which addresses are clients); the welcome email is the client's receipt
        act("signup", _pick(body, "name", "company", "email", "request_id"))
        return {"ok": True}

    @router.post("/api/voice")
    async def voice(request: Request, authorization: str | None = Header(None)):
        # task 13: read at most VOICE_MAX_BYTES before parsing, so a huge body can't stall
        # the event loop ahead of the Send limiter (the page's 200 x 1000-char turns fit)
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > VOICE_MAX_BYTES:
            raise APIError(413, "too_large", "request body too large")
        raw = bytearray()
        async for chunk in request.stream():
            raw += chunk
            if len(raw) > VOICE_MAX_BYTES:
                raise APIError(413, "too_large", "request body too large")
        try:
            body = json.loads(raw)
        except ValueError as exc:
            raise APIError(400, "bad_input", "the body must be JSON") from exc
        if not isinstance(body, dict):
            raise APIError(400, "bad_input", "the body must be a JSON object")
        return await asyncio.to_thread(_voice_send, request, body, authorization)

    def _voice_send(request: Request, body: dict, authorization: str | None) -> dict:
        args = _pick(body, "text", "channel", "speaker", "reply_email", "turns", "request_id")
        if body.get("speaker") == "ceo":
            require_admin(authorization)  # a typed order is the CEO's
            return act("voice", args)
        _talk_limit(send_limiter, SEND_LIMITS)
        _talk_limit(free_limiter, FREE_LIMITS)
        ip = _client_ip(request)
        stamp = send_limiter.check(ip)  # the public /talk page's Send

        def give_back() -> None:
            # task 13: the gateway refuses bad input (and answers a retry) before any costly
            # work, so these don't use the Send budget, within their own per-IP allowance
            try:
                free_limiter.check(ip)
            except APIError:
                return  # past the allowance: it stays counted as a Send
            send_limiter.refund(ip, stamp)

        try:
            out = act("voice", args)
        except APIError as err:
            if err.code == "bad_input":
                give_back()
            raise
        if out.get("duplicate"):  # a retried request_id is the same Send
            give_back()
        return out

    @router.post("/api/trust")
    def trust(body: dict = Body(...), authorization: str | None = Header(None)):
        require_admin(authorization)
        return act("set_trust", _pick(body, "action_type", "state"))

    @router.post("/api/cases/{case_id}/unblock")
    def unblock(case_id: str, body: dict = Body(default_factory=dict),
                authorization: str | None = Header(None)):
        require_admin(authorization)
        return act("unblock", {"case_id": case_id, **_pick(body, "to", "note")})

    @router.post("/api/candidates/{candidate_id}/decision")
    def decision(candidate_id: str, body: dict = Body(...),
                 authorization: str | None = Header(None)):
        require_admin(authorization)
        return act("candidate_decision", {"candidate_id": candidate_id,
                                          **_pick(body, "decision", "note")})

    @router.post("/api/voice/session")
    def voice_session(request: Request, body: dict = Body(default_factory=dict),
                      authorization: str | None = Header(None)):
        mode = (body or {}).get("mode", "customer") if isinstance(body, dict) else None
        if mode not in ("customer", "ceo"):
            raise APIError(400, "bad_input", 'mode must be "customer" or "ceo"')
        if mode == "ceo":
            require_admin(authorization)
            stamp = ceo_mint_limiter.check("ceo")
            try:
                return _mint(gw().settings.ceo_voice_prompt_path, ceo=True)
            except Exception:  # no session came back: it doesn't use the CEO's 10 an hour
                ceo_mint_limiter.refund("ceo", stamp)
                raise
        _talk_limit(mint_limiter, MINT_LIMITS)
        mint_limiter.check(_client_ip(request))
        return _mint(gw().settings.voice_prompt_path, ceo=False)

    def _mint(prompt_path, *, ceo: bool) -> dict:
        """One paid Realtime session: the prompt from disk (none: refuse), the budget and the
        hold before the call (the public mint also checks the /talk case and mint caps; the
        CEO's has its own 10 an hour), booked only if a session comes back."""
        try:
            instructions = prompt_path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            instructions = ""
        if not instructions:  # never mint a general assistant on the firm's key
            log.error("voice prompt %s missing or empty; refusing to mint", prompt_path)
            raise APIError(503, "voice_unavailable", "voice is unavailable; use typed chat")
        voice = gw().ports.voice
        paid = voice.provider == "openai"
        # task 13: budget, the case cap and the mint cap checked, and the estimate held,
        # before the paid call; booked only if a session comes back
        if gw().reserve_voice("realtime", paid=paid, public=not ceo):
            raise APIError(503, "voice_unavailable", "voice is resting; use typed chat")
        session = None
        try:
            if ceo:
                from app.ceo_tools import realtime_tools

                session = voice.create_session(instructions, tools=realtime_tools())
            else:
                session = voice.create_session(instructions)
        except NotConfigured as exc:
            raise APIError(503, "voice_unavailable", "no voice key; use typed chat") from exc
        except PortError as exc:  # provider detail stays in the server log
            log.warning("voice session failed: %s", exc)
            raise APIError(503, "voice_unavailable", "voice is unavailable; use typed chat") from exc
        finally:
            if paid:
                model = session.model if session else ""
                from app.gateway.projections import CEO_MINT

                gw().settle_voice("realtime", f"{CEO_MINT}{model}" if ceo else model,
                                  ok=session is not None)
        return {"client_secret": session.client_secret, "model": session.model,
                "expires_at": session.expires_at, "realtime_url": session.realtime_url}

    # ------------------------------------------------------------------ §5b CEO console

    def _console(name: str, args: dict) -> dict:
        data = result_or_raise(gw().perform(name, args))
        data.pop("ok", None)
        return data

    @router.post("/api/ceo/tool")
    def ceo_tool(body: dict = Body(...), authorization: str | None = Header(None)):
        require_admin(authorization)
        return _console("ceo_tool", _pick(body, "name", "args", "call_id", "channel"))

    @router.post("/api/ceo/confirm")
    def ceo_confirm(body: dict = Body(...), authorization: str | None = Header(None)):
        require_admin(authorization)
        return _console("ceo_confirm", _pick(body, "proposal_id", "decision", "via", "heard"))

    @router.post("/api/ceo/command")
    def ceo_command(body: dict = Body(...), authorization: str | None = Header(None)):
        require_admin(authorization)
        return _console("ceo_command", _pick(body, "text"))

    @router.get("/assets/{name}")
    def asset(name: str):
        """v1.11 make_ad: the firm's ad PNGs, public (X and LinkedIn fetch them). Only
        ad_<n>.png from the gateway's own assets folder: no other file can be named."""
        if not re.fullmatch(r"ad_[0-9]{1,6}\.png", name):
            raise APIError(404, "not_found", "no such asset")
        path = gw().settings.data_dir / "assets" / name
        if not path.is_file():
            raise APIError(404, "not_found", "no such asset")
        return Response(content=path.read_bytes(), media_type="image/png",
                        headers={"Cache-Control": "public, max-age=3600"})

    @router.get("/api/docs/{case_id}/{file_name}")
    def case_document(case_id: str, file_name: str, authorization: str | None = Header(None)):
        """v1.15: a case's verified workpaper or memo, for the CEO (ADMIN_TOKEN or a session
        as the bearer: fetch it and save the blob; never a ?token= link that lands in logs).
        Only a file prepare_documents built and checked can be named."""
        require_admin(authorization)
        if not re.fullmatch(r"case_[0-9]{1,6}", case_id) or not re.fullmatch(
                r"[A-Za-z0-9_]{1,80}\.(xlsx|docx)", file_name):
            raise APIError(404, "not_found", "no such document")
        path = gw().doc_file(case_id, file_name)
        if path is None:
            raise APIError(404, "not_found", "no such document")
        return Response(content=path.read_bytes(),
                        media_type=gw().DOC_TYPES["." + file_name.rsplit(".", 1)[1]],
                        headers={"Content-Disposition": f'attachment; filename="{file_name}"',
                                 "Cache-Control": "private, no-store"})

    @router.get("/api/brief/audio")
    def brief_audio(request: Request, event_id: int = Query(..., ge=1),
                    token: str | None = Query(None), authorization: str | None = Header(None)):
        require_theater(token, authorization)
        if event_id in audio_cache:
            return Response(content=audio_cache[event_id], media_type="audio/mpeg")
        audio_limiter.check(_client_ip(request))
        event = next((e for e in gw().store.since(event_id - 1, limit=1)
                      if e.id == event_id), None)
        if event is None or event.type != "brief.generated":
            raise APIError(404, "not_found", f"no brief.generated event {event_id}")
        voice = gw().ports.voice
        text = event.payload["text"]
        if voice.provider == "elevenlabs":  # v1.10 §5c: booked per character
            if gw().reserve_tts("brief", len(text), "elevenlabs"):
                raise APIError(503, "voice_unavailable", "audio is resting; use speechSynthesis")
            try:
                audio = voice.speak(text)
            except Exception as exc:  # any failure releases the hold
                gw().settle_tts("brief", len(text), "elevenlabs", voice.tts_model, ok=False)
                if not isinstance(exc, PortError):
                    raise
                log.warning("brief audio failed: %s", exc)
                raise APIError(503, "voice_unavailable",
                               "audio is unavailable; use speechSynthesis") from exc
            gw().settle_tts("brief", len(text), "elevenlabs", voice.tts_model, ok=True)
            if len(audio_cache) > 200:
                audio_cache.pop(next(iter(audio_cache)))
            audio_cache[event_id] = audio
            return Response(content=audio, media_type="audio/mpeg")
        paid = voice.provider == "openai"
        if gw().reserve_voice("tts", paid=paid):  # task 13: SPEND_CAP_USD covers TTS too
            raise APIError(503, "voice_unavailable", "audio is resting; use speechSynthesis")
        audio = None
        try:
            audio = voice.speak(text)
        except NotConfigured as exc:
            raise APIError(503, "voice_unavailable", "no voice key; use speechSynthesis") from exc
        except PortError as exc:
            log.warning("brief audio failed: %s", exc)
            raise APIError(503, "voice_unavailable", "audio is unavailable; use speechSynthesis") from exc
        finally:
            if paid:
                gw().settle_voice("tts", getattr(voice, "tts_model", "tts"),
                                  ok=audio is not None)
        if len(audio_cache) > 200:
            audio_cache.pop(next(iter(audio_cache)))
        audio_cache[event_id] = audio
        return Response(content=audio, media_type="audio/mpeg")

    TTS_CEO_MAX = 1500

    def talk_phrases() -> dict[str, str] | None:
        """naive/prompts/talk-phrases.json, loaded strictly, re-read when it changes."""
        from app.gateway.talk_phrases import PhraseError, load

        path = gw().settings.talk_phrases_path
        try:
            stat = path.stat()
            stamp: Any = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            stamp = None
        if stamp is None or phrases_cache["stamp"] != stamp:
            try:
                phrases_cache["phrases"] = load(path)
            except PhraseError as err:  # retried on the next call (a file held by a scanner)
                log.error("talk phrases refused: %s", err)
                phrases_cache["phrases"] = None
                return None
            phrases_cache["stamp"] = stamp
        return phrases_cache["phrases"]

    @router.post("/api/tts")
    def tts(request: Request, body: dict = Body(...), authorization: str | None = Header(None)):
        """v1.10 §5c: speech through the voice adapter, streamed as audio/mpeg.
        purpose "ceo" (admin): the text as given, unmasked, at most 1500 characters.
        purpose "talk" (public): only a registry line {phrase, args}, masked, with /talk's
        per-IP limits and its own daily character budget."""
        purpose = body.get("purpose")
        if purpose == "ceo":
            require_admin(authorization)
            text = body.get("text")
            if not isinstance(text, str) or not text.strip() or len(text) > TTS_CEO_MAX:
                raise APIError(400, "bad_input", f"text must be 1-{TTS_CEO_MAX} characters")
        elif purpose == "talk":
            if "text" in body:
                raise APIError(400, "bad_input", "purpose talk takes {phrase, args}, no text")
            _talk_limit(tts_limiter, SEND_LIMITS)
            tts_limiter.check(_client_ip(request))
            from app.ceo_tools import mask_words
            from app.gateway.talk_phrases import PhraseError, render

            phrases = talk_phrases()
            if phrases is None:
                raise APIError(503, "voice_unavailable", "no phrase registry; use speechSynthesis")
            try:
                text = mask_words(render(phrases, body.get("phrase"), body.get("args")))
            except PhraseError as err:
                raise APIError(400, "bad_input", str(err)) from err
            if len(text) > 600:  # §5c: a talk line is at most 600 characters, as spoken
                raise APIError(400, "bad_input", "that line is over 600 characters")
        else:
            raise APIError(400, "bad_input", 'purpose must be "ceo" or "talk"')
        voice = gw().ports.voice
        if voice.provider is None:
            raise APIError(503, "voice_unavailable", "no voice key; use speechSynthesis")
        refusal = gw().reserve_tts(purpose, len(text), voice.provider)
        if refusal == "talk_budget":
            raise APIError(429, "rate_limited", "today's /talk voice is spent; use speechSynthesis")
        if refusal:
            raise APIError(503, "voice_unavailable", "voice is resting; use speechSynthesis")
        model = getattr(voice, "tts_model", "tts")
        try:
            opener = getattr(voice, "open_stream", None)
            if callable(opener):
                chunks, close = opener(text)
            else:
                audio = voice.speak(text)
                chunks, close = iter([audio]), (lambda: None)
        except Exception as exc:  # any failure releases the hold (never leaks the budget)
            gw().settle_tts(purpose, len(text), voice.provider, model, ok=False)
            if not isinstance(exc, PortError):
                raise
            log.warning("tts (%s) failed: %s", purpose, exc)
            raise APIError(503, "voice_unavailable", "audio is unavailable; use speechSynthesis") \
                from exc
        try:
            gw().settle_tts(purpose, len(text), voice.provider, model, ok=True)  # it bills now
        except Exception:
            close()  # never leave the upstream connection checked out of the pool
            raise

        def stream():
            try:
                yield from chunks
            finally:
                close()

        # the background task closes the upstream even when the browser leaves before the
        # first chunk (a generator that never started never runs its finally)
        return StreamingResponse(stream(), media_type="audio/mpeg",
                                 headers={"Cache-Control": "no-store"},
                                 background=BackgroundTask(close))

    @router.get("/api/ceo/brief")
    def ceo_brief(since_id: int = Query(0, ge=0), authorization: str | None = Header(None)):
        require_admin(authorization)
        data = result_or_raise(gw().perform("ceo_brief", {"since_id": since_id}))
        data.pop("ok", None)
        return data

    @router.post("/api/dev/inject-email")
    def inject_email(body: dict = Body(...), authorization: str | None = Header(None)):
        require_admin(authorization)
        # `inbox` is a dev-only extra ("hiring" plays the second inbox, §6c)
        return act("inject_email", _pick(body, "from", "subject", "body", "message_id", "inbox"))

    @router.post("/api/dev/resolve-purchase")
    def resolve_purchase(body: dict = Body(...), authorization: str | None = Header(None)):
        """Admin: an unsure Monid purchase checked in Monid's runs list. `key` is the agent's
        buy_tool key (no "buy:" prefix); `charged` (true/false) is required: false = never
        charged (no cost_usd; frees the key), true = charged with the cost_usd Monid shows.
        A purchase resolves once, never while in flight; a cost over its ceiling (max_usd)
        needs over_ceiling: true, never over monid.MAX_PLAUSIBLE_USD."""
        require_admin(authorization)
        picked = _pick(body, "key", "charged", "cost_usd", "over_ceiling", "episode")
        if not isinstance(picked.get("charged"), bool):  # "not charged" frees the key: say so
            raise APIError(400, "bad_input", "charged: true or false is required")
        return act("resolve_purchase", picked)

    @router.post("/hooks/agentmail")
    async def agentmail_hook(request: Request,
                             x_utax_hook_token: str | None = Header(None)):
        """AgentMail's message.received webhook (task 8). Svix-signed when
        AGENTMAIL_WEBHOOK_SECRET is set, else the X-UTax-Hook-Token header (never a query
        string: those land in access logs); with neither it is closed (503). Only our two
        inboxes are accepted. Delivery is at least once: the gateway dedupes on the
        message id. Anything acknowledged but not opened as a case is logged."""
        from app.gateway.adapters.agentmail import verify_svix

        settings = gw().settings
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > HOOK_MAX_BYTES:
            raise APIError(413, "too_large", "webhook body too large")
        raw = b""
        async for chunk in request.stream():
            raw += chunk
            if len(raw) > HOOK_MAX_BYTES:
                raise APIError(413, "too_large", "webhook body too large")
        if settings.agentmail_webhook_secret:
            if not verify_svix(settings.agentmail_webhook_secret, dict(request.headers), raw):
                raise APIError(401, "unauthorized", "bad webhook signature")
        elif settings.hook_token:
            if not _same(x_utax_hook_token, settings.hook_token):
                raise APIError(401, "unauthorized", "bad hook token")
        else:
            raise APIError(503, "hook_not_configured",
                           "set AGENTMAIL_WEBHOOK_SECRET (or UTAX_HOOK_TOKEN)")
        try:
            payload = json.loads(raw)
        except (ValueError, RecursionError) as err:
            raise APIError(400, "bad_input", "JSON expected") from err
        if not isinstance(payload, dict):
            raise APIError(400, "bad_input", "JSON object expected")
        event_id = str(payload.get("event_id") or "?")[:80]
        kind = payload.get("event_type")
        if kind != "message.received":
            if isinstance(kind, str) and kind.startswith("message.received."):
                # spam / blocked / unauthenticated: AgentMail held it; a human can look
                log.warning("agentmail hook: %s (event %s) not opened as a case", kind, event_id)
            return {"ok": True, "ignored": kind}  # 2xx: AgentMail must not retry these
        port = gw().ports.mail
        if not hasattr(port, "inbox_of"):  # a config slip must not swallow client mail
            raise APIError(503, "hook_not_configured", "ADAPTER_MAIL is not agentmail")
        inbox = port.inbox_of(payload)
        if inbox is None:  # another inbox in the organization: never ours to answer
            log.warning("agentmail hook: event %s is for an inbox that isn't ours", event_id)
            return {"ok": True, "ignored": "other_inbox"}
        try:
            mail = await asyncio.to_thread(port.parse_inbound, payload)
        except PortError as err:  # the > 64 KiB body fetch is down: let AgentMail retry
            raise APIError(503, "unavailable", str(err)) from err
        if mail is None:
            log.warning("agentmail hook: event %s (%s inbox) has no usable sender or "
                        "message id; not opened", event_id, inbox)
            return {"ok": True, "ignored": "unparseable"}
        result = await asyncio.to_thread(gw().perform, "receive_email",
                                         {"mail": mail, "inbox": inbox})
        if result.status == "error":  # e.g. the event store is down: retry later
            raise APIError(503, result.error["code"], result.error["message"])
        return {"ok": result.ok, "status": result.status, "data": result.data,
                "error": result.error}

    return router


HOOK_MAX_BYTES = 1_000_000  # a message.received body is inlined only up to 64 KiB


def _pick(body: dict, *names: str) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise APIError(400, "bad_input", "JSON object expected")
    return {n: body[n] for n in names if n in body}


def _bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    return value.strip() if scheme.lower() == "bearer" else None


def _client_ip(request: Request) -> str:
    """The caller's IP for rate limiting. Forwarding headers are trusted only from a
    loopback/private peer (the cloudflared tunnel on localhost sets CF-Connecting-IP;
    Render's proxy sets X-Forwarded-For); anyone else could forge them."""
    import ipaddress

    peer = request.client.host if request.client else "unknown"
    try:
        ip = ipaddress.ip_address(peer)
        loopback, private = ip.is_loopback, ip.is_private and not ip.is_loopback
    except ValueError:
        loopback, private = peer in ("testclient", "localhost"), False
    if loopback:
        # cloudflared on this machine: it sets CF-Connecting-IP to the real client
        value = request.headers.get("cf-connecting-ip")
        if value:
            return value.strip()
    elif private:
        # a platform proxy (Render): it APPENDS the client it saw, so only the rightmost
        # X-Forwarded-For hop is trustworthy; anything left of it is client-written
        value = request.headers.get("x-forwarded-for")
        if value:
            return value.split(",")[-1].strip()
    return peer


def json_error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})
