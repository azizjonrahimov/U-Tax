"""Task 49 (rehearsal): Naive's list limit is 1..100. ceo_talk asked for 200 and every CEO
turn answered 503. NaiveClient clamps every list call; ceo_talk pages to the latest reply."""

from __future__ import annotations

import asyncio

import httpx

from app.naive import NaiveClient

from ..naive_strict import strict
from .test_task33 import FakeAssistant, talker


def test_a_turn_reads_the_reply_past_100_events(h):
    fake = FakeAssistant(h.gw, ["first", "the latest"])
    talk = talker(h, fake)
    out = asyncio.run(talk.talk("sid_1", "hello"))
    assert out["say"] == "first"
    for seq in range(250):  # a long tool-heavy turn: far more than one page of events
        fake.events.append({"seq": len(fake.events) + 1, "type": "tool.call",
                            "data": {"n": seq}})
    fake.events.append({"seq": len(fake.events) + 1, "type": "message.completed",
                        "data": {"role": "user", "content": "x"}})
    fake.events.append({"seq": len(fake.events) + 1, "type": "message.completed",
                        "data": {"role": "assistant", "content": "the latest"}})
    fake.replies = []
    conversation = talk.conversations[out["conversation_id"]]
    assert asyncio.run(talk._last_message(conversation)) == "the latest"
    assert conversation["after_seq"] == len(fake.events)


def test_the_client_clamps_every_list_limit_to_1_100():
    seen: list[str] = []

    def naive(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params.get("limit"))
        return httpx.Response(200, json={"data": [], "has_more": False})

    client = NaiveClient("nk_test", transport=httpx.MockTransport(strict(naive)))

    async def go():
        await client.session_events("ses_1", after_seq=0, limit=200)
        await client.list_sessions(limit=500)
        await client.list_agents(limit=0)
        await client.list_agents()
        async for _ in client.paginate("/agents", {"limit": 1000}):
            pass

    asyncio.run(go())
    assert seen == ["100", "100", "1", None, "100"]


def test_the_strict_fake_rejects_a_limit_over_100_like_the_real_naive():
    calls = []
    handler = strict(lambda r: calls.append(r) or httpx.Response(200, json={}))
    r = handler(httpx.Request("GET", "https://x/v1/sessions/s/events?limit=200"))
    assert r.status_code == 400 and r.json()["error"]["code"] == "validation_failed"
    assert handler(httpx.Request("GET", "https://x/v1/agents?limit=100")).status_code == 200
    assert len(calls) == 1
