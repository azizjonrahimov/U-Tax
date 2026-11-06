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
import threading
import time
from collections import defaultdict, deque
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Body, Header, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from app.errors import APIError
from app.gateway.events import Event
from app.gateway.perform import Gateway, ToolResult
from app.gateway.ports import NotConfigured, PortError

log = logging.getLogger("utax.api")

HEARTBEAT_SECONDS = 15.0
SSE_QUEUE_MAX = 2000

# refused codes -> HTTP status (everything else refused is 409)
_REFUSED_STATUS = {
    "not_found": 404, "bad_input": 400, "unknown_action": 404, "not_local": 403,
    "tool_not_allowed": 403, "wrong_status": 409, "paused": 409, "denied": 409,
    "qa_gate": 409,
}
_ERROR_STATUS = {"store_unavailable": 503, "internal_error": 500}


def result_or_raise(result: ToolResult) -> dict[str, Any]:
    """Map a perform() result onto the contract's HTTP shapes."""
    if result.status == "done":
        return {"ok": True, **result.data}
    if result.status == "needs_approval":
        return {"ok": True, "status": "needs_approval", "approval_id": result.approval_id,
                **result.data}
    err = result.error or {"code": "error", "message": "failed"}
    if result.status == "refused":
        raise APIError(_REFUSED_STATUS.get(err["code"], 409), err["code"], err["message"])
    raise APIError(_ERROR_STATUS.get(err["code"], 502), err["code"], err["message"])


class RateLimiter:
    """Sliding window per key (client IP): `limit` calls per `window` seconds, plus a
    `global_limit` across all keys, so forged or rotating IPs can't multiply the budget."""

    def __init__(self, limit: int, window: float = 60.0, global_limit: int | None = None) -> None:
        self.limit, self.window, self.global_limit = limit, window, global_limit
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._all: deque[float] = deque()
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
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


def _same(given: str | None, expected: str) -> bool:
    return given is not None and hmac.compare_digest(given.encode(), expected.encode())


def sse_frame(event: Event) -> str:
    return f"id: {event.id}\ndata: {json.dumps(event.public(), ensure_ascii=False)}\n\n"


async def sse_events(gw: Gateway, after_id: int, *, is_disconnected: Callable[[], Awaitable[bool]],
                     heartbeat: float = HEARTBEAT_SECONDS) -> AsyncIterator[str]:
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
                yield sse_frame(event)
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
            yield sse_frame(event)
            last = event.id
    finally:
        unsubscribe()


def build_router(get_gateway: Callable[[], Gateway]) -> APIRouter:
    router = APIRouter()
    public_limiter = RateLimiter(limit=20, global_limit=120)
    audio_limiter = RateLimiter(limit=30, global_limit=120)
    audio_cache: dict[int, bytes] = {}  # brief text never changes: one TTS call per brief

    def gw() -> Gateway:
        return get_gateway()

    def require_theater(token: str | None, authorization: str | None) -> None:
        settings = gw().settings
        if not settings.theater_token:
            return
        if _same(token, settings.theater_token):
            return
        if settings.admin_token and _same(_bearer(authorization), settings.admin_token):
            return  # the CEO console may read what the theater reads
        raise APIError(401, "unauthorized", "theater token required")

    def require_admin(authorization: str | None) -> None:
        settings = gw().settings
        if settings.admin_token and not _same(_bearer(authorization), settings.admin_token):
            raise APIError(401, "unauthorized", "admin token required")

    def _talk_limit(limiter: RateLimiter) -> None:
        # PUBLIC_MODE=event: the finale crowd shares one venue IP, so 60/min per IP
        event = gw().settings.public_mode == "event"
        limiter.limit = 60 if event else 20
        limiter.global_limit = 360 if event else 120  # the global cap keeps its 6x headroom

    def act(name: str, args: dict[str, Any]) -> dict[str, Any]:
        return result_or_raise(gw().perform(name, args))

    # ------------------------------------------------------------------ §4 theater

    @router.get("/api/state")
    def state(token: str | None = Query(None), authorization: str | None = Header(None)):
        require_theater(token, authorization)
        settings = gw().settings
        admin = not settings.admin_token or _same(_bearer(authorization), settings.admin_token)
        return gw().public_state(admin=admin)

    @router.get("/api/events")
    def events(after_id: int = Query(0, ge=0), limit: int = Query(500, ge=1, le=2000),
               token: str | None = Query(None), authorization: str | None = Header(None)):
        require_theater(token, authorization)
        batch = list(gw().store.since(after_id, limit=limit + 1))
        has_more = len(batch) > limit
        batch = batch[:limit]
        return {"data": [e.public() for e in batch],
                "next_after_id": batch[-1].id if batch else after_id, "has_more": has_more}

    @router.get("/api/events/stream")
    async def stream(request: Request, after_id: int = Query(0, ge=0),
                     token: str | None = Query(None), authorization: str | None = Header(None),
                     last_event_id: str | None = Header(None)):
        require_theater(token, authorization)
        start = after_id
        if last_event_id and last_event_id.strip().isdigit():
            start = max(start, int(last_event_id.strip()))
        body = sse_events(gw(), start, is_disconnected=request.is_disconnected,
                          heartbeat=request.app.state.sse_heartbeat)
        return StreamingResponse(body, media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # ------------------------------------------------------------------ §5 control

    @router.post("/api/approve")
    def approve(body: dict = Body(...), authorization: str | None = Header(None)):
        require_admin(authorization)
        return act("approve", _pick(body, "approval_id", "decision", "note"))

    @router.post("/api/voice")
    def voice(request: Request, body: dict = Body(...), authorization: str | None = Header(None)):
        if body.get("speaker") == "ceo":
            require_admin(authorization)  # a typed order is the CEO's
        else:
            _talk_limit(public_limiter)
            public_limiter.check(_client_ip(request))  # the public /talk page
        return act("voice", _pick(body, "text", "channel", "speaker", "reply_email", "turns",
                                  "request_id"))

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
    def voice_session(request: Request):
        _talk_limit(public_limiter)
        public_limiter.check(_client_ip(request))
        prompt_path = gw().settings.voice_prompt_path
        try:
            instructions = prompt_path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            instructions = ""
        if not instructions:  # never mint a general assistant on the firm's key
            log.error("voice prompt %s missing or empty; refusing to mint", prompt_path)
            raise APIError(503, "voice_unavailable", "voice is unavailable; use typed chat")
        try:
            session = gw().ports.voice.create_session(instructions)
        except NotConfigured as exc:
            raise APIError(503, "voice_unavailable", "no voice key; use typed chat") from exc
        except PortError as exc:  # provider detail stays in the server log
            log.warning("voice session failed: %s", exc)
            raise APIError(503, "voice_unavailable", "voice is unavailable; use typed chat") from exc
        return {"client_secret": session.client_secret, "model": session.model,
                "expires_at": session.expires_at}

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
        try:
            audio = gw().ports.voice.speak(event.payload["text"])
        except NotConfigured as exc:
            raise APIError(503, "voice_unavailable", "no voice key; use speechSynthesis") from exc
        except PortError as exc:
            log.warning("brief audio failed: %s", exc)
            raise APIError(503, "voice_unavailable", "audio is unavailable; use speechSynthesis") from exc
        if len(audio_cache) > 200:
            audio_cache.pop(next(iter(audio_cache)))
        audio_cache[event_id] = audio
        return Response(content=audio, media_type="audio/mpeg")

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

    return router


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
