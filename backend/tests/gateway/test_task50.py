"""Task 50 (rehearsal): Naive answering 429 "at capacity" made every CEO turn a 503.
The turn now waits out the shared backoff within its 25 s budget, then answers pending and
keeps delivering in the background; GET collects. The CEO never repeats himself."""

from __future__ import annotations

import asyncio

import httpx

from app.ceo_talk import STILL_WORKING, CeoTalk
from app.naive import NaiveClient
from app.naive.backoff import NaiveBackoff

from ..naive_strict import strict
from .test_task33 import FakeAssistant


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    async def sleep(self, seconds: float) -> None:
        self.t += seconds
        await asyncio.sleep(0)


class Busy:
    """Naive at capacity for the next `n` calls (any route), then the inner fake."""

    def __init__(self, inner, n: int) -> None:
        self.inner, self.n, self.refused = inner, n, 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.n > 0:
            self.n -= 1
            self.refused += 1
            return httpx.Response(429, headers={"Retry-After": "1"}, json={"error": {
                "code": "rate_limited", "message": "the service is at capacity"}})
        return self.inner(request)


def busy_talker(h, fake, n):
    clock = Clock()
    busy = Busy(fake, n)
    backoff = NaiveBackoff(clock=clock, rand=lambda: 0.5)  # live: the SHARED gate
    client = NaiveClient("nk_test", transport=httpx.MockTransport(strict(busy)),
                         backoff=backoff, sleep=clock.sleep)
    return CeoTalk(h.gw, client, sleep=clock.sleep, clock=clock), busy, clock


def test_a_short_busy_spell_is_waited_out_inside_the_turn(h):
    fake = FakeAssistant(h.gw, ["Brief: nothing waiting."])
    talk, busy, clock = busy_talker(h, fake, 3)
    out = asyncio.run(talk.talk("sid_1", "what happened overnight"))
    assert out["say"] == "Brief: nothing waiting." and "pending" not in out
    assert busy.refused == 3 and fake.messages == ["what happened overnight"]
    assert clock.t < 25


def test_busy_past_the_budget_answers_pending_then_delivers_in_the_background(h):
    fake = FakeAssistant(h.gw, ["Done: emails go out on their own."])
    talk, busy, clock = busy_talker(h, fake, 10_000)

    async def go():
        first = await talk.talk("sid_1", "put emails on auto")
        assert first["pending"] is True and first["say"] == STILL_WORKING
        assert fake.messages == []  # nothing reached Naive yet
        again = await talk.check("sid_1", first["conversation_id"])
        assert again["pending"] is True  # still in the outbox: no 503
        busy.n = 0  # Naive recovers
        await talk.drain()
        later = await talk.check("sid_1", first["conversation_id"])
        return first, later

    first, later = asyncio.run(go())
    assert later["say"] == "Done: emails go out on their own." and "pending" not in later
    assert fake.messages == ["put emails on auto"]  # once: the CEO never repeats himself
    assert len(h.events("ceo.said")) == 1


def test_a_second_message_while_delivering_queues_behind_the_first(h, monkeypatch):
    from app import ceo_talk

    # the background delivery runs on the fake clock while the second turn awaits a thread
    monkeypatch.setattr(ceo_talk, "DELIVER_SECONDS", 1e12)
    fake = FakeAssistant(h.gw, ["one", "two"])
    talk, busy, clock = busy_talker(h, fake, 10**9)

    async def go():
        first = await talk.talk("sid_1", "first thing")
        second = await talk.talk("sid_1", "second thing", first["conversation_id"])
        assert second["pending"] is True and second["conversation_id"] == \
            first["conversation_id"]
        busy.n = 0
        await talk.drain()
        return await talk.check("sid_1", first["conversation_id"])

    later = asyncio.run(go())
    assert fake.messages == ["first thing", "second thing"]  # in order, each once
    assert later["say"] == "two"


def test_a_stop_while_naive_is_busy_is_still_instant(h):
    fake = FakeAssistant(h.gw, ["Hi."])
    talk, busy, clock = busy_talker(h, fake, 10_000)
    out = asyncio.run(talk.talk("sid_1", "stop posting"))
    assert out["say"] and h.gw.state.trust["social_post"] == "PAUSED"
    assert busy.refused == 0 and clock.t == 0  # no Naive call, no wait


def test_a_hard_naive_refusal_is_still_a_503_and_nothing_is_resent(h):
    fake = FakeAssistant(h.gw, ["later"])

    def refuse(request):
        if request.method == "POST":
            return httpx.Response(402, json={"error": {"code": "insufficient_credits",
                                                       "message": "no credits"}})
        return fake(request)

    clock = Clock()
    client = NaiveClient("nk_test", transport=httpx.MockTransport(strict(refuse)),
                         backoff=NaiveBackoff(clock=clock), sleep=clock.sleep)
    talk = CeoTalk(h.gw, client, sleep=clock.sleep, clock=clock)
    try:
        asyncio.run(talk.talk("sid_1", "hello"))
        raise AssertionError("expected a 503")
    except Exception as err:  # noqa: BLE001
        assert getattr(err, "status", None) == 503
    [conversation] = talk.conversations.values()
    assert conversation["outbox"] == [] and conversation["pending"] is False


# ---------------------------------------------------------------- X posts (part 2)


def test_a_timed_out_x_post_then_409_in_flight_is_final_channel_failed(tmp_path):
    from app.gateway.adapters.naive_social import SOCIAL_TIMEOUT, NaiveSocial
    from app.gateway.ports import ChannelFailed, Post, Unavailable

    assert SOCIAL_TIMEOUT.read >= 25  # Naive posts to X synchronously (image upload first)
    keys = []

    def naive(request):
        keys.append(request.headers["Idempotency-Key"])
        if len(keys) == 1:
            raise httpx.ReadTimeout("slow X", request=request)
        return httpx.Response(409, json={"error": {
            "type": "conflict", "code": "idempotency_conflict",
            "message": "a request with this Idempotency-Key is still in flight"}})

    port = NaiveSocial("sk_test", identity="utax-mkt", data_dir=tmp_path,
                       transport=httpx.MockTransport(naive))
    post = Post(channel="x", text="Free checkup", image_url=None)
    try:
        port.post(post, "k")
        raise AssertionError("expected Unavailable")
    except Unavailable:
        pass
    try:
        port.post(post, "k")
        raise AssertionError("expected ChannelFailed")
    except ChannelFailed as err:
        assert not err.retryable and "don't retry" in str(err)
    assert len(set(keys)) == 1  # the same key every time: never a second X post


def test_post_social_answers_channel_failed_once_and_keeps_the_error(h):
    from app.gateway.ports import ChannelFailed, Unavailable

    first = h.do("post_social", channel="x", text="Delaware C-corp? Free checkup.")
    h.approve_pending(first)
    h.ports.social.faults.add(Unavailable("naive timed out"),
                              ChannelFailed("x: the post didn't go through. Final: don't "
                                            "retry; say so on the card"),
                              Unavailable("never reached"))
    out = h.do("post_social", channel="x", text="Delaware C-corp? Free checkup.")
    assert out.status == "error" and out.error["code"] == "channel_failed"
    assert "don't retry" in out.error["message"]
    assert len(h.ports.social.faults._faults) == 1  # two tries, then final: no third
    [failed] = [e for e in h.events("post.published") if e.status == "failed"]
    assert failed.private["error"].startswith("channel_failed: ")
    public = h.gw.public_state(admin=False)
    assert "don't retry" not in str(public)  # the error stays private
