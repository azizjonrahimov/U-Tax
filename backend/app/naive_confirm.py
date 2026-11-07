"""Contract v1.9 §6b-bis (design D29, approved by the owner): Naïve's data-leak confirms.

Once a session has read gateway output (`read_people: true`), Naïve asks a person before
every write tool, even at "allow". The gateway answers those asks itself, for the 7 U-Tax
agents only:

- allow (one call, never the session) `board_read`, `board_write`, and `utax.<tool>` when
  `<tool>` is in that agent's own §6 column. The gateway already enforces trust, caps,
  masking and the CEO's approvals on every one of them;
- deny anything else (web fetch, bash, Naïve's own sends, social, wallet), with a CEO notice;
- never answer a `kind: "question"` (ask_operator): it goes to the CEO.

The HTTP calls run off the gateway lock; only the event write takes it.
"""

from __future__ import annotations

import asyncio
import logging

from app.gateway.perform import TOOL_ACTORS, Gateway
from app.naive import NaiveClient, NaiveError

log = logging.getLogger("utax.naive_confirm")

# The 7 agents' names in Naïve (naive/src/crew.ts) and their gateway seats (§6 actors).
AGENT_NAMES: dict[str, str] = {
    "chief-of-staff": "cos", "intake": "intake", "tax": "tax", "qa": "qa",
    "accounting": "acct", "marketing": "mkt", "hr": "hr",
}
BOARD_TOOLS = ("board_read", "board_write")
MCP_PREFIX = "utax."  # naive/src/crew.ts MCP_SERVER
POLL_SECONDS = 5.0
SESSIONS_PAGE = 100
DETAILS_PER_POLL = 25  # GET /v1/sessions/{id} per pass: never a burst against Naïve's limits


def naive_decision(actor: str | None, name: str) -> str:
    """"allow" for the board tools and the seat's own utax tools; "deny" for anything else."""
    if actor not in AGENT_NAMES.values():
        return "deny"
    if name in BOARD_TOOLS:
        return "allow"
    if name.startswith(MCP_PREFIX) and actor in TOOL_ACTORS.get(name[len(MCP_PREFIX):], ()):
        return "allow"
    return "deny"


class NaiveConfirmer:
    """Polls the U-Tax agents' sessions and answers their pending tool confirms."""

    def __init__(self, gw: Gateway, client: NaiveClient, *,
                 interval: float = POLL_SECONDS) -> None:
        self.gw = gw
        self.client = client
        self.interval = interval
        self.agents: dict[str, str] = {}  # Naïve agent id -> gateway actor

    async def boot(self) -> dict[str, str]:
        """Resolve the 7 agent ids by name (an agent of another app never matches)."""
        found: dict[str, str] = {}
        async for agent in self.client.paginate("/agents", {"limit": 100}):
            actor = AGENT_NAMES.get(str(agent.get("name") or ""))
            if actor and agent.get("id"):
                found[str(agent["id"])] = actor
        missing = sorted(set(AGENT_NAMES.values()) - set(found.values()))
        if missing:
            log.warning("naive confirms: no Naïve agent for %s", ", ".join(missing))
        self.agents = found
        return found

    async def poll_once(self) -> int:
        """One pass over every waiting session; returns how many asks were answered."""
        answered, budget = 0, DETAILS_PER_POLL
        for agent_id, actor in self.agents.items():
            page = await self.client.list_sessions(agent_id=agent_id, status="idle",
                                                   limit=SESSIONS_PAGE)
            for row in page.data:
                if row.get("agent_id") not in (None, agent_id):
                    continue  # the filter is the server's; never trust it alone
                if row.get("stop_reason") not in (None, "awaiting_approval"):
                    continue
                if budget <= 0:
                    return answered  # the rest wait for the next pass
                budget -= 1
                try:
                    answered += await self._answer(str(row["id"]), actor)
                except (NaiveError, KeyError, TypeError, ValueError):
                    log.exception("naive confirm for session %s failed; retried next poll",
                                  row.get("id"))
        return answered

    async def _answer(self, session_id: str, actor: str) -> int:
        session = await self.client.get_session(session_id)
        if session.get("status") != "idle" or session.get("stop_reason") != "awaiting_approval":
            return 0
        answered = 0
        for action in session.get("pending_actions") or []:
            read_people = bool(session.get("read_people") or action.get("read_people"))
            call_id = str(action.get("tool_call_id") or "")
            name = str(action.get("name") or "")[:120]
            if not call_id:
                continue
            if action.get("kind") != "tool":  # a question: the CEO's, never the gateway's
                await asyncio.to_thread(self.gw.perform, "naive_question", {
                    "session_id": session_id, "agent": actor, "tool": name,
                    "tool_call_id": call_id})
                continue
            decision = naive_decision(actor, name)
            await self.client.confirm_tool(
                session_id, tool_call_id=call_id, decision=decision,
                reason=("the U-Tax gateway checks this tool itself" if decision == "allow"
                        else "not a U-Tax tool for this agent"),
                idempotency_key=f"utax-confirm:{call_id}:{decision}")
            answered += 1
            await asyncio.to_thread(self.gw.perform, "naive_confirm", {
                "session_id": session_id, "agent": actor, "tool": name, "decision": decision,
                "read_people": read_people, "tool_call_id": call_id})
        return answered

    async def run(self) -> None:
        """The server's background loop: boot (retried), then poll every `interval`."""
        while not self.agents:
            try:
                await self.boot()
                if not self.agents:
                    log.warning("naive confirms: no U-Tax agents in Naïve; retry in 60 s")
            except Exception:  # noqa: BLE001 - Naïve down at boot never stops the server
                log.exception("naive confirms: agent lookup failed; retry in 60 s")
            if not self.agents:
                await asyncio.sleep(60)
        while True:
            try:
                await self.poll_once()
            except Exception:  # noqa: BLE001 - one bad poll never ends the loop
                log.exception("naive confirm poll failed")
            await asyncio.sleep(self.interval)


__all__ = ["AGENT_NAMES", "NaiveConfirmer", "naive_decision"]
