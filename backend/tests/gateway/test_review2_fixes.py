"""Regression tests for the second /review pass (task 6), one per confirmed finding."""

import httpx
import pytest
from fastapi.testclient import TestClient

from app.driver import LocalCrew
from app.gateway.masking import mask_text
from app.main import create_app

from .conftest import HAPPY_BODY, HAPPY_QUOTES


# ---------------------------------------------------------------- approvals / trust


def test_superseded_approvals_wake_their_owners(h):
    wakes = []
    h.gw.on_wake(wakes.append)
    a = h.to_qa_passed()
    b = h.to_qa_passed(h.new_case(sender="b@team2.dev"))
    first = h.do("send_memo", case_id=a)
    second = h.do("send_memo", case_id=b)
    h.ok("approve", approval_id=first.approval_id, decision="allow")
    assert f"Approval {second.approval_id} allowed for {b}" in wakes  # b isn't stranded


def test_superseded_questionnaire_is_sent_by_the_gateway(h):
    for sender in ("a@x.io", "b@y.io"):
        h.ok("inject_email", **{"from": sender, "subject": "gig", "body": "EA",
                                "inbox": "hiring"})
    first = h.events("approval.requested")[0].payload["approval_id"]
    h.ok("approve", approval_id=first, decision="allow")
    assert len(h.events("hr.questionnaire_sent")) == 2  # both, not only the approved one


def test_approving_while_paused_keeps_the_ceo_order(h):
    case_id = h.to_qa_passed()
    pending = h.do("send_memo", case_id=case_id)
    h.ok("set_trust", action_type="email", state="PAUSED")
    h.ok("approve", approval_id=pending.approval_id, decision="allow")
    assert h.gw.state.trust["email"] == "PAUSED"
    assert h.do("send_memo", case_id=case_id).error["code"] == "paused"


def test_public_cases_never_pause_firm_email(make_harness):
    h = make_harness(cap_per_hour=2)
    h.to_sent()  # email -> AUTO
    for i in range(12):  # anonymous /talk cases
        cid = h.ok("voice", text="hi", speaker="customer", reply_email=f"x{i}@junk.io")["case_id"]
        h.ok("extract_facts", case_id=cid, quotes={}, incorporated_delaware=None,
             foreign_owner_25=None)
        result = h.do("ask_client", case_id=cid)
        assert result.status in ("done", "refused")
        if result.status == "refused":
            assert result.error["code"] == "public_cap"
    assert h.gw.state.trust["email"] == "AUTO"  # the firm keeps sending
    real = h.to_qa_passed(h.new_case(sender="real@client.dev"))
    assert h.ok("send_memo", case_id=real)["message_id"]


# ---------------------------------------------------------------- mail loops


def test_not_eligible_mail_is_sent_once(h):
    case_id = h.new_case(body="We're an LLC in Wyoming.", sender="x@wyo.co")
    h.approve_pending(h.do("mark_not_eligible", case_id=case_id, reason="not_a_corporation"))
    h.ok("mark_not_eligible", case_id=case_id, reason="not_a_corporation")
    h.new_case(body="why?", sender="x@wyo.co")  # an auto-responder or a follow-up
    assert h.gw.state.cases[case_id].status == "NOT_ELIGIBLE"  # not reopened by mail
    assert len(h.outbox()) == 1


def test_stalled_nudge_returns_to_waiting(h):
    case_id = h.new_case(body="Hi, can you check our franchise tax?", sender="hello@team12.ai")
    h.ok("extract_facts", case_id=case_id, quotes={}, incorporated_delaware=None,
         foreign_owner_25=None)
    h.approve_pending(h.do("ask_client", case_id=case_id))
    h.ok("ask_client", case_id=case_id)
    h.clock.advance(hours=7)
    h.ok("stall_sweep")
    h.ok("ask_client", case_id=case_id)  # the nudge
    assert h.gw.state.cases[case_id].status == "INTAKE_PENDING"
    assert h.ok("ask_client", case_id=case_id)["already_asked"] is True  # no second nudge


# ---------------------------------------------------------------- idempotency


def test_voice_double_click_opens_one_case(h):
    args = dict(text="hi", speaker="customer", reply_email="j@yc.com", request_id="req-1")
    first = h.ok("voice", **args)
    again = h.ok("voice", **args)
    assert again["case_id"] == first["case_id"] and again["duplicate"] is True


def test_extract_facts_retry_is_idempotent(h):
    case_id = h.new_case()
    args = dict(case_id=case_id, quotes=HAPPY_QUOTES, incorporated_delaware=True,
                foreign_owner_25=False)
    h.ok("extract_facts", **args)
    assert h.ok("extract_facts", **args)["duplicate"] is True
    assert len(h.events("facts.extracted")) == 1


def test_unblocked_case_runs_to_the_end(h):
    case_id = h.to_qa_passed()
    from app.gateway.ports import Unavailable

    h.ports.mail.faults.add(Unavailable(), Unavailable(), Unavailable())
    h.approve_pending(h.do("send_memo", case_id=case_id))
    assert h.do("send_memo", case_id=case_id).status == "error"
    h.ok("unblock", case_id=case_id, to="READY")
    h.ok("compute_tax", case_id=case_id)
    h.ok("qa_check", case_id=case_id)
    h.ok("prepare_documents", case_id=case_id)  # v1.15
    h.ok("send_memo", case_id=case_id)
    assert h.gw.state.cases[case_id].status == "SENT"


