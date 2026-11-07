"""Contract v1.9 §6b-bis (design D29, approved by the owner): Naïve's data-leak confirms.

Once a session has read gateway output (`read_people: true`), Naïve asks a person before
every write tool, even at "allow". The gateway answers those asks itself, for the 7 U-Tax
agents only:

- allow (one call, `scope: "call"`) `board_read`, `board_write`, and `utax.<tool>` when
  `<tool>` is in that agent's own §6 column; for the Chief of Staff only, also the
  coordinator tools (v1.10.3: send_to_agent, wait_for_agents, list_agents). The gateway already enforces trust, caps,
  masking and the CEO's approvals on every one of them;
- deny anything else (web fetch, bash, Naïve's own sends, social, wallet), with a CEO notice;
- never answer a `kind: "question"` (ask_operator, `stop_reason: awaiting_answer`): the CEO
  hears about it.

The HTTP calls run off the gateway lock; only the event writes take it. The key needs
`agents:read` (the 7 ids are looked up by name) plus `sessions:read` and `sessions:write`.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from app.gateway.perform import TOOL_ACTORS, Gateway
from app.naive import NaiveClient, NaiveError

log = logging.getLogger("utax.naive_confirm")

# The 7 agents' names in Naïve (naive/src/crew.ts) and their gateway seats (§6 actors).
AGENT_NAMES: dict[str, str] = {
    "chief-of-staff": "cos", "intake": "intake", "tax": "tax", "qa": "qa",
    "accounting": "acct", "marketing": "mkt", "hr": "hr",
}
BOARD_TOOLS = ("board_read", "board_write")
# v1.10.3 (owner: "allow handoffs"): Naive's coordinator tools hand work to our own agents
# (naive/src/policy.ts COORDINATOR); the Chief of Staff only
COORDINATOR_TOOLS = ("send_to_agent", "wait_for_agents", "list_agents")
MCP_PREFIX = "utax."  # naive/src/crew.ts MCP_SERVER
WAITING = ("awaiting_approval", "awaiting_answer")  # a tool confirm, an ask_operator question
POLL_SECONDS = 5.0
SESSIONS_PAGE = 100
DETAILS_PER_POLL = 25  # GET /v1/sessions/{id} per pass: never a burst against Naïve's limits
QUIET_SECONDS = 30.0  # a session with nothing for the gateway to answer rests this long
REBOOT_SECONDS = 600.0  # the 7 ids are looked up again (a re-created agent, a partial boot)


def naive_decision(actor: str | None, name: str) -> str:
    """"allow" for the board tools and the seat's own utax tools; "deny" for anything else."""
    if actor not in AGENT_NAMES.values():
        return "deny"
    if name in BOARD_TOOLS:
        return "allow"
    if name in COORDINATOR_TOOLS:
        return "allow" if actor == "cos" else "deny"
    if name.startswith(MCP_PREFIX) and actor in TOOL_ACTORS.get(name[len(MCP_PREFIX):], ()):
        return "allow"
    return "deny"


