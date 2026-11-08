"""Task 42 (design D37): the U-Tax agents' live Naïve session activity, relayed as
`agent.activity` events for the CEO's console (admin and session readers only: theater
readers never get them).

The confirm poller already lists the sessions once per pass (task 35); it hands each pass's
rows to `ActivityRelay.observe`, which opens one SSE stream (`GET /v1/sessions/{id}/stream`,
`after_seq` resumes) per RUNNING session of our agents, at most MAX_STREAMS at once. Naïve
event types map to a kind:

- `session.running` -> thinking
- `tool.started {name}` -> tool_started (the tool's name only, never its arguments)
- `tool.completed {name}` -> tool_done
- `message.delta` -> writing: the reply's text so far, at most one event a second per
  session, masked and capped at 600 characters (the latest ones)
- `message.completed` (the agent's) -> writing, once more with the whole text
- `session.idle` -> idle, and the stream ends

The session's opening (user) message naming `order_<n>` stamps that order on its activity.
Under the shared 429 backoff no stream opens, and open ones stop.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any, Callable

from app.naive import NaiveClient, NaiveError

log = logging.getLogger("utax.naive_activity")

MAX_STREAMS = 8
WRITING_EVERY = 1.0  # seconds between "writing" events of one session
TEXT_MAX = 600
_ORDER = re.compile(r"\border_[0-9]{1,12}\b")


def _text(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    for key in ("delta", "text", "content"):
        value = data.get(key)
        if isinstance(value, str):
            return value
        if isinstance(value, list):  # content parts: [{"type": "text", "text": "..."}]
            return "".join(p.get("text", "") for p in value if isinstance(p, dict)
                           and isinstance(p.get("text"), str))
    return ""


class _Session:
    def __init__(self, actor: str) -> None:
        self.actor = actor
        self.last_seq = 0
        self.order_id: str | None = None
        self.text = ""
        self.last_writing = float("-inf")
        self.pending = False  # text arrived since the last "writing" event


class ActivityRelay:
    def __init__(self, gw: Any, client: NaiveClient, *, clock: Callable[[], float] = time.monotonic,
                 max_streams: int = MAX_STREAMS) -> None:
        self.gw = gw
        self.client = client
        self.clock = clock
        self.max_streams = max_streams
        self.sessions: dict[str, _Session] = {}
        self.tasks: dict[str, asyncio.Task] = {}

    def _blocked(self) -> bool:
        backoff = getattr(self.client, "backoff", None)
        return backoff is not None and backoff.blocked()

    def observe(self, rows: list[dict], agents: dict[str, str]) -> list[str]:
        """One poller pass: open a stream for each running session of our agents that has
        none. Returns the session ids started."""
        self.tasks = {sid: t for sid, t in self.tasks.items() if not t.done()}
        if self._blocked():
            return []
        started = []
        for row in rows:
            sid, actor = str(row.get("id") or ""), agents.get(str(row.get("agent_id") or ""))
            if not sid or actor is None or row.get("status") != "running":
                continue
            if sid in self.tasks or len(self.tasks) >= self.max_streams:
                continue
            self.sessions.setdefault(sid, _Session(actor))
            self.tasks[sid] = asyncio.create_task(self.follow(sid))
            started.append(sid)
        return started

    async def follow(self, session_id: str) -> None:
        """Stream one session until it idles, the stream ends, or the backoff trips."""
        state = self.sessions[session_id]
        try:
            async for event in self.client.stream(session_id, after_seq=state.last_seq):
                if self._blocked():
                    return
                seq = event.get("seq")
                if isinstance(seq, int):
                    state.last_seq = max(state.last_seq, seq)
                if await self.handle(session_id, event) == "idle":
                    return
            await self._flush(session_id)
        except NaiveError as err:
            backoff = getattr(self.client, "backoff", None)
            if err.status_code in (429, 503) and backoff is not None:
                backoff.trip(err.status_code, None)
            elif err.status_code not in (429, 503):
                log.warning("activity stream for %s stopped: %s", session_id, err)
        except Exception:  # noqa: BLE001 - one stream never takes the server down
            log.exception("activity stream for %s failed", session_id)

    async def handle(self, session_id: str, event: dict) -> str | None:
        state = self.sessions[session_id]
        kind, data = event.get("type"), event.get("data") or {}
        seq = event.get("seq")
        if kind == "message.completed" and isinstance(data, dict) and data.get("role") == "user":
            found = _ORDER.search(_text(data))
            if found and state.order_id is None:
                state.order_id = found.group(0)
            return None
        if kind == "session.running":
            return await self._emit(session_id, "thinking", seq=seq)
        if kind in ("tool.started", "tool.completed"):
            await self._flush(session_id)
            name = str(data.get("name") or "")[:120] if isinstance(data, dict) else ""
            return await self._emit(session_id, "tool_started" if kind == "tool.started"
                                    else "tool_done", seq=seq, tool=name or None)
        if kind == "message.delta":
            state.text = (state.text + _text(data))[-4 * TEXT_MAX:]
            state.pending = True
            if self.clock() - state.last_writing >= WRITING_EVERY:
                await self._flush(session_id, seq=seq)
            return None
        if kind == "message.completed":
            whole = _text(data)
            if whole:
                state.text, state.pending = whole, True
            await self._flush(session_id, seq=seq, force=True)
            state.text = ""
            return None
        if kind == "session.idle":
            await self._flush(session_id)
            await self._emit(session_id, "idle", seq=seq)
            return "idle"
        return None

    async def _flush(self, session_id: str, *, seq: Any = None, force: bool = False) -> None:
        state = self.sessions[session_id]
        if not state.pending or not state.text.strip():
            return
        if not force and self.clock() - state.last_writing < WRITING_EVERY:
            return
        state.pending = False
        state.last_writing = self.clock()
        await self._emit(session_id, "writing", seq=seq, text=state.text)

    async def _emit(self, session_id: str, kind: str, *, seq: Any = None,
                    tool: str | None = None, text: str | None = None) -> str:
        state = self.sessions[session_id]
        args = {"agent": state.actor, "session_id": session_id, "kind": kind,
                "seq": seq if isinstance(seq, int) else None}
        if tool:
            args["tool"] = tool
        if text:
            args["text"] = text
        if state.order_id:
            args["order_id"] = state.order_id
        result = await asyncio.to_thread(self.gw.perform, "agent_activity", args)
        if result.status != "done":
            log.warning("agent.activity not logged: %s", result.error)
        return kind
