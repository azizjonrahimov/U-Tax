"""Task 13: protect the overnight run from public /talk floods. A daily cap on public cases
(429 at_capacity + one CEO notice), retries and bad input don't use the Send budget, and
voice spend (Realtime mints, TTS briefs) is booked so SPEND_CAP_USD sees it. All mocked."""

from __future__ import annotations

from decimal import Decimal

from fastapi.testclient import TestClient

from app.gateway.events import NewEvent
from app.gateway.ports import VoiceSession
from app.gateway.pricing import VOICE_USD
from app.main import create_app


def send(client, n=0, **extra):
    body = {"text": "Delaware C-corp, 10M authorized", "channel": "typed",
            "speaker": "customer", "reply_email": f"judge{n}@yc.com", **extra}
    return client.post("/api/voice", json=body)


class FakeVoice:
    provider = "openai"
    tts_model = "gpt-4o-mini-tts"

    def __init__(self):
        self.mints = self.speeches = 0

    def create_session(self, instructions):
        self.mints += 1
        return VoiceSession(client_secret="ek", model="gpt-realtime", expires_at="x")

    def speak(self, text):
        self.speeches += 1
        return b"mp3"


# ---------------------------------------------------------------- a. daily public-case cap


def test_public_cases_stop_at_the_daily_cap_with_one_ceo_notice(make_harness):
    h = make_harness(public_cases_per_day=2, ceo_email="ceo@u-tax.dev",
                     inbox_clients="clients@u-tax.dev")
    with TestClient(create_app(h.gw, start_background=False)) as client:
        assert send(client, 1).status_code == 200
        assert send(client, 2).status_code == 200
        over = [send(client, 3), send(client, 4)]
    for r in over:
        assert r.status_code == 429
        assert r.json()["error"]["code"] == "at_capacity"
        assert "clients@u-tax.dev" in r.json()["error"]["message"]
    assert len(h.gw.state.cases) == 2
    notices = [e for e in h.events("ceo.notified") if e.payload["reason"] == "paused"]
    assert len(notices) == 1 and "at capacity" in notices[0].payload["subject"]


def test_the_cap_lifts_the_next_utc_day_and_survives_a_restart(make_harness):
    h = make_harness(public_cases_per_day=1, ceo_email="ceo@u-tax.dev")
    assert h.gw.perform("voice", {"text": "hi", "speaker": "customer",
                                  "reply_email": "a@b.co"}).status == "done"
    gw = h.restart()  # the count is rebuilt from the log
    over = gw.perform("voice", {"text": "hi", "speaker": "customer", "reply_email": "c@d.co"})
    assert over.status == "refused" and over.error["code"] == "at_capacity"
    h.clock.advance(days=1)
    assert gw.perform("voice", {"text": "hi", "speaker": "customer",
                                "reply_email": "c@d.co"}).status == "done"


def test_email_and_ceo_orders_never_count_against_the_cap(make_harness):
    h = make_harness(public_cases_per_day=1)
    h.ok("inject_email", **{"from": "founder@team7.dev", "subject": "x", "body": "hello"})
    h.gw.perform("voice", {"text": "status?", "speaker": "ceo"})
    assert h.gw.perform("voice", {"text": "hi", "speaker": "customer",
                                  "reply_email": "a@b.co"}).status == "done"


# ---------------------------------------------------------------- c. no charge for retries


def test_a_retried_request_id_uses_neither_the_limiter_nor_the_cap(make_harness):
    h = make_harness(public_cases_per_day=1)
    with TestClient(create_app(h.gw, start_background=False)) as client:
        first = send(client, request_id="req-1")
        retries = [send(client, request_id="req-1") for _ in range(40)]  # > the 30/min bucket
    assert first.status_code == 200
    assert all(r.status_code == 200 and r.json()["duplicate"] for r in retries)
    assert len(h.gw.state.cases) == 1


