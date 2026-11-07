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
    realtime_model = "gpt-realtime"
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
    monkeypatch.setattr(perform_mod, "agent_text", boom)
    turns = [{"speaker": "agent", "text": "x" * 4000}] * 1000
    ask = {"speaker": "customer", "reply_email": "a@b.co", "turns": turns}
    bad_email = h.gw.perform("voice", {**ask, "reply_email": "nope"})
    no_customer_text = h.gw.perform("voice", ask)  # agent turns only: "text is required"
    bad_channel = h.gw.perform("voice", {**ask, "channel": "fax"})
    retry = h.gw.perform("voice", {**ask, "request_id": "req-1"})
    assert {r.error["code"] for r in (bad_email, no_customer_text, bad_channel)} == {"bad_input"}
    assert retry.data["duplicate"] is True


def test_a_long_intake_is_kept_short_for_agents_not_refused(make_harness):
    # review: the page keeps up to 200 agent turns (talk.ts MAX_TURNS); agents get the
    # first 60 non-empty turns, each cut to 1000 characters before masking
    h = make_harness()
    turns = [{"speaker": "agent", "text": "y" * 5000}] * 200 + [
        {"speaker": "customer", "text": "Delaware C-corp, 10M authorized"}]
    out = h.gw.perform("voice", {"speaker": "customer", "reply_email": "a@b.co",
                                 "channel": "voice", "turns": turns})
    assert out.status == "done"
    stored = h.events("voice.command")[-1].private["turns"]
    assert len(stored) == 60 and all(len(t["text"]) <= 1000 for t in stored)


def test_past_1000_turns_is_too_long_and_counted(make_harness):
    h = make_harness()
    turns = [{"speaker": "agent", "text": "ok"}] * 1001
    with TestClient(create_app(h.gw, start_background=False)) as client:
        codes = [send(client, turns=turns).status_code for _ in range(35)]
    assert codes.count(429) == 5 and set(codes) == {400, 429}  # too_long is not refunded


def test_free_refusals_have_their_own_allowance(make_harness):
    # review: refunds are capped at 30 a minute per IP (= the Send limit), so one IP takes
    # at most 60 of the 180 global Send slots a minute
    h = make_harness()
    with TestClient(create_app(h.gw, start_background=False)) as client:
        codes = [send(client, reply_email="nope").status_code for _ in range(65)]
    assert codes[:60] == [400] * 60 and 429 in codes[60:]  # 30 free, then 30 counted


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


def test_only_openai_voice_is_booked_and_an_unbookable_mint_is_refused(make_harness):
    from app.gateway.ports import Rejected

    local = FakeVoice()
    local.provider = "local"
    h = make_harness()
    h.ports.voice = local
    with TestClient(create_app(h.gw, start_background=False)) as client:
        assert client.post("/api/voice/session").status_code == 200
    assert h.events("cost.booked") == []

    # review: a store that can't record the spend refuses the paid call up front
    from app.gateway.ports.store import EventWriteError

    h2 = make_harness()
    paid = h2.ports.voice = FakeVoice()

    def down():
        raise EventWriteError("disk full")

    h2.gw.store.ensure_writable = down
    with TestClient(create_app(h2.gw, start_background=False)) as client:
        assert client.post("/api/voice/session").status_code == 503
    assert paid.mints == 0


def test_a_failed_paid_call_books_nothing_and_frees_its_hold(make_harness):
    # review pass 3: a provider outage must not book phantom spend or use up the mints
    from app.gateway.ports import Unavailable

    class Down(FakeVoice):
        def create_session(self, instructions):
            raise Unavailable("openai 503")

        def speak(self, text):
            raise Unavailable("openai 503")

    h = make_harness(public_cases_per_day=1, spend_cap_usd=Decimal("0.30"))
    h.ports.voice = Down()
    brief = h.gw._emit([NewEvent(type="brief.generated", actor="cos", provider="local",
                                 payload={"text": "All quiet.", "counts": {}})])[0]
    with TestClient(create_app(h.gw, start_background=False)) as client:
        for _ in range(5):
            assert client.post("/api/voice/session").status_code == 503
            assert client.get("/api/brief/audio",
                              params={"event_id": brief.id}).status_code == 503
        assert h.events("cost.booked") == []
        assert h.gw._voice_pending == {"realtime": 0, "tts": 0}
        h.ports.voice = FakeVoice()  # the provider is back: mints work again today
        assert client.post("/api/voice/session").status_code == 200


def test_glued_addresses_are_all_masked():
    from app.gateway.masking import agent_text, mask_text

    for text in ("jane@x.com+bob@y.org", "a@b.com1x@c.org", "x_bob@y.io%c@d.co"):
        assert "@" not in agent_text(text), text
        assert "bob@y" not in mask_text(text, masked_name="Team #1", company=None)


def test_a_cut_turn_never_keeps_a_contact_split_at_the_edge(make_harness):
    # review pass 3: masking shrinks text, so the cut edge can move into the kept 1000
    h = make_harness()
    spoken = "jane " + " dot ".join(["x"] * 60) + " at example dot com. "
    phone = "call 415 555 0199 now"
    turn = spoken + "y" * (1190 - len(spoken)) + phone + "z" * 100
    h.gw.perform("voice", {"speaker": "customer", "reply_email": "a@b.co",
                           "turns": [{"speaker": "agent", "text": turn},
                                     {"speaker": "customer", "text": "Delaware C-corp"}]})
    stored = h.events("voice.command")[-1].private["turns"][0]["text"]
    assert "415" not in stored and "555" not in stored


def test_an_oversized_body_is_refused_before_parsing(make_harness):
    h = make_harness()
    with TestClient(create_app(h.gw, start_background=False)) as client:
        big = client.post("/api/voice", content=b'{"text": "' + b"x" * 1_000_001 + b'"}',
                          headers={"Content-Type": "application/json"})
        not_json = client.post("/api/voice", content=b"nope",
                               headers={"Content-Type": "application/json"})
    assert big.status_code == 413 and not_json.status_code == 400
