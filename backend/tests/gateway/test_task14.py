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
    h.ok("prepare_documents", case_id=case_id)  # v1.15
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


def test_a_reopen_makes_a_pending_approval_stale_and_the_crew_asks_again(h):
    wakes = []
    h.gw.on_wake(wakes.append)
    case_id = h.to_qa_passed()
    first = h.do("send_memo", case_id=case_id)
    h.ok("inject_email", **{"from": "founder@team7.dev", "subject": "re",
                            "body": "Wait, we have more assets than that."})
    assert f"FAST PATH {case_id}" in wakes  # back to intake: the crew re-reads it (D42)
    stale = h.do("approve", approval_id=first.approval_id, decision="deny")
    assert stale.error["code"] == "superseded"
    assert stale.data["superseded_by"] is None  # nothing newer to decide yet
    assert h.gw.state.trust["email"] == "ASK" and h.gw.state.touches["operating"] == 0
    h.to_qa_passed(case_id)  # same figures re-read
    again = h.do("send_memo", case_id=case_id)
    assert again.status == "needs_approval" and again.approval_id != first.approval_id
    h.ok("approve", approval_id=again.approval_id, decision="allow")
    assert h.ok("send_memo", case_id=case_id)["message_id"]


def test_a_quiet_thanks_keeps_a_pending_call_invite(h):
    # review: a "thanks!" on a SENT case wakes nobody, so it must not void the invite
    case_id = h.to_sent()
    slot = h.ok("get_case", case_id=case_id)["offered_slots"][0]
    pending = h.do("book_call", case_id=case_id, start=slot)
    h.ok("inject_email", **{"from": "founder@team7.dev", "subject": "re",
                            "body": "thanks, see you!"})
    h.ok("approve", approval_id=pending.approval_id, decision="allow")
    assert h.ok("book_call", case_id=case_id, start=slot)["start"] == slot


def test_a_superseded_by_points_at_the_newest_live_request(h):
    case_id, first, second = two_slot_requests(h)
    c = h.ok("get_case", case_id=case_id)["offered_slots"][2]
    third = h.do("book_call", case_id=case_id, start=c).approval_id
    assert h.do("approve", approval_id=first, decision="allow").data["superseded_by"] == third


def test_a_back_and_forth_mails_the_ceo_once_per_request(make_harness):
    h = make_harness(ceo_email="ceo@u-tax.dev")
    case_id, first, second = two_slot_requests(h)
    notices = len(h.events("ceo.notified"))
    assert notices >= 2  # the two slot requests did mail the CEO
    slot_a = h.ok("get_case", case_id=case_id)["offered_slots"][0]
    again = h.do("book_call", case_id=case_id, start=slot_a)  # back to slot A
    assert again.status == "needs_approval" and again.approval_id not in (first, second)
    assert len(h.events("ceo.notified")) == notices  # no third "Approve?" mail


def test_a_re_ask_after_the_newer_one_was_decided_mails_the_ceo(make_harness):
    # review 2: the throttle hid a request nobody was told about
    h = make_harness(ceo_email="ceo@u-tax.dev")
    case_id, first, second = two_slot_requests(h)
    h.ok("approve", approval_id=second, decision="deny")
    notices = len(h.events("ceo.notified"))
    slot_a = h.ok("get_case", case_id=case_id)["offered_slots"][0]
    assert h.do("book_call", case_id=case_id, start=slot_a).status == "needs_approval"
    assert len(h.events("ceo.notified")) == notices + 1


def test_a_scheduling_reply_with_digits_keeps_the_call_invite(h):
    # review 2: "3pm on the 12th?" re-reads the case but changes no fact
    case_id = h.to_sent()
    slot = h.ok("get_case", case_id=case_id)["offered_slots"][0]
    pending = h.do("book_call", case_id=case_id, start=slot)
    h.ok("inject_email", **{"from": "founder@team7.dev", "subject": "re",
                            "body": "Great, can we talk at 3pm on the 12th?"})
    assert h.gw.state.cases[case_id].status == "INTAKE_PENDING"
    back = h.ok("extract_facts", case_id=case_id, quotes=HAPPY_QUOTES,
                incorporated_delaware=True, foreign_owner_25=False)
    assert back["unchanged"] is True and h.gw.state.cases[case_id].status == "SENT"
    h.ok("approve", approval_id=pending.approval_id, decision="allow")
    assert h.ok("book_call", case_id=case_id, start=slot)["start"] == slot


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


