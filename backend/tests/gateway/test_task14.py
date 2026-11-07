"""Task 14 (contract v1.7.7, from the frontend's /ceo review): a stale approval never sends
a figure the CEO didn't see, approvals allowed while PAUSED are carried out on resume,
/api/trust takes ASK and RESUME, and a public run never boots with open auth. All mocked."""

from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.gateway.registry import AdapterNotReady, build_gateway, open_auth_problem
from app.gateway.settings import Settings
from app.main import create_app

from .conftest import HAPPY_QUOTES


def corrected_memo(h):
    """A memo waits on the CEO, the client corrects a number, a new memo waits too."""
    case_id = h.to_qa_passed()
    first = h.do("send_memo", case_id=case_id)
    h.ok("inject_email", **{"from": "founder@team7.dev", "subject": "re",
                            "body": "Correction: about $900k in assets."})
    h.ok("extract_facts", case_id=case_id, quotes=dict(HAPPY_QUOTES, gross_assets="$900k"),
         incorporated_delaware=True, foreign_owner_25=False)
    h.ok("compute_tax", case_id=case_id)
    h.ok("qa_check", case_id=case_id)
    second = h.do("send_memo", case_id=case_id)
    assert first.status == second.status == "needs_approval"
    return case_id, first.approval_id, second.approval_id


# ---------------------------------------------------------------- a. superseded approvals


def two_slot_requests(h):
    case_id = h.to_sent()
    a, b = h.ok("get_case", case_id=case_id)["offered_slots"][:2]
    first = h.do("book_call", case_id=case_id, start=a)
    second = h.do("book_call", case_id=case_id, start=b)
    return case_id, first.approval_id, second.approval_id


def test_a_newer_request_supersedes_the_old_one(h):
    case_id, old, new = two_slot_requests(h)
    stale = h.do("approve", approval_id=old, decision="allow")
    assert stale.status == "refused" and stale.error["code"] == "superseded"
    assert stale.data == {"approval_id": old, "superseded_by": new}
    assert h.gw.state.trust["calendar_invite"] == "ASK"  # no trust flip
    assert not h.events("call.booked")
    assert new in [p["approval_id"] for p in h.ok("ceo_brief")["pending_approvals"]]
    assert old not in [p["approval_id"] for p in h.ok("ceo_brief")["pending_approvals"]]


def test_a_corrected_memo_never_sends_on_the_old_approval(h):
    case_id, old, new = corrected_memo(h)
    stale = h.do("approve", approval_id=old, decision="allow")
    assert stale.error["code"] == "superseded"
    assert h.gw.state.trust["email"] == "ASK" and h.gw.state.touches["training"] == 0
    assert not h.events("memo.sent")
    assert [p["approval_id"] for p in h.ok("ceo_brief")["pending_approvals"]] == [new]
    h.ok("approve", approval_id=new, decision="allow")
    assert h.ok("send_memo", case_id=case_id)["message_id"]
    asked = [e.payload["summary"] for e in h.events("approval.requested")]
    assert asked[0] != asked[1]  # the figure changed: the CEO approved the new one


def test_a_client_reply_makes_a_pending_approval_stale(h):
    case_id = h.to_qa_passed()
    first = h.do("send_memo", case_id=case_id)
    h.ok("inject_email", **{"from": "founder@team7.dev", "subject": "re",
                            "body": "Wait, we have more assets than that."})
    stale = h.do("approve", approval_id=first.approval_id, decision="deny")
    assert stale.error["code"] == "superseded"
    assert stale.data["superseded_by"] == "client_reply"
    assert h.gw.state.trust["email"] == "ASK" and h.gw.state.touches["operating"] == 0


def test_a_other_cases_and_types_are_not_superseded(h):
    a = h.to_qa_passed()
    b = h.to_qa_passed(h.new_case(sender="b@team2.dev"))
    first, second = h.do("send_memo", case_id=a), h.do("send_memo", case_id=b)
    assert {p["approval_id"] for p in h.ok("ceo_brief")["pending_approvals"]} == \
        {first.approval_id, second.approval_id}


def test_a_superseded_survives_a_restart_and_answers_409(h):
    _, old, new = two_slot_requests(h)
    h.restart()
    assert h.gw.state.approvals[old].superseded_by == new
    with TestClient(create_app(h.gw, start_background=False)) as client:
        r = client.post("/api/approve", json={"approval_id": old, "decision": "allow"})
        assert r.status_code == 409 and r.json()["error"]["code"] == "superseded"
        assert client.post("/api/approve", json={"approval_id": new,
                                                 "decision": "allow"}).status_code == 200
    assert h.gw.state.trust["calendar_invite"] == "AUTO"


# ---------------------------------------------------------------- b. carried out on resume


