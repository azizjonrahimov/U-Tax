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
        self._notes: set[asyncio.Task] = set()  # stop notes in flight (a reference each)

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
        """The gateway's own message into the session (queued if it's mid-run)."""
        try:
            await self.client.send_message(conversation["session_id"], message=text, queue=True,
                                           idempotency_key=f"utax-note:{secrets.token_hex(8)}")
        except NaiveError as err:
            log.warning("stop note not delivered to %s: %s", conversation["session_id"], err)

    async def _reply(self, conversation: dict) -> str | None:
        """Wait (bounded) for the session to idle; its last assistant message, or None."""
        deadline = self._clock() + self.wait
        while True:
            session = await self.client.get_session(conversation["session_id"])
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
            conversation = self._conversation(owner, conversation_id, lenient=True)
            try:
                agent_id = await self._agent()
                if conversation is not None and conversation["pending"]:
                    await self._collect(conversation)  # task 41: the earlier reply first
                if conversation is None:
                    cid = f"conv_{secrets.token_hex(6)}"
                    turn = await self._perform("ceo_turn", {"conversation_id": cid,
                                                            "text": text})
                    created = await self.client.create_session(
                        agent_id=agent_id, message=text,
                        idempotency_key=f"utax-talk:{cid}:1")
                    conversation = {"conversation_id": cid, "owner": owner,
                                    "session_id": str(created["id"]), "after_seq": 0,
                                    "pending": True, "last": None,
                                    "turn_id": (turn.data or {}).get("turn_id")}
                    self.conversations[cid] = conversation
                    self._by_owner[owner] = cid
                else:
                    cid = conversation["conversation_id"]
                    turn = await self._perform("ceo_turn", {"conversation_id": cid,
                                                            "text": text})
                    conversation["turn_id"] = (turn.data or {}).get("turn_id")
                    sent = await self.client.send_message(
                        conversation["session_id"], message=text, queue=True,
                        idempotency_key=f"utax-talk:{cid}:{secrets.token_hex(6)}")
                    accepted = sent.get("accepted_seq") if isinstance(sent, dict) else None
                    if isinstance(accepted, int):
                        conversation["after_seq"] = max(conversation["after_seq"], accepted)
                    conversation["pending"] = True
                return await self._finish(conversation)
            except NaiveError as err:
                log.warning("ceo talk: Naïve failed: %s", err)
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

    async def _finish(self, conversation: dict) -> dict:
        cid = conversation["conversation_id"]
        said = await self._reply(conversation)
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