class NaiveConfirmer:
    """Polls the U-Tax agents' sessions and answers their pending tool confirms."""

    def __init__(self, gw: Gateway, client: NaiveClient, *,
                 interval: float = POLL_SECONDS, clock=time.monotonic) -> None:
        self.gw = gw
        self.client = client
        self.interval = interval
        self.clock = clock
        self.agents: dict[str, str] = {}  # Naïve agent id -> gateway actor
        self.booted_at: float | None = None
        self._turn = 0  # which agent goes first: rotated, so none starves the others
        self._quiet: dict[str, float] = {}  # session id -> rest until
        self._unlogged: dict[str, tuple[str, dict]] = {}  # answered, its event not written yet

    async def boot(self) -> dict[str, str]:
        """Resolve the 7 agent ids by name. naive.config.ts also stamps each agent with
        `metadata.utax_actor`: an agent whose stamp names another seat (another app's "tax")
        is never adopted."""
        found: dict[str, str] = {}
        async for agent in self.client.paginate("/agents", {"limit": 100}):
            actor = AGENT_NAMES.get(str(agent.get("name") or ""))
            meta = agent.get("metadata")
            stamp = meta.get("utax_actor") if isinstance(meta, dict) else None
            if stamp is not None and stamp != actor:
                continue
            if actor and agent.get("id"):
                found[str(agent["id"])] = actor
        missing = sorted(set(AGENT_NAMES.values()) - set(found.values()))
        if missing and found:  # none at all is the run loop's notice
            await self._notice("ceo:naive-confirm:missing:" + "-".join(missing),
                               "Naïve confirms: agents missing",
                               "No Naïve agent found for " + ", ".join(missing)
                               + "; their confirms wait for a person")
        self.agents = found
        self.booted_at = self.clock()
        return found

    async def poll_once(self) -> int:
        """One pass over every waiting session; returns how many asks were answered."""
        await self._replay_unlogged()
        if self.booted_at is not None and self.clock() - self.booted_at >= REBOOT_SECONDS:
            try:
                await self.boot()
            except NaiveError:
                log.exception("naive confirms: agent lookup failed; the known ids stay")
        now = self.clock()
        self._quiet = {sid: until for sid, until in self._quiet.items() if until > now}
        agents = list(self.agents.items())
        if not agents:
            return 0
        start = self._turn % len(agents)
        self._turn += 1
        answered, budget = 0, DETAILS_PER_POLL
        for agent_id, actor in agents[start:] + agents[:start]:
            try:
                page = await self.client.list_sessions(agent_id=agent_id, status="idle",
                                                       limit=SESSIONS_PAGE)
            except NaiveError:  # one agent's error never ends the pass for the others
                log.exception("naive confirms: listing %s's sessions failed", actor)
                continue
            for row in page.data:
                session_id = str(row.get("id") or "")
                if not session_id or row.get("agent_id") not in (None, agent_id):
                    continue  # the filter is the server's; _answer re-checks the session
                if row.get("stop_reason") not in (None, *WAITING):
                    continue
                if session_id in self._quiet:
                    continue
                if budget <= 0:
                    return answered  # the rest wait for the next pass
                budget -= 1
                try:
                    n = await self._answer(session_id, agent_id, actor)
                except (NaiveError, KeyError, TypeError, ValueError):
                    log.exception("naive confirm for session %s failed; retried next poll",
                                  session_id)
                    continue
                if n == 0:
                    self._quiet[session_id] = now + QUIET_SECONDS
                answered += n
        return answered

    async def _answer(self, session_id: str, agent_id: str, actor: str) -> int:
        session = await self.client.get_session(session_id)
        if session.get("agent_id") != agent_id:  # fail closed: only this agent's own session
            log.warning("naive confirms: session %s is not agent %s's; left alone",
                        session_id, agent_id)
            return 0
        if session.get("status") != "idle" or session.get("stop_reason") not in WAITING:
            return 0
        answered = 0
        for action in session.get("pending_actions") or []:
            call_id = str(action.get("tool_call_id") or "")[:120]
            name = str(action.get("name") or "")[:120] or "?"
            kind = action.get("kind")
            if not call_id:
                continue
            if kind == "question":  # the CEO's, never the gateway's
                await self._record("naive_question", {
                    "session_id": session_id, "agent": actor, "tool": name,
                    "tool_call_id": call_id})
                continue
            if kind != "tool":
                log.warning("naive confirms: unknown pending kind %r in %s; left alone",
                            kind, session_id)
                continue
            decision = naive_decision(actor, name)
            await self.client.confirm_tool(
                session_id, tool_call_id=call_id, decision=decision, scope="call",
                reason=("the U-Tax gateway checks this tool itself" if decision == "allow"
                        else "not a U-Tax tool for this agent"),
                idempotency_key=f"utax-confirm:{session_id}:{call_id}:{decision}")
            answered += 1
            read_people = bool(session.get("read_people") or action.get("read_people"))
            await self._record("naive_confirm", {
                "session_id": session_id, "agent": actor, "tool": name, "decision": decision,
                "read_people": read_people, "tool_call_id": call_id})
        return answered

    async def _record(self, action: str, args: dict[str, Any]) -> None:
        """The decision onto the log. A store error keeps it for the next pass: the answer
        already reached Naïve, and the session no longer waits to remind us."""
        key = f"{action}:{args['session_id']}:{args['tool_call_id']}"
        result = await asyncio.to_thread(self.gw.perform, action, dict(args))
        if result.status == "error":
            log.error("naive confirms: %s not logged (%s); retried next pass", key,
                      (result.error or {}).get("message"))
            self._unlogged[key] = (action, args)
        else:
            self._unlogged.pop(key, None)
            if result.status != "done":
                log.error("naive confirms: %s refused by the gateway: %s", key, result.error)

    async def _replay_unlogged(self) -> None:
        for action, args in list(self._unlogged.values()):
            await self._record(action, args)

    async def _notice(self, key: str, subject: str, summary: str) -> None:
        await asyncio.to_thread(self.gw.perform, "naive_notice",
                                {"key": key, "subject": subject, "summary": summary})

    async def run(self) -> None:
        """The server's background loop: boot (retried), then poll every `interval`."""
        while not self.agents:
            try:
                await self.boot()
                if not self.agents:
                    log.warning("naive confirms: no U-Tax agents in Naïve; retry in 60 s")
            except NaiveError as err:
                log.exception("naive confirms: agent lookup failed; retry in 60 s")
                if err.status_code in (401, 403):  # a key without agents:read, or a bad key
                    await self._notice(f"ceo:naive-confirm:boot:{err.status_code}",
                                       "Naïve confirms are off",
                                       f"The gateway's Naïve key was refused ({err.status_code}"
                                       f" {err.code}); it needs agents:read, sessions:read and"
                                       " sessions:write. Agents' write tools wait for a person.")
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
