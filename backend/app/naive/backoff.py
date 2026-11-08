"""Task 35: one backoff for every Naïve call the gateway makes (the confirm poller, its
recovery nudges and the waker).

Naïve answered 429 "the service is at capacity" to nearly everything, and each component
kept calling on its own clock (the poller per seat every 5 s). Now any 429 or 503 trips one
shared gate: no Naïve call at all until it clears, for `Retry-After` when Naïve sends one,
else 30 s doubling to 5 minutes, with jitter. One WARNING per trip, not per call; the first
success resets it.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from typing import Callable

log = logging.getLogger("utax.naive_backoff")

FIRST_SECONDS = 30.0
MAX_SECONDS = 300.0
JITTER = 0.2  # +-20%: the poller and the waker never retry in lockstep
TRIP_STATUSES = (429, 503)


class NaiveBackoff:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic,
                 rand: Callable[[], float] = random.random) -> None:
        self._clock = clock
        self._rand = rand
        self._lock = threading.Lock()  # the waker runs on threads, the poller on the loop
        self._until = 0.0
        self._level = 0  # trips in a row without a success

    def remaining(self) -> float:
        """Seconds until Naïve may be called again (0: go)."""
        with self._lock:
            return max(0.0, self._until - self._clock())

    def blocked(self) -> bool:
        return self.remaining() > 0

    def trip(self, status: int, retry_after: float | None = None) -> float:
        """A 429/503 from Naïve: stop every call. Returns the wait."""
        with self._lock:
            now = self._clock()
            if retry_after is not None and retry_after > 0:
                wait = min(float(retry_after), MAX_SECONDS)
            else:
                wait = min(FIRST_SECONDS * 2 ** self._level, MAX_SECONDS)
            wait *= 1 + JITTER * (2 * self._rand() - 1)
            already = self._until > now
            self._until = max(self._until, now + wait)
            self._level += 1
            wait = self._until - now
        if not already:  # one line per backoff, however many callers hit it
            log.warning("Naïve answered %s: every Naïve call paused for %.0f s", status, wait)
        return wait

    def ok(self) -> None:
        """A successful call: the next trip starts at 30 s again."""
        with self._lock:
            if self._level and self._until <= self._clock():
                log.info("Naïve calls resumed")
            if self._until <= self._clock():
                self._level = 0


SHARED = NaiveBackoff()  # main.py hands this one to the poller and the waker

__all__ = ["NaiveBackoff", "SHARED", "TRIP_STATUSES"]