def test_hr_record_after_the_ceo_decided_is_refused(h):
    h.ok("inject_email", **{"from": "a@x.io", "subject": "gig", "body": "EA", "inbox": "hiring"})
    h.ok("candidate_decision", candidate_id="cand_1", decision="decline")
    r = h.do("hr_record", stage="shortlisted", candidate_id="cand_1", note="")
    assert r.error["code"] == "wrong_status"


# ---------------------------------------------------------------- masking


@pytest.mark.parametrize("text", [
    "We are a Delaware C Corp with 10M shares",
    "We're a Delaware C Corporation",
    "Our Delaware Corporation has 10M shares",
])
def test_entity_type_phrases_survive_masking(text):
    out = mask_text(text, masked_name="Team #7", company=None)
    assert "Delaware" in out and "Team #7" not in out


def test_name_before_a_delaware_corporation_is_masked():
    out = mask_text("Zebra Robotics, a Delaware corporation, has 10M shares",
                    masked_name="Team #7", company=None)
    assert "Zebra" not in out and "a Delaware corporation" in out


# ---------------------------------------------------------------- crew


def test_crew_coalesces_wakes_and_serves_fast_path_first(h):
    crew = LocalCrew(h.gw, pace=0)  # not started: inspect the queue
    for message in ("New case case_1", "New case case_1", "New case case_2", "FAST PATH case_3"):
        crew.enqueue(message)
    order = []
    while not crew._queue.empty():
        order.append(crew._queue.get()[2])
    assert order == ["FAST PATH case_3", "New case case_1", "New case case_2"]


# ---------------------------------------------------------------- HTTP


def test_forged_forwarded_for_does_not_beat_the_limit(make_harness):
    h = make_harness()
    with TestClient(create_app(h.gw, start_background=False)) as client:
        # from loopback (the tunnel) X-Forwarded-For is never trusted: one key, 20/min
        codes = [client.post("/api/voice/session",
                             headers={"X-Forwarded-For": f"1.2.3.{i}"}).status_code
                 for i in range(25)]
        assert codes.count(429) == 5
    with TestClient(create_app(make_harness().gw, start_background=False)) as client:
        # CF-Connecting-IP is trusted from the tunnel (Cloudflare overwrites it), but a
        # stream of "different clients" still stops at the global cap
        codes = [client.post("/api/voice/session",
                             headers={"CF-Connecting-IP": f"5.6.7.{i % 250}"}).status_code
                 for i in range(125)]
        assert 429 in codes


def test_rate_limiter_prunes_and_has_a_global_cap():
    from app.api import RateLimiter
    from app.errors import APIError

    limiter = RateLimiter(limit=5, global_limit=10)
    for i in range(10):
        limiter.check(f"ip{i}")
    with pytest.raises(APIError):
        limiter.check("ip-new")  # the global cap holds against rotating keys


def test_stream_resume_uses_last_event_id(make_harness):
    import asyncio

    from app.api import sse_events

    h = make_harness()
    h.new_case()
    h.new_case(sender="b@two.dev")

    async def first_id(start):
        async def never():
            return False
        gen = sse_events(h.gw, start, is_disconnected=never, heartbeat=0.1)
        frame = await gen.__anext__()
        await gen.aclose()
        return frame

    assert asyncio.run(first_id(2)).startswith("id: 3\n")


def test_brief_audio_is_cached_per_brief(make_harness):
    h = make_harness()

    class CountingVoice:
        provider = "openai"
        calls = 0

        def create_session(self):  # pragma: no cover - unused
            raise NotImplementedError

        def speak(self, text):
            CountingVoice.calls += 1
            return b"ID3"

    h.ports.voice = CountingVoice()
    brief = h.ok("ceo_brief")["brief_event_id"]
    with TestClient(create_app(h.gw, start_background=False)) as client:
        for _ in range(3):
            r = client.get("/api/brief/audio", params={"event_id": brief})
            assert r.status_code == 200 and r.content == b"ID3"
    assert CountingVoice.calls == 1


def test_production_store_refuses_open_auth():
    from app.gateway.registry import AdapterNotReady, build_gateway
    from app.gateway.settings import Settings

    with pytest.raises(AdapterNotReady):
        build_gateway(Settings(store="postgres", database_url="postgresql://x/y"))


# ---------------------------------------------------------------- OpenAI voice adapter


def _voice(handler):
    from app.gateway.adapters.openai_voice import OpenAIVoice

    return OpenAIVoice("sk-test", transport=httpx.MockTransport(handler))


def test_openai_session_parses_the_client_secret():
    def ok(request):
        assert request.headers["authorization"] == "Bearer sk-test"
        return httpx.Response(200, json={"model": "gpt-realtime",
                                         "client_secret": {"value": "ek_1", "expires_at": 1800000000}})

    session = _voice(ok).create_session()
    assert session.client_secret == "ek_1" and session.expires_at.endswith("Z")


@pytest.mark.parametrize("status, headers, error", [
    (429, {"retry-after": "Fri, 07 Nov 2026 21:00:00 GMT"}, "RateLimited"),  # HTTP-date
    (401, {}, "AuthExpired"),
    (503, {}, "Unavailable"),
    (400, {}, "Rejected"),
])
def test_openai_errors_map_to_port_errors(status, headers, error):
    voice = _voice(lambda r: httpx.Response(status, headers=headers, text="nope"))
    with pytest.raises(Exception) as info:
        voice.create_session()
    assert type(info.value).__name__ == error


def test_openai_without_a_secret_is_rejected():
    from app.gateway.ports import Rejected

    with pytest.raises(Rejected):
        _voice(lambda r: httpx.Response(200, json={"model": "m"})).create_session()
