"""Task 42 (design D37): the agents' live Naïve activity as agent.activity (admin and
session readers only), the artifacts as sent for admin readers, doc.step cells, and the
payment receipt only for exactly $49."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api import sse_frame
from app.main import create_app
from app.naive import NaiveClient
from app.naive.backoff import NaiveBackoff
from app.naive_activity import ActivityRelay, _Session

ADMIN = "admin-" + "a" * 30
THEATER = "theater-" + "b" * 30
SID = "ses_1"


def sse(events: list[dict]) -> str:
    return "".join(f"id: {e['seq']}\ndata: {json.dumps(e)}\n\n" for e in events)


def ev(seq, type_, **data):
    return {"seq": seq, "type": type_, "session_id": SID, "data": data}


RUN = [
    ev(1, "message.completed", role="user", content="CEO order order_12: follow up case_1"),
    ev(2, "session.running"),
    ev(3, "tool.started", name="utax.get_case", args={"case_id": "case_1"}),
    ev(4, "tool.completed", name="utax.get_case", output="{...}", is_error=False),
    ev(5, "message.delta", delta="Drafting the follow-up to "),
    ev(6, "message.delta", delta="jo@team7.dev about the "),
    ev(7, "message.delta", delta="missing share count."),
    ev(8, "message.completed", role="assistant",
       content="Drafting the follow-up to jo@team7.dev about the missing share count. Sent."),
    ev(9, "session.idle", stop_reason="end_turn"),
]


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def client_for(handler, backoff=None):
    return NaiveClient("nk_test", "https://api.vetta.sh",
                       transport=httpx.MockTransport(handler), backoff=backoff)


def streaming(events, seen=None):
    async def handler(request: httpx.Request):
        if seen is not None:
            seen.append((request.url.path, dict(request.url.params)))
        return httpx.Response(200, text=sse(events),
                              headers={"content-type": "text/event-stream"})
    return handler


def activity(h):
    return [e.payload for e in h.events("agent.activity")]


def follow(h, events, actor="intake", clock=None, seen=None, backoff=None):
    relay = ActivityRelay(h.gw, client_for(streaming(events, seen), backoff),
                          clock=clock or Clock())
    relay.sessions[SID] = _Session(actor)
    asyncio.run(relay.follow(SID))
    return relay


# ---------------------------------------------------------------- the relay


def test_a_run_becomes_thinking_tools_writing_and_idle_with_the_order(h):
    seen = []
    follow(h, RUN, seen=seen)
    assert seen == [(f"/v1/sessions/{SID}/stream", {"after_seq": "0"})]
    got = activity(h)
    assert [a["kind"] for a in got] == ["thinking", "tool_started", "tool_done", "writing",
                                        "writing", "idle"]
    assert got[1]["tool"] == "utax.get_case" and "args" not in got[1]
    assert all(a["order_id"] == "order_12" and a["agent"] == "intake"
               and a["session_id"] == SID for a in got)
    final = got[4]["text"]
    assert "jo@team7.dev" not in final and "missing share count. Sent." in final
    [e] = h.events("agent.activity")[:1]
    assert e.actor == "intake" and e.provider == "naive"


def test_writing_is_at_most_once_a_second_per_session(h):
    deltas = [ev(i, "message.delta", delta=f"word{i} ") for i in range(1, 6)]

    class Ticking(Clock):
        def __call__(self):
            self.now += 0.3  # each event 0.3 s after the last
            return self.now

    follow(h, deltas + [ev(9, "session.idle")], clock=Ticking())
    writing = [a for a in activity(h) if a["kind"] == "writing"]
    assert 1 < len(writing) < 5  # throttled, and the rest flushed before idle
    assert writing[-1]["text"].endswith("word5")  # accumulated


def test_the_text_is_capped_at_600_characters_the_latest_ones(h):
    long = "x" * 700 + " the end"
    follow(h, [ev(1, "message.completed", role="assistant", content=long),
               ev(2, "session.idle")])
    [writing] = [a for a in activity(h) if a["kind"] == "writing"]
    assert len(writing["text"]) == 600 and writing["text"].endswith("the end")
    assert writing["text"].startswith("…")


def test_the_ceo_assistant_is_logged_as_its_own_actor(h):
    follow(h, [ev(1, "session.running"), ev(2, "session.idle")], actor="ceo")
    assert {e.actor for e in h.events("agent.activity")} == {"ceo_assistant"}


def test_a_resumed_stream_never_doubles_an_event(h):
    relay = follow(h, RUN)
    count = len(activity(h))
    relay.sessions[SID].last_seq = 0  # a replay of the same seqs
    asyncio.run(relay.follow(SID))
    assert len(activity(h)) == count


def test_observe_streams_only_our_running_sessions_and_never_under_backoff(h):
    async def run():
        relay = ActivityRelay(h.gw, client_for(streaming([ev(1, "session.idle")])))
        rows = [{"id": "ses_a", "agent_id": "ag_intake", "status": "running"},
                {"id": "ses_b", "agent_id": "ag_intake", "status": "idle"},
                {"id": "ses_c", "agent_id": "ag_other", "status": "running"}]
        started = relay.observe(rows, {"ag_intake": "intake"})
        again = relay.observe(rows, {"ag_intake": "intake"})  # already streaming
        await asyncio.gather(*relay.tasks.values())
        return started, again

    assert asyncio.run(run()) == (["ses_a"], [])

    async def blocked():
        backoff = NaiveBackoff()
        backoff.trip(429, 30)
        relay = ActivityRelay(h.gw, client_for(streaming(RUN), backoff))
        return relay.observe([{"id": "ses_a", "agent_id": "ag", "status": "running"}],
                             {"ag": "intake"})

    assert asyncio.run(blocked()) == []


def test_a_429_on_the_stream_trips_the_backoff_and_stops(h):
    backoff = NaiveBackoff()

    async def handler(request):
        return httpx.Response(429, json={"error": {"code": "rate_limited"}},
                              headers={"Retry-After": "30"})

    relay = ActivityRelay(h.gw, client_for(handler, backoff))
    relay.sessions[SID] = _Session("intake")
    asyncio.run(relay.follow(SID))
    assert backoff.blocked() and activity(h) == []


def test_only_the_relay_writes_activity_never_an_agent_token(h):
    out = h.gw.perform("agent_activity", {"agent": "intake", "session_id": SID,
                                          "kind": "thinking"}, actor="intake")
    assert out.status == "refused" and out.error["code"] == "tool_not_allowed"
    out = h.do("agent_activity", agent="intake", session_id=SID, kind="dancing")
    assert out.status == "refused"


# ---------------------------------------------------------------- who sees what


@pytest.fixture
def secured(make_harness):
    h = make_harness(admin_token=ADMIN, theater_token=THEATER, session_secret="s" * 48)
    follow(h, RUN)
    return h


def test_theater_readers_never_get_agent_activity(secured):
    h = secured
    with TestClient(create_app(h.gw, start_background=False)) as c:
        theater = c.get("/api/events", params={"token": THEATER, "limit": 2000}).json()
        admin = c.get("/api/events", params={"limit": 2000},
                      headers={"Authorization": f"Bearer {ADMIN}"}).json()
    assert not [e for e in theater["data"] if e["type"] == "agent.activity"]
    assert len([e for e in admin["data"] if e["type"] == "agent.activity"]) == 6
    [event] = h.events("agent.activity")[:1]
    assert sse_frame(event, lambda e: h.gw.public_event(e, admin=False)) == ""
    assert sse_frame(event, lambda e: h.gw.public_event(e, admin=True)).startswith("id: ")


def test_admin_readers_get_the_memo_as_sent_and_the_theater_the_masked_one(h):
    h.to_sent()
    [memo] = h.events("memo.sent")
    theater, admin = h.gw.public_event(memo), h.gw.public_event(memo, admin=True)
    assert "sent" not in theater["payload"]
    sent = admin["payload"]["sent"]
    assert sent["to_masked"] == memo.payload["to_masked"]
    assert sent["subject"] == memo.payload["subject"]
    assert sent["body"].startswith("Hi Team7")  # the greeting the client really got
    assert theater["payload"]["body"] == memo.payload["body"]  # masked, as before


def test_doc_steps_carry_their_cells_for_admin_readers(h):
    h.to_qa_passed()
    steps = h.events("doc.step")
    sheet = next(e for e in steps if e.payload["detail"].startswith("Comparison"))
    cells = h.gw.public_event(sheet, admin=True)["payload"]["cells"]
    assert {"sheet": "Comparison", "cell": "A4", "value": "Tax owed: the lower of the two",
            "fmt": "text"} in cells
    assert "cells" not in h.gw.public_event(sheet)["payload"]
    books = next(e for e in steps if e.payload["doc"] == "books")
    assert any(c["value"] == "case_1" for c in
               h.gw.public_event(books, admin=True)["payload"]["cells"])


# ---------------------------------------------------------------- the receipt guard


def test_a_payment_that_isnt_49_dollars_goes_to_the_ceo_only(make_harness):
    from app.gateway.adapters.stripe_pay import sign

    h = make_harness(admin_token=ADMIN, stripe_webhook_secret="whsec_" + "s" * 32,
                     ceo_email="ceo@u-tax.test", hr_booking_url="https://cal.u-tax.test/x")
    case_id = h.to_sent()
    event = {"id": "evt_1", "type": "checkout.session.completed", "livemode": False,
             "data": {"object": {"id": "cs_1", "payment_status": "paid", "amount_total": 5000,
                                 "currency": "usd", "metadata": {"case_id": case_id}}}}
    raw = json.dumps(event).encode()
    import time

    with TestClient(create_app(h.gw, start_background=False)) as c:
        out = c.post("/hooks/stripe", content=raw, headers={
            "Stripe-Signature": sign("whsec_" + "s" * 32, raw, int(time.time()))})
    assert out.json()["receipt"] == "unexpected_amount"
    assert h.events("client.followed_up") == []
    [notice] = [e for e in h.events("ceo.notified") if e.payload["reason"] == "paid"]
    assert notice.payload["subject"] == "Payment of $50 received: check it"
