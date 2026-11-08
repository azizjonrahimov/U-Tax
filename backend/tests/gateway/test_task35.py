"""Task 35: one shared Naïve backoff (the poller, its recovery and the waker), one list call
per poll pass, wakes held until the backoff clears."""

from __future__ import annotations

import asyncio
import logging

import httpx
import pytest

from app.naive import NaiveClient, NaiveError
from app.naive.backoff import NaiveBackoff
from app.naive_confirm import AGENT_NAMES, POLL_SECONDS, NaiveConfirmer
from app.naive_waker import NaiveWaker, WakeFailed

from .test_task19 import FakeNaive, tool_call, waiting


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def backoff(clock):
    return NaiveBackoff(clock=clock, rand=lambda: 0.5)  # 0.5: no jitter


class AtCapacity:
    """Naïve at capacity: every call answers 429 (no Retry-After) until `cleared`."""

    def __init__(self, inner, retry_after: str | None = None) -> None:
        self.inner, self.retry_after, self.cleared = inner, retry_after, False
        self.calls: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(f"{request.method} {request.url.path}?{request.url.query.decode()}")
        if self.cleared:
            return self.inner(request)
        headers = {"Retry-After": self.retry_after} if self.retry_after else {}
        return httpx.Response(429, headers=headers, json={"error": {
            "code": "rate_limited", "message": "the service is at capacity"}})


def test_the_backoff_doubles_from_30_s_to_5_min_and_honours_retry_after(backoff, clock):
    assert [backoff.trip(429) for _ in range(5)][0] == 30
    clock.t += 1000
    assert backoff.trip(429) == 300  # capped (level 5: 960 s)
    clock.t += 1000
    backoff.ok()  # a success: the next trip starts at 30 s again
    assert backoff.trip(503) == 30
    clock.t += 1000
    assert backoff.trip(429, retry_after=12) == 12
    jittered = NaiveBackoff(clock=clock, rand=lambda: 1.0)
    assert jittered.trip(429) == pytest.approx(36)  # +20%


def test_a_429_stops_every_call_with_no_retry_and_one_warning(backoff, clock, caplog):
    fake = AtCapacity(lambda r: httpx.Response(200, json={"data": [], "has_more": False}))

    async def go():
        client = NaiveClient("nk_test", transport=httpx.MockTransport(fake), backoff=backoff,
                             sleep=lambda s: asyncio.sleep(0))
        try:
            with pytest.raises(NaiveError) as first:
                await client.list_sessions(limit=50)
            assert first.value.status_code == 429 and len(fake.calls) == 1  # no retry
            for _ in range(5):  # everyone else during the backoff: nothing leaves
                with pytest.raises(NaiveError) as held:
                    await client.get_session("ses_1")
                assert held.value.code == "backoff"
            assert len(fake.calls) == 1
            clock.t += 31
            fake.cleared = True
            await client.list_sessions(limit=50)
            assert len(fake.calls) == 2
        finally:
            await client.aclose()

    with caplog.at_level(logging.WARNING, logger="utax.naive_backoff"):
        asyncio.run(go())
    assert len([r for r in caplog.records if r.name == "utax.naive_backoff"]) == 1


def test_one_list_call_per_pass_and_none_while_backed_off(h, backoff, clock):
    inner = FakeNaive(h.gw, {"ses_1": waiting("tax", tool_call("utax.compute_tax", "tc1"))})
    fake = AtCapacity(inner)

    async def go():
        client = NaiveClient("nk_test", transport=httpx.MockTransport(fake), backoff=backoff)
        confirmer = NaiveConfirmer(h.gw, client)
        confirmer.agents = {f"agt_{name}": seat for name, seat in AGENT_NAMES.items()}
        try:
            assert await confirmer.poll_once() == 0
            assert fake.calls == ["GET /v1/sessions?limit=50"]  # one call for all 7 seats
            for _ in range(2):  # 10 s polls inside the 30 s backoff: no call at all
                clock.t += POLL_SECONDS
                assert await confirmer.poll_once() == 0
            assert len(fake.calls) == 1
            clock.t += 31
            fake.cleared = True
            assert await confirmer.poll_once() == 1  # the tax confirm is answered
            lists = [c for c in fake.calls if c.startswith("GET /v1/sessions?")]
            assert lists == ["GET /v1/sessions?limit=50"] * 2
            assert inner.confirms[0]["tool_call_id"] == "tc1"
        finally:
            await client.aclose()

    asyncio.run(go())
    assert POLL_SECONDS == 10.0


def test_a_wake_held_by_the_backoff_goes_once_it_clears(h, backoff, clock, monkeypatch):
    posts: list[str] = []

    def naive(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/agents":
            return httpx.Response(200, json={"data": [{"id": "agt_cos", "name": "chief-of-staff",
                                                       "metadata": {"utax_actor": "cos"}}],
                                             "has_more": False})
        posts.append(request.url.path)
        if len(posts) == 1:
            return httpx.Response(429, headers={"Retry-After": "20"},
                                  json={"error": {"code": "rate_limited", "message": "x"}})
        return httpx.Response(201, json={"id": "ses_9", "status": "running"})

    waker = NaiveWaker("nk_test", transport=httpx.MockTransport(naive), clock=clock,
                       backoff=backoff)
    with pytest.raises(WakeFailed) as failed:
        waker("New case case_1")
    assert failed.value.retry_after == 20 and backoff.remaining() == 20
    with pytest.raises(WakeFailed):
        waker("Approval apr_1 allowed for case_1")  # held: no request at all
    assert len(posts) == 1
    # the gateway queues a held wake for when the backoff clears, at the same attempt
    import app.gateway.perform as perform

    now = {"t": 5000.0}
    monkeypatch.setattr(perform.time, "monotonic", lambda: now["t"])
    h.gw.on_wake(waker)
    h.gw._try_wake("New case case_1", waker, attempt=0)
    [(message, _, attempt, due)] = h.gw._wake_retries
    assert message == "New case case_1" and attempt == 0 and due == pytest.approx(5021)
    clock.t += 21
    now["t"] += 21
    assert h.gw.retry_wakes() == 1 and len(posts) == 2 and not h.gw._wake_retries
