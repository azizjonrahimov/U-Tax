"""Contract v1.14 §5a-ter (design D34): the CEO talks to the assistant in plain words.

`POST /api/ceo/talk {text, conversation_id?}`:
1. A stop the gateway's grammar catches ("stop posting", "okay, stop everything") runs at
   once, never waiting on the LLM. The gateway then appends its own note to the assistant's
   session: "System: the CEO said stop; the gateway paused <types>. Undo id <id>." (so
   "undo that" works). Any leading "System:" is stripped from the CEO's own words first.
2. Anything else goes into the CEO's one Naïve session for this sign-in (`ceo-assistant`,
   created on the first turn), and the gateway waits until it idles, at most 25 s. A tool
   confirm Naïve raises meanwhile is answered on the spot (the same rules as the poller).
3. The reply is the assistant's last `message.completed` after our message, plus what it did
   (`actions: [{tool, summary, undo_id?}]`). On a timeout: `{say: "Still working on it…",
   pending: true}`, and `GET /api/ceo/talk/{conversation_id}` picks it up.
4. Task 50: Naive answering 429/503 ("at capacity", the shared backoff) never turns a turn
   into a 503. The CEO's words go into the conversation's outbox (one idempotency key each)
   and are retried within the 25 s budget; past it the turn answers pending, a background
   task keeps delivering (up to DELIVER_SECONDS), and GET collects the reply. The CEO never
   repeats himself. Stops stay instant (the grammar runs first, with no Naive call).
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from typing import Any

from app import ceo_direct, ceo_tools
from app.naive import NaiveClient, NaiveError

log = logging.getLogger("utax.ceo_talk")

AGENT_NAME = "ceo-assistant"
WAIT_SECONDS = 25.0
POLL_SECONDS = 1.0
STILL_WORKING = "Still working on it…"
BUSY = "The assistant can't be reached right now. Stops still work: say stop."
TEXT_MAX = 2000
EVENTS_PAGE = 100  # task 49: Naive's max list limit
EVENT_PAGES_MAX = 50
BUSY_STATUSES = (429, 503)  # task 50: Naive at capacity, or the shared backoff's gate
DELIVER_SECONDS = 600.0  # task 50: how long the background delivery keeps trying
RETRY_FLOOR = 0.5  # never spin: at least this long between retries


def _busy(err: NaiveError) -> bool:
    return err.status_code in BUSY_STATUSES


class TalkRefused(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


class CeoTalk:
    def __init__(self, gw: Any, client: NaiveClient, *, confirmer: Any = None,
                 wait: float = WAIT_SECONDS, poll: float = POLL_SECONDS,
                 sleep=asyncio.sleep, clock=time.monotonic) -> None:
        self.gw, self.client, self.confirmer = gw, client, confirmer
        self.wait, self.poll, self._sleep, self._clock = wait, poll, sleep, clock
        self.agent_id: str | None = None
        self.conversations: dict[str, dict] = {}  # conversation_id -> its Naïve session
        self._by_owner: dict[str, str] = {}  # a sign-in -> its conversation
        self._lock = asyncio.Lock()
        self._notes: set[asyncio.Task] = set()  # stop notes and deliveries (a reference each)

    async def drain(self) -> None:
        """Tests and shutdown: wait for the stop notes in flight."""
        while self._notes:
            await asyncio.gather(*list(self._notes), return_exceptions=True)

    # ------------------------------------------------------------------ the gateway (threads)

    async def _perform(self, name: str, args: dict) -> Any:
        return await asyncio.to_thread(self.gw.perform, name, args)

    # ------------------------------------------------------------------ Naïve

    async def _agent(self) -> str:
        if self.agent_id:
            return self.agent_id
        async for agent in self.client.paginate("/agents", {"limit": 100}):
            meta = agent.get("metadata")
            stamp = meta.get("utax_actor") if isinstance(meta, dict) else None
            if agent.get("name") == AGENT_NAME and agent.get("id") and stamp in (None, "ceo"):
                self.agent_id = str(agent["id"])
                return self.agent_id
        raise TalkRefused(503, "assistant_missing", "no ceo-assistant agent in Naïve "
                                                    "(naive up not run?)")

    async def _note(self, conversation: dict, text: str) -> None:
        """The gateway's own message into the session (queued if it's mid-run). Task 50:
        through the outbox, so a busy Naive delays it instead of losing it."""
        if conversation["session_id"] is None and not conversation.get("sending"):
            return  # no session (Naive never took the first message): nobody to tell
        conversation["outbox"].append((text, f"utax-note:{secrets.token_hex(8)}"))
        if conversation.get("sending"):
            return  # the turn or the delivery in flight sends it after the CEO's words
        try:
            await self._send(conversation, self._clock() + self.wait)
        except NaiveError as err:
            if _busy(err):
                self._deliver_later(conversation)
            else:
                log.warning("stop note not delivered to %s: %s", conversation["session_id"],
                            err)
                conversation["outbox"].clear()

    # ------------------------------------------------------------------ task 50: Naive busy

    async def _call(self, fn, deadline: float) -> Any:
        """One Naive call, retried through 429/503 (waiting out Retry-After or the shared
        backoff) until `deadline`; then the last NaiveError."""
        while True:
            try:
                return await fn()
            except NaiveError as err:
                left = deadline - self._clock()
                if not _busy(err) or left <= 0:
                    raise
                wait = err.retry_after or getattr(self.client.backoff, "remaining",
                                                  lambda: 0.0)() or 1.0
                await self._sleep(min(max(wait, RETRY_FLOOR), left))

    async def _send(self, conversation: dict, deadline: float) -> None:
        """Deliver the outbox in order: the first message creates the session. Each item keeps
        its idempotency key across retries, so nothing is ever posted twice."""
        conversation["sending"] = True
        try:
            while conversation["outbox"]:
                text, key = conversation["outbox"][0]
                if conversation["session_id"] is None:
                    agent_id = await self._call(self._agent, deadline)
                    created = await self._call(lambda: self.client.create_session(
                        agent_id=agent_id, message=text, idempotency_key=key), deadline)
                    conversation["session_id"] = str(created["id"])
                else:
                    sent = await self._call(lambda: self.client.send_message(
                        conversation["session_id"], message=text, queue=True,
                        idempotency_key=key), deadline)
                    accepted = sent.get("accepted_seq") if isinstance(sent, dict) else None
                    if isinstance(accepted, int):
                        conversation["after_seq"] = max(conversation["after_seq"], accepted)
                conversation["outbox"].pop(0)
        finally:
            conversation["sending"] = False

    def _deliver_later(self, conversation: dict) -> None:
        """Past the turn's budget: keep delivering in the background (GET collects)."""
        if conversation.get("delivering"):
            return
        conversation["delivering"] = True

        async def deliver() -> None:
            try:
                await self._send(conversation, self._clock() + DELIVER_SECONDS)
            except (NaiveError, TalkRefused) as err:
                log.warning("ceo talk: gave up delivering to Naïve: %s", err)
                conversation["outbox"].clear()
                conversation["gave_up"] = True
            finally:
                conversation["delivering"] = False

        task = asyncio.create_task(deliver())
        self._notes.add(task)
        task.add_done_callback(self._notes.discard)

    def _still_working(self, conversation: dict) -> dict:
        conversation["pending"] = True
        return {"conversation_id": conversation["conversation_id"], "say": STILL_WORKING,
                "pending": True}

    async def _reply(self, conversation: dict, deadline: float | None = None) -> str | None:
        """Wait (bounded) for the session to idle; its last assistant message, or None."""
        deadline = self._clock() + self.wait if deadline is None else deadline
        while True:
            try:
                session = await self._call(
                    lambda: self.client.get_session(conversation["session_id"]), deadline)
            except NaiveError as err:
                if _busy(err):
                    return None  # task 50: busy past the budget is "still working"
                raise
            stopped = session.get("stop_reason")
            if session.get("status") == "idle" and stopped == "awaiting_approval" \
                    and self.confirmer is not None and self.agent_id:
                await self.confirmer._answer(conversation["session_id"], self.agent_id, "ceo")
            elif session.get("status") in ("idle", "failed", "cancelled", "error"):
                return await self._last_message(conversation)
            if self._clock() >= deadline:
                return None
            await self._sleep(self.poll)

    async def _last_message(self, conversation: dict) -> str:
        """Task 49: Naive's limit is 1..100, so page (by after_seq) while has_more: the
        latest message.completed is never missed. Bounded; stops if a page doesn't advance."""
        said = None
        for _ in range(EVENT_PAGES_MAX):
            before = conversation["after_seq"]
            page = await self.client.session_events(conversation["session_id"],
                                                    after_seq=before, limit=EVENTS_PAGE)
            for event in page.data:
                conversation["after_seq"] = max(conversation["after_seq"],
                                                int(event.get("seq") or 0))
                data = event.get("data") or {}
                if event.get("type") == "message.completed" and data.get("role") != "user":
                    said = str(data.get("content") or "").strip() or said
            if not page.has_more or conversation["after_seq"] <= before:
                break
        return said or "Done."

    # ------------------------------------------------------------------ the route

    def _conversation(self, owner: str, conversation_id: str | None, *,
                      lenient: bool = False) -> dict | None:
        """`lenient` (talk, task 41): an unknown or forgotten id (a restart, another
        sign-in's) is None, a new conversation, never a 404, so a stop always works."""
        cid = conversation_id or self._by_owner.get(owner)
        if cid is None:
            return None
        conversation = self.conversations.get(cid)
        if conversation is None or conversation["owner"] != owner:
            if lenient:
                return None
            raise TalkRefused(404, "not_found", "no such conversation")
        return conversation

    async def talk(self, owner: str, text: Any, conversation_id: str | None = None) -> dict:
        if not isinstance(text, str):
            raise TalkRefused(400, "bad_input", "text is required")
        text = ceo_direct.strip_system(text).strip()[:TEXT_MAX]
        if not text:
            raise TalkRefused(400, "bad_input", "text is required")
        parsed = ceo_tools.parse(text)  # task 41: the stop grammar FIRST, before anything
        if parsed and parsed[0] == "tool" and ceo_tools.REGISTRY[parsed[1][0]].kind == "stop":
            return await self._stop(self._conversation(owner, conversation_id, lenient=True),
                                    parsed[1][1])
        async with self._lock:  # one turn at a time for the CEO
            deadline = self._clock() + self.wait
            conversation = self._conversation(owner, conversation_id, lenient=True)
            try:
                if conversation is not None and conversation["pending"] \
                        and not conversation["outbox"]:
                    await self._collect(conversation)  # task 41: the earlier reply first
                if conversation is None:
                    cid = f"conv_{secrets.token_hex(6)}"
                    turn = await self._perform("ceo_turn", {"conversation_id": cid,
                                                            "text": text})
                    conversation = {"conversation_id": cid, "owner": owner,
                                    "session_id": None, "after_seq": 0, "pending": True,
                                    "last": None, "outbox": [(text, f"utax-talk:{cid}:1")],
                                    "turn_id": (turn.data or {}).get("turn_id")}
                    self.conversations[cid] = conversation
                    self._by_owner[owner] = cid
                else:
                    cid = conversation["conversation_id"]
                    turn = await self._perform("ceo_turn", {"conversation_id": cid,
                                                            "text": text})
                    conversation["turn_id"] = (turn.data or {}).get("turn_id")
                    conversation["outbox"].append(
                        (text, f"utax-talk:{cid}:{secrets.token_hex(6)}"))
                    conversation["pending"] = True
                    conversation.pop("gave_up", None)
                if conversation.get("delivering"):
                    return self._still_working(conversation)  # queued behind the delivery
                try:
                    await self._send(conversation, deadline)
                except NaiveError as err:
                    if not _busy(err):
                        raise
                    log.warning("ceo talk: Naïve busy past the turn budget: %s", err)
                    self._deliver_later(conversation)
                    return self._still_working(conversation)
                return await self._finish(conversation, deadline)
            except NaiveError as err:
                log.warning("ceo talk: Naïve failed: %s", err)
                if conversation is not None and not conversation.get("delivering"):
                    conversation["outbox"].clear()  # a hard refusal: never resent later
                    if conversation["session_id"] is None:
                        conversation["pending"] = False  # nothing to collect
                raise TalkRefused(503, "assistant_unavailable", BUSY) from err

    async def check(self, owner: str, conversation_id: str) -> dict:
        """GET /api/ceo/talk/{id}: a pending turn's reply, once it's ready."""
        conversation = self._conversation(owner, conversation_id)
        if conversation is None:
            raise TalkRefused(404, "not_found", "no such conversation")
        async with self._lock:  # task 41: `pending` is read under the lock (a turn may land)
            if not conversation["pending"]:
                return conversation["last"] or {"conversation_id": conversation_id, "say": "",
                                                "actions": []}
            if conversation.pop("gave_up", False):  # task 50: Naive stayed busy 10 minutes
                conversation["pending"] = False
                raise TalkRefused(503, "assistant_unavailable", BUSY)
            if conversation["outbox"]:  # task 50: still being delivered in the background
                return self._still_working(conversation)
            try:
                return await self._collect(conversation)
            except NaiveError as err:
                raise TalkRefused(503, "assistant_unavailable", BUSY) from err

    async def _collect(self, conversation: dict) -> dict:
        """A pending turn's reply, with a short wait (a page poll; the lock is held)."""
        saved, self.wait = self.wait, min(self.wait, 2.0)
        try:
            return await self._finish(conversation)
        finally:
            self.wait = saved

    async def _finish(self, conversation: dict, deadline: float | None = None) -> dict:
        cid = conversation["conversation_id"]
        said = await self._reply(conversation, deadline)
        if said is None:
            return {"conversation_id": cid, "say": STILL_WORKING, "pending": True}
        done = await self._perform("ceo_reply", {"conversation_id": cid,
                                                 "turn_id": conversation.get("turn_id")})
        out = {"conversation_id": cid, "say": said, "actions": (done.data or {}).get("actions",
                                                                                      [])}
        conversation.update(pending=False, last=out)
        return out

    async def _stop(self, conversation: dict | None, args: dict) -> dict:
        """The grammar caught a stop: pause now, as the CEO; then tell the assistant."""
        result = await self._perform("ceo_direct", {"name": "pause", "args": args,
                                                    "as_ceo": True})
        if result.status != "done":
            raise TalkRefused(400, (result.error or {}).get("code", "bad_input"),
                              (result.error or {}).get("message", "that stop failed"))
        data = result.data
        action = {"tool": "pause", "summary": data["summary"],
                  **({"undo_id": data["undo_id"]} if data.get("undo_id") else {})}
        cid = None
        if conversation is not None:
            cid = conversation["conversation_id"]
            task = asyncio.create_task(self._note(conversation, ceo_direct.stop_note(
                data.get("paused") or [], data.get("undo_id"))))
            self._notes.add(task)  # never waited on: the stop already ran
            task.add_done_callback(self._notes.discard)
        return {"conversation_id": cid, "say": data["summary"], "actions": [action]}


__all__ = ["CeoTalk", "TalkRefused"]
