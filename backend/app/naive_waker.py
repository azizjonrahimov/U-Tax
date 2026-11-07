"""Contract §6b: the gateway wakes the real Chief of Staff in Naïve.

Each wake message ("New case case_7", "FAST PATH case_3", "Approval apr_2 allowed for
case_3", ...) starts a Chief of Staff session: `POST /v1/sessions {agent_id, message}`. A
create always starts a NEW session, so a CoS still running an earlier wake never refuses
this one (409 session_running is only for messages to a running session); every wake gets
its own run. The agent's attached identity (`utax-cos`, naive.config.ts) applies: the
documented create fields don't include an identity, so none is sent.

- The chief-of-staff agent id is looked up by name (and its `metadata.utax_actor` stamp,
  like the confirm resolver), then cached; a 404 looks it up again.
- One Idempotency-Key per delivery: a retry after a failure reuses it (Naïve dedupes if the
  first try did land), a later identical wake (a restart's boot wake) gets a new one.
- It runs where the gateway delivers wakes: off the gateway lock. A failure raises, and the
  gateway's WAKE_BACKOFF retry path (5 s, 15 s, 60 s, then every 5 minutes) takes over.
"""

from __future__ import annotations

import logging
import threading
import uuid
from typing import Any

import httpx

from app.naive.client import API_VERSION, USER_AGENT, _api_root

log = logging.getLogger("utax.naive_waker")

COS_NAME = "chief-of-staff"


class WakeFailed(RuntimeError):
    pass


class NaiveWaker:
    def __init__(self, api_key: str, api_base: str = "https://api.vetta.sh", *,
                 transport: httpx.BaseTransport | None = None, timeout: float = 15.0) -> None:
        self._http = httpx.Client(
            base_url=_api_root(api_base), timeout=timeout, transport=transport,
            headers={"Authorization": f"Bearer {api_key}", "Vetta-Version": API_VERSION,
                     "Accept": "application/json", "User-Agent": USER_AGENT})
        self._lock = threading.Lock()  # its own: never the gateway's
        self.agent_id: str | None = None
        self._keys: dict[str, str] = {}  # wake message -> the key of its undelivered attempt

    def __repr__(self) -> str:  # never show the key
        return f"NaiveWaker(agent_id={self.agent_id!r})"

    def close(self) -> None:
        self._http.close()

    def resolve(self) -> str | None:
        """The chief-of-staff agent id, by name (never another seat's stamped agent)."""
        after: str | None = None
        for _ in range(20):
            response = self._http.get("/agents", params={"limit": 100,
                                                         **({"after": after} if after else {})})
            if not response.is_success:
                raise WakeFailed(f"agent lookup answered {response.status_code}")
            body: dict[str, Any] = response.json()
            for agent in body.get("data") or []:
                meta = agent.get("metadata")
                stamp = meta.get("utax_actor") if isinstance(meta, dict) else None
                if agent.get("name") == COS_NAME and stamp in (None, "cos") and agent.get("id"):
                    with self._lock:
                        self.agent_id = str(agent["id"])
                    return self.agent_id
            after = body.get("next_cursor")
            if not body.get("has_more") or not after:
                return None
        return None

    def __call__(self, message: str) -> None:
        """Deliver one wake, or raise (the gateway retries with backoff)."""
        agent_id = self.agent_id or self.resolve()
        if not agent_id:
            raise WakeFailed("no chief-of-staff agent in Naïve (naive up not run?)")
        with self._lock:
            key = self._keys.setdefault(message, f"utax-wake:{uuid.uuid4()}")
        try:
            response = self._http.post("/sessions", json={
                "agent_id": agent_id, "message": message,
                "metadata": {"utax_wake": message[:200]}}, headers={"Idempotency-Key": key})
        except httpx.HTTPError as exc:
            raise WakeFailed(f"Naïve unreachable: {exc}") from exc
        if response.status_code == 404:  # the agent was re-created: look it up again
            with self._lock:
                self.agent_id = None
        if not response.is_success:
            try:
                code = (response.json().get("error") or {}).get("code")
            except (ValueError, AttributeError):
                code = None
            raise WakeFailed(f"session create answered {response.status_code} {code or ''}")
        with self._lock:
            self._keys.pop(message, None)  # delivered: the next identical wake is a new one
        try:
            session = response.json().get("id")
        except (ValueError, AttributeError):
            session = None
        log.info("WAKE delivered to the Chief of Staff: %r -> session %s", message, session)
