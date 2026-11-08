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

import hashlib
import logging
import threading
import time
import uuid
from typing import Any

import httpx

from app.naive.client import API_VERSION, USER_AGENT, _api_root

log = logging.getLogger("utax.naive_waker")

COS_NAME = "chief-of-staff"
DOWN_SECONDS = 30.0  # after an outage, wakes fail fast (straight to the gateway's backoff)
# once per case or applicant: a restart's boot wake reuses the first delivery's key, so
# Naive (24 h idempotency) never starts a second CoS session for the same open case
ONCE_PREFIXES = ("New case ", "FAST PATH ", "New applicant ")


class WakeFailed(RuntimeError):
    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        # task 35: set when the shared Naïve backoff holds it: the gateway retries the wake
        # once that clears (no escalating attempts, no stack trace)
        self.retry_after = retry_after


class NaiveWaker:
    def __init__(self, api_key: str, api_base: str = "https://api.vetta.sh", *,
                 transport: httpx.BaseTransport | None = None, timeout: float = 8.0,
                 clock=time.monotonic, trigger_of=None, backoff=None) -> None:
        self.backoff = backoff  # task 35: the gateway's shared NaiveBackoff
        # 22b: message -> the id of the event that triggered it (Gateway.wake_trigger)
        self._trigger_of = trigger_of
        # short: wakes run on request threads and at boot; a hung Naive must not hold them
        self._http = httpx.Client(
            base_url=_api_root(api_base), timeout=httpx.Timeout(timeout, connect=3.0),
            transport=transport,
            headers={"Authorization": f"Bearer {api_key}", "Vetta-Version": API_VERSION,
                     "Accept": "application/json", "User-Agent": USER_AGENT})
        self._lock = threading.Lock()  # its own: never the gateway's
        self.agent_id: str | None = None
        # wake message -> (agent id, key) of its undelivered attempt: a new agent, a new key
        # (the same key with a different body is a 409 at Naive)
        self._keys: dict[str, tuple[tuple[str, int | None], str]] = {}
        self._clock = clock
        self._down_until = 0.0

    def __repr__(self) -> str:  # never show the key
        return f"NaiveWaker(agent_id={self.agent_id!r})"

    def close(self) -> None:
        self._http.close()

    def resolve(self) -> str | None:
        """The chief-of-staff agent id, by name (never another seat's stamped agent)."""
        after: str | None = None
        unstamped: str | None = None
        for _ in range(20):
            response = self._http.get("/agents", params={"limit": 100,
                                                         **({"after": after} if after else {})})
            self._saw(response)
            if not response.is_success:
                raise WakeFailed(f"agent lookup answered {response.status_code}")
            body: dict[str, Any] = response.json()
            for agent in body.get("data") or []:
                meta = agent.get("metadata")
                stamp = meta.get("utax_actor") if isinstance(meta, dict) else None
                if (agent.get("name") != COS_NAME or not agent.get("id")
                        or agent.get("archived") or agent.get("status") == "archived"):
                    continue
                if stamp == "cos":  # ours for sure (naive.config.ts stamps every agent)
                    return self._found(str(agent["id"]))
                if stamp is None and unstamped is None:
                    unstamped = str(agent["id"])
            after = body.get("next_cursor")
            if not body.get("has_more") or not after:
                break
        return self._found(unstamped) if unstamped else None

    def _found(self, agent_id: str) -> str:
        with self._lock:
            self.agent_id = agent_id
        return agent_id

    def _saw(self, response: httpx.Response) -> None:
        """Task 35: a 429/503 trips the shared backoff; a success clears its level."""
        if self.backoff is None:
            return
        if response.status_code in (429, 503):
            from app.naive.client import _parse_retry_after

            wait = self.backoff.trip(response.status_code,
                                     _parse_retry_after(response.headers.get("Retry-After")))
            raise WakeFailed(f"Naïve answered {response.status_code}; retry in {wait:.0f} s",
                             retry_after=wait)
        if response.is_success:
            self.backoff.ok()

    def __call__(self, message: str) -> None:
        """Deliver one wake, or raise (the gateway retries with backoff)."""
        if self.backoff is not None and self.backoff.blocked():
            wait = self.backoff.remaining()
            raise WakeFailed(f"Naïve calls are paused; retry in {wait:.0f} s", retry_after=wait)
        if self._clock() < self._down_until:
            raise WakeFailed("Naïve was down a moment ago; retried with backoff")
        try:
            agent_id = self.agent_id or self.resolve()
        except httpx.HTTPError as exc:
            self._down_until = self._clock() + DOWN_SECONDS
            raise WakeFailed(f"Naïve unreachable: {exc}") from exc
        if not agent_id:
            raise WakeFailed("no chief-of-staff agent in Naïve (naive up not run?)")
        once = message.startswith(ONCE_PREFIXES)
        # 22b: a once-per-entity wake is keyed on the event that triggered it: a client's
        # second email on an open case starts a new session; a restart's boot wake dedupes
        trigger = self._trigger_of(message) if once and self._trigger_of else None
        with self._lock:
            held = self._keys.get(message)
            if held is None or held[0] != (agent_id, trigger):
                if once:
                    digest = hashlib.sha256(
                        f"{agent_id}|{message}|{trigger}".encode()).hexdigest()[:32]
                    held = ((agent_id, trigger), f"utax-wake:{digest}")
                else:
                    held = ((agent_id, trigger), f"utax-wake:{uuid.uuid4()}")
                self._keys[message] = held
            key = held[1]
        try:
            response = self._http.post("/sessions", json={"agent_id": agent_id,
                                                          "message": message},
                                       headers={"Idempotency-Key": key})
        except httpx.HTTPError as exc:
            self._down_until = self._clock() + DOWN_SECONDS
            raise WakeFailed(f"Naïve unreachable: {exc}") from exc
        self._saw(response)  # a 429/503 raises here, with the backoff's wait
        if response.status_code == 404:  # the agent was re-created: look it up again
            with self._lock:
                self.agent_id = None
        if response.status_code == 409:  # an idempotency conflict: the next try, a new key
            with self._lock:
                self._keys.pop(message, None)
        if response.status_code >= 500:
            self._down_until = self._clock() + DOWN_SECONDS
        if 400 <= response.status_code < 500 and response.status_code not in (404, 409, 429):
            log.error("WAKE refused by Naïve (%s): %r; check the key's sessions:write scope",
                      response.status_code, message)
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