def test_bad_input_never_uses_the_send_budget(make_harness):
    h = make_harness()
    with TestClient(create_app(h.gw, start_background=False)) as client:
        bad = [send(client, reply_email="not an email").status_code for _ in range(40)]
        assert set(bad) == {400}
        assert send(client).status_code == 200  # the whole 30/min bucket is still there


def test_real_sends_still_hit_the_limiter(make_harness):
    h = make_harness()
    with TestClient(create_app(h.gw, start_background=False)) as client:
        codes = [send(client, i).status_code for i in range(35)]
    assert codes.count(429) == 5


# ---------------------------------------------------------------- d. voice spend is booked


def test_each_mint_and_tts_brief_books_an_estimated_cost(make_harness):
    h = make_harness()
    h.ports.voice = FakeVoice()
    brief = h.gw._emit([NewEvent(type="brief.generated", actor="cos", provider="local",
                                 payload={"text": "All quiet.", "counts": {}})])[0]
    with TestClient(create_app(h.gw, start_background=False)) as client:
        assert client.post("/api/voice/session").status_code == 200
        assert client.get("/api/brief/audio", params={"event_id": brief.id}).status_code == 200
        client.get("/api/brief/audio", params={"event_id": brief.id})  # cached: no new call
    booked = h.events("cost.booked")
    assert [(e.payload["model"], e.cost_usd, e.payload["estimated"]) for e in booked] == [
        ("realtime:gpt-realtime", "0.3000", True), ("tts:gpt-4o-mini-tts", "0.0200", True)]
    assert h.gw.state.spent_by_provider["openai"] == VOICE_USD["realtime"] + VOICE_USD["tts"]


def test_paid_voice_stops_at_the_spend_cap(make_harness):
    h = make_harness(spend_cap_usd=Decimal("0.25"))
    voice = h.ports.voice = FakeVoice()
    brief = h.gw._emit([NewEvent(type="brief.generated", actor="cos", provider="local",
                                 payload={"text": "All quiet.", "counts": {}})])[0]
    with TestClient(create_app(h.gw, start_background=False)) as client:
        assert client.post("/api/voice/session").status_code == 200  # $0.30 now spent
        again = client.post("/api/voice/session")
        audio = client.get("/api/brief/audio", params={"event_id": brief.id})
        assert send(client).status_code == 200  # typed chat keeps working
    assert again.status_code == 503 and again.json()["error"]["code"] == "voice_unavailable"
    assert audio.status_code == 503
    assert voice.mints == 1 and voice.speeches == 0


# ---------------------------------------------------------------- /review regressions


def test_uncounted_refusals_do_no_per_turn_work(make_harness, monkeypatch):
    # review: bad_input and retries skip the Send limiter, so they must stay cheap: the
    # reply_email check and the request_id lookup run before any turn is processed
    import app.gateway.perform as perform_mod

    h = make_harness()
    h.gw.perform("voice", {"text": "hi", "speaker": "customer", "reply_email": "a@b.co",
                           "request_id": "req-1"})

    def boom(*_):
        raise AssertionError("turns were processed")

    monkeypatch.setattr(perform_mod, "redact_ids", boom)
    turns = [{"speaker": "agent", "text": "x" * 3000}] * 100
    bad = h.gw.perform("voice", {"speaker": "customer", "reply_email": "nope", "turns": turns})
    retry = h.gw.perform("voice", {"speaker": "customer", "reply_email": "a@b.co",
                                   "request_id": "req-1", "turns": turns})
    assert bad.error["code"] == "bad_input" and retry.data["duplicate"] is True


def test_oversized_turns_are_refused_and_counted(make_harness):
    h = make_harness()
    turns = [{"speaker": "agent", "text": "ok"}] * 121
    with TestClient(create_app(h.gw, start_background=False)) as client:
        codes = [send(client, turns=turns).status_code for _ in range(35)]
    assert codes.count(429) == 5 and set(codes) == {409, 429}  # too_long is not refunded
    long_turn = [{"speaker": "agent", "text": "x" * 4001}]
    assert h.gw.perform("voice", {"speaker": "customer", "reply_email": "a@b.co",
                                  "turns": long_turn}).error["code"] == "too_long"