def test_b_case_approval_allowed_while_paused_runs_on_resume(h):
    wakes = []
    h.gw.on_wake(wakes.append)
    case_id = h.to_qa_passed()
    pending = h.do("send_memo", case_id=case_id)
    h.ok("set_trust", action_type="email", state="PAUSED")
    h.ok("approve", approval_id=pending.approval_id, decision="allow")
    assert h.do("send_memo", case_id=case_id).error["code"] == "paused"
    wakes.clear()
    assert h.ok("set_trust", action_type="email", state="RESUME")["trust"] == "ASK"
    assert f"Approval {pending.approval_id} allowed for {case_id}" in wakes
    assert h.ok("send_memo", case_id=case_id)["message_id"]  # the allowed key passes ASK
    # carried out once: a later pause and resume doesn't wake it again
    h.ok("set_trust", action_type="email", state="PAUSED")
    wakes.clear()
    h.ok("set_trust", action_type="email", state="RESUME")
    assert not any(pending.approval_id in w for w in wakes)


def test_b_gateway_owned_questionnaire_goes_out_on_resume(h):
    h.ok("inject_email", **{"from": "a@x.io", "subject": "gig", "body": "EA",
                            "inbox": "hiring"})
    approval = h.events("approval.requested")[0].payload["approval_id"]
    h.ok("set_trust", action_type="email", state="PAUSED")
    h.ok("approve", approval_id=approval, decision="allow")
    assert not h.events("hr.questionnaire_sent")
    assert h.gw.retry_questionnaires() == []  # the tick only runs it under AUTO
    h.ok("set_trust", action_type="email", state="RESUME")
    assert h.gw.state.trust["email"] == "ASK"
    assert len(h.events("hr.questionnaire_sent")) == 1


def test_b_while_paused_flag_survives_a_restart(h):
    h.ok("inject_email", **{"from": "a@x.io", "subject": "gig", "body": "EA",
                            "inbox": "hiring"})
    approval = h.events("approval.requested")[0].payload["approval_id"]
    h.ok("set_trust", action_type="email", state="PAUSED")
    h.ok("approve", approval_id=approval, decision="allow")
    h.restart()
    assert h.gw.state.approvals[approval].while_paused
    h.ok("set_trust", action_type="email", state="AUTO")
    assert len(h.events("hr.questionnaire_sent")) == 1
    h.restart()
    assert not h.gw.state.approvals[approval].while_paused


# ---------------------------------------------------------------- c. ASK and RESUME


def test_c_ask_and_resume(h):
    h.to_sent()  # email -> AUTO
    assert h.ok("set_trust", action_type="email", state="PAUSED")["trust"] == "PAUSED"
    assert h.ok("set_trust", action_type="email", state="RESUME")["trust"] == "AUTO"
    assert h.ok("set_trust", action_type="email", state="RESUME")["duplicate"] is True
    assert h.ok("set_trust", action_type="email", state="ASK")["trust"] == "ASK"
    h.ok("set_trust", action_type="email", state="PAUSED")
    assert h.ok("set_trust", action_type="email", state="RESUME")["trust"] == "ASK"
    reasons = [e.payload["reason"] for e in h.events("trust.changed")]
    assert reasons[-1] == "CEO resume"
    assert h.do("set_trust", action_type="email", state="ON").error["code"] == "bad_input"


def test_c_resume_after_a_cap_pause_returns_to_auto(make_harness):
    h = make_harness(spend_cap_usd=Decimal("1"))
    h.to_sent()
    h.ok("book_spend", provider="openrouter", model="m", cost_usd="1.00")
    h.do("send_memo", case_id=h.to_qa_passed(h.new_case(sender="b@other.io")))
    assert h.gw.state.trust["email"] == "PAUSED"
    assert h.ok("set_trust", action_type="email", state="RESUME")["trust"] == "AUTO"


def test_c_resume_without_a_recorded_pause_is_ask(h):
    h.ok("set_trust", action_type="social_post", state="PAUSED")
    assert h.ok("set_trust", action_type="social_post", state="RESUME")["trust"] == "ASK"


def test_c_api_takes_ask_and_resume(h):
    with TestClient(create_app(h.gw, start_background=False)) as client:
        for state, want in (("PAUSED", "PAUSED"), ("RESUME", "ASK"), ("AUTO", "AUTO"),
                            ("ASK", "ASK")):
            r = client.post("/api/trust", json={"action_type": "email", "state": state})
            assert r.status_code == 200 and r.json()["trust"] == want, (state, r.json())


# ---------------------------------------------------------------- d. no open auth in public


@pytest.mark.parametrize("kw", [{"public_mode": "event"},
                                {"public_talk_url": "https://u-tax.vercel.app/talk"},
                                {"public_talk_url": " HTTPS://u-tax.app/talk"},
                                {"public_mode": "event", "admin_token": "a"},
                                {"public_mode": "event", "theater_token": "t"}])
def test_d_public_runs_refuse_open_auth(tmp_path, kw):
    with pytest.raises(AdapterNotReady, match="ADMIN_TOKEN and THEATER_TOKEN"):
        build_gateway(Settings(data_dir=tmp_path, **kw))


@pytest.mark.parametrize("kw", [{}, {"public_talk_url": "http://localhost:3000/talk"},
                                {"public_mode": "event", "admin_token": "a",
                                 "theater_token": "t",
                                 "public_talk_url": "https://u-tax.app/talk"}])
def test_d_local_dev_and_full_tokens_boot(kw):
    assert open_auth_problem(Settings(**kw)) is None