def test_b_a_post_allowed_while_paused_wakes_the_firm_on_resume(h):
    # review: posts have no case; the resume used to drop them silently
    wakes = []
    h.gw.on_wake(wakes.append)
    post = h.do("post_social", channel="x", text="Free Delaware checkup.")
    h.ok("set_trust", action_type="social_post", state="PAUSED")
    h.ok("approve", approval_id=post.approval_id, decision="allow")
    wakes.clear()
    h.ok("set_trust", action_type="social_post", state="RESUME")
    assert f"Approval {post.approval_id} allowed for firm" in wakes
    assert h.ok("post_social", channel="x", text="Free Delaware checkup.")


def test_b_a_failed_send_on_resume_to_ask_is_retried_by_the_tick(h):
    from app.gateway.ports import PortError

    h.ok("inject_email", **{"from": "a@x.io", "subject": "gig", "body": "EA",
                            "inbox": "hiring"})
    approval = h.events("approval.requested")[0].payload["approval_id"]
    h.ok("set_trust", action_type="email", state="PAUSED")
    h.ok("approve", approval_id=approval, decision="allow")
    real = h.gw.ports.mail.send

    def down(*a, **k):
        raise PortError("mail down")

    h.gw.ports.mail.send = down
    h.ok("set_trust", action_type="email", state="RESUME")  # -> ASK, the send fails
    assert not h.events("hr.questionnaire_sent")
    h.gw.ports.mail.send = real
    assert h.gw.retry_questionnaires() == ["cand_1"]
    assert len(h.events("hr.questionnaire_sent")) == 1


def test_b_ceo_stop_out_of_pause_carries_out_too(h):
    h.ok("inject_email", **{"from": "a@x.io", "subject": "gig", "body": "EA",
                            "inbox": "hiring"})
    approval = h.events("approval.requested")[0].payload["approval_id"]
    h.ok("set_trust", action_type="email", state="PAUSED")
    h.ok("approve", approval_id=approval, decision="allow")
    assert h.ok("ceo_stop", action_type="email")["trust"] == "ASK"
    assert len(h.events("hr.questionnaire_sent")) == 1


def test_b_a_reopen_voids_an_ok_given_while_paused(h):
    case_id = h.to_sent()
    slot = h.ok("get_case", case_id=case_id)["offered_slots"][0]
    pending = h.do("book_call", case_id=case_id, start=slot)
    h.ok("set_trust", action_type="calendar_invite", state="PAUSED")
    h.ok("approve", approval_id=pending.approval_id, decision="allow")
    h.ok("unblock", case_id=case_id, to="INTAKE_PENDING", note="new numbers")
    assert h.gw.state.approvals[pending.approval_id].decision == "superseded"
    wakes = []
    h.gw.on_wake(wakes.append)
    h.ok("set_trust", action_type="calendar_invite", state="RESUME")
    assert not any(pending.approval_id in w for w in wakes)


def test_b_resume_to_ask_wakes_the_cases_the_pause_held(h):
    wakes = []
    h.gw.on_wake(wakes.append)
    case_id = h.to_qa_passed()
    h.ok("set_trust", action_type="email", state="PAUSED")
    wakes.clear()
    h.ok("set_trust", action_type="email", state="ASK")
    assert f"New case {case_id}" in wakes
    h.ok("set_trust", action_type="email", state="PAUSED")
    wakes.clear()
    h.ok("ceo_stop", action_type="email")  # review 2: a stop out of PAUSED resumes too
    assert f"New case {case_id}" in wakes


# ---------------------------------------------------------------- c. ASK and RESUME


def test_c_resume_after_a_restart_returns_to_auto(h):
    h.to_sent()  # email -> AUTO
    h.ok("set_trust", action_type="email", state="PAUSED")
    h.restart()
    assert h.ok("set_trust", action_type="email", state="RESUME")["trust"] == "AUTO"


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


def test_d_public_runs_refuse_the_theater_token_as_admin(tmp_path):
    with pytest.raises(AdapterNotReady, match="different from THEATER_TOKEN"):
        build_gateway(Settings(data_dir=tmp_path, public_mode="event", admin_token="same",
                               theater_token="same"))


@pytest.mark.parametrize("kw", [{}, {"public_talk_url": "http://localhost:3000/talk"},
                                {"admin_token": "same", "theater_token": "same"},
                                {"public_mode": "event", "admin_token": "a",
                                 "theater_token": "t",
                                 "public_talk_url": "https://u-tax.app/talk"}])
def test_d_local_dev_and_full_tokens_boot(kw):
    assert open_auth_problem(Settings(**kw)) is None