def test_refunds_give_back_the_per_ip_hit_but_not_the_global_one():
    from app.api import RateLimiter
    from app.errors import APIError

    limiter = RateLimiter(limit=2, global_limit=3)
    for _ in range(3):
        limiter.refund("ip1", limiter.check("ip1"))  # one IP, refunded: never stops it ...
    try:
        limiter.check("ip2")  # ... but the global window still holds all three
    except APIError as err:
        assert err.code == "rate_limited"
    else:
        raise AssertionError("the global cap was refunded")


def test_a_failed_capacity_notice_is_retried_later_not_every_request(make_harness, monkeypatch):
    import app.gateway.perform as perform_mod

    h = make_harness(public_cases_per_day=0, ceo_email="ceo@u-tax.dev")
    calls = []
    monkeypatch.setattr(h.gw, "_notify_ceo", lambda *a, **k: calls.append(k["key"]) or False)
    ask = {"text": "hi", "speaker": "customer", "reply_email": "a@b.co"}
    for _ in range(3):
        assert h.gw.perform("voice", ask).error["code"] == "at_capacity"
    assert len(calls) == 1
    now = perform_mod.time.monotonic()
    monkeypatch.setattr(perform_mod.time, "monotonic", lambda: now + 601)
    h.gw.perform("voice", ask)
    assert len(calls) == 2


def test_one_capacity_notice_a_day_across_a_restart(make_harness):
    h = make_harness(public_cases_per_day=0, ceo_email="ceo@u-tax.dev")
    ask = {"text": "hi", "speaker": "customer", "reply_email": "a@b.co"}
    h.gw.perform("voice", ask)
    h.restart().perform("voice", ask)
    assert len([e for e in h.events("ceo.notified") if e.payload["reason"] == "paused"]) == 1


def test_event_mode_takes_more_public_cases_by_default(make_harness):
    assert make_harness().gw.public_case_cap() == 50
    assert make_harness(public_mode="event").gw.public_case_cap() == 200
    assert make_harness(public_mode="event", public_cases_per_day=7).gw.public_case_cap() == 7


def test_mints_stop_at_the_case_cap_and_at_twice_it(make_harness):
    h = make_harness(public_cases_per_day=1)
    voice = h.ports.voice = FakeVoice()
    with TestClient(create_app(h.gw, start_background=False)) as client:
        mints = [client.post("/api/voice/session").status_code for _ in range(3)]
        assert mints == [200, 200, 503]  # 2 x PUBLIC_CASES_PER_DAY a day
        h.clock.advance(days=1)
        assert client.post("/api/voice/session").status_code == 200
        assert send(client).status_code == 200  # today's one public case
        assert client.post("/api/voice/session").status_code == 503  # at capacity: no mint
    assert voice.mints == 3


def test_spend_exactly_at_the_cap_stops_paid_voice(make_harness):
    h = make_harness(spend_cap_usd=VOICE_USD["realtime"])
    h.ports.voice = FakeVoice()
    with TestClient(create_app(h.gw, start_background=False)) as client:
        assert client.post("/api/voice/session").status_code == 200
        assert client.post("/api/voice/session").status_code == 503


def test_only_openai_voice_is_booked_and_a_failed_booking_never_fails_the_mint(make_harness):
    from app.gateway.ports import Rejected

    local = FakeVoice()
    local.provider = "local"
    h = make_harness()
    h.ports.voice = local
    with TestClient(create_app(h.gw, start_background=False)) as client:
        assert client.post("/api/voice/session").status_code == 200
    assert h.events("cost.booked") == []

    h2 = make_harness()
    h2.ports.voice = FakeVoice()

    def refuse(args):
        raise Rejected("store says no")

    h2.gw._handlers["book_spend"] = refuse
    with TestClient(create_app(h2.gw, start_background=False)) as client:
        assert client.post("/api/voice/session").status_code == 200
