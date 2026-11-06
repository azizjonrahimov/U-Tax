"""Contract v1.3: actor permissions, wake-ups (§6b), CEO notices, slots, HR ingress (§6c)."""

import json

import pytest

from app.gateway.perform import TOOL_ACTORS

from .conftest import HAPPY_QUOTES

APPLICANT = ("Hi! I'm an EA with 6 years of startup tax work. Reach me at ann@example.com "
             "or (415) 555-0100. My SSN is 123-45-6789 if you need it.")


# ---------------------------------------------------------------- actor permissions


@pytest.mark.parametrize("tool, actor, allowed", [
    ("extract_facts", "intake", True),
    ("extract_facts", "tax", False),
    ("send_memo", "acct", True),
    ("send_memo", "intake", False),
    ("get_case", "hr", True),  # get_case: any agent
    ("list_open_cases", "mkt", False),
    ("approve", "cos", False),  # control actions are never open to an agent
    ("book_spend", "acct", False),
    ("inject_email", "intake", False),
])
def test_tool_permissions_follow_the_contract_column(h, tool, actor, allowed):
    result = h.gw.perform(tool, {"case_id": "case_1"}, actor=actor)
    if allowed:
        assert result.error is None or result.error["code"] != "tool_not_allowed"
    else:
        assert result.status == "refused" and result.error["code"] == "tool_not_allowed"


def test_every_tool_has_an_owner_and_book_cost_is_gone():
    assert "book_cost" not in TOOL_ACTORS
    assert set(TOOL_ACTORS) == {
        "list_open_cases", "get_case", "extract_facts", "ask_client", "mark_not_eligible",
        "law_search", "compute_tax", "qa_check", "send_memo", "book_call", "phone_check",
        "post_social", "draft_dm", "hr_record", "firm_brief", "get_candidate", "flag_case"}


# ---------------------------------------------------------------- wake-ups (§6b)


def test_wake_messages_are_exact(h):
    wakes = []
    h.gw.on_wake(wakes.append)
    case_id = h.new_case()
    assert wakes == [f"New case {case_id}"]
    h.to_qa_passed(case_id)
    pending = h.do("send_memo", case_id=case_id)
    h.ok("approve", approval_id=pending.approval_id, decision="allow")
    assert wakes[-1] == f"Approval {pending.approval_id} allowed for {case_id}"
    post = h.do("post_social", channel="x", text="Free Delaware checkup.")
    h.ok("approve", approval_id=post.approval_id, decision="deny")
    assert wakes[-1] == f"Approval {post.approval_id} denied for firm"


def test_duplicate_mail_does_not_wake_again(h):
    wakes = []
    h.gw.on_wake(wakes.append)
    args = {"from": "a@team3.dev", "subject": "hi", "body": "x", "message_id": "m-1"}
    h.ok("inject_email", **args)
    h.ok("inject_email", **args)
    assert wakes == ["New case case_1"]


def test_a_failing_waker_never_breaks_the_action(h):
    def boom(message):
        raise RuntimeError("naive down")

    h.gw.on_wake(boom)
    assert h.new_case() == "case_1"


# ---------------------------------------------------------------- CEO notices (§5, D24)


def test_ceo_is_emailed_on_approval_and_block(make_harness):
    h = make_harness(ceo_email="ceo@u-tax.dev")
    case_id = h.to_qa_passed()
    h.do("send_memo", case_id=case_id)
    notice = h.events("ceo.notified")[0]
    assert notice.payload["reason"] == "approval" and notice.payload["channel"] == "email"
    sent = [json.loads(p.read_text(encoding="utf-8")) for p in h.outbox()]
    assert sent[0]["to"] == "ceo@u-tax.dev"
    blocked = h.new_case(body="Hi, can you check our franchise tax?", sender="b@team9.ai")
    h.gw._block(h.gw.state.cases[blocked], "test block")
    assert h.events("ceo.notified")[-1].payload["reason"] == "blocked"


def test_no_ceo_email_means_no_notice(h):
    case_id = h.to_qa_passed()
    h.do("send_memo", case_id=case_id)
    assert h.events("ceo.notified") == []


def test_ceo_login_brief(h):
    case_id = h.to_qa_passed()
    h.do("send_memo", case_id=case_id)
    data = h.ok("ceo_brief", since_id=3)
    assert data["pending_approvals"][0]["action_type"] == "email"
    assert data["text"].startswith("Welcome back. Since you were last here")
    assert "1 approval waiting on you." in data["text"]
    assert data["last_event_id"] == h.gw.state.last_event_id
    assert h.events("ceo.notified")[-1].payload["channel"] == "login"
    assert h.events("brief.generated")[-1].id == data["brief_event_id"]


def test_ceo_digest_waits_for_the_interval(make_harness):
    h = make_harness(ceo_email="ceo@u-tax.dev", ceo_digest_hours=3)
    # decided: the first digest waits a full period from boot (no mail per demo restart)
    assert h.ok("ceo_digest")["sent"] is False
    h.clock.advance(hours=3, minutes=1)
    assert h.ok("ceo_digest")["sent"] is True
    assert h.ok("ceo_digest")["sent"] is False
    h.clock.advance(hours=3, minutes=1)
    assert h.ok("ceo_digest")["sent"] is True


# ---------------------------------------------------------------- slots


def test_offered_slots_and_booking_limits(h):
    case_id = h.to_sent()
    slots = h.ok("get_case", case_id=case_id)["offered_slots"]
    assert len(slots) == 3 and slots == sorted(slots)
    refused = h.do("book_call", case_id=case_id, start="2026-11-09T03:00:00Z")
    assert refused.error["code"] == "slot_not_offered"
    assert refused.data["offered_slots"] == slots
    pending = h.do("book_call", case_id=case_id, start=slots[0])
    h.ok("approve", approval_id=pending.approval_id, decision="allow")
    h.ok("book_call", case_id=case_id, start=slots[0])
    other = h.to_sent(h.new_case(sender="two@b.io"))
    assert slots[0] not in h.ok("get_case", case_id=other)["offered_slots"]  # taken


# ---------------------------------------------------------------- HR ingress (§6c)


def test_applicant_mail_is_masked_and_gets_the_questionnaire(h):
    wakes = []
    h.gw.on_wake(wakes.append)
    first = h.ok("inject_email", **{"from": "ann@example.com", "subject": "gig",
                                    "body": APPLICANT, "inbox": "hiring"})
    assert first["candidate_id"] == "cand_1" and first["name_masked"] == "Candidate A"
    assert first["questionnaire"] == "needs_approval"  # email trust still ASK
    applicant = h.events("hr.applicant")[0]
    public = json.dumps(applicant.public())
    for leak in ("ann@example.com", "555-0100", "123-45-6789"):
        assert leak not in public
    assert applicant.payload["note"] == "EA · 6 yrs"  # credentials only, no free text
    assert "EA with 6 years" in applicant.private["text"]
    assert wakes == ["New applicant cand_1"]
    # the CEO approves: the gateway itself sends the questionnaire
    approval = h.events("approval.requested")[0].payload["approval_id"]
    data = h.ok("approve", approval_id=approval, decision="allow")
    assert data["questionnaire"] == "sent"
    sent = json.loads(h.outbox()[0].read_text(encoding="utf-8"))
    assert sent["to"] == "ann@example.com"
    assert "This screen is run by an AI agent. A human makes the final hiring decision." in sent["body"]
    # answers on the same thread join the same candidate, no second questionnaire
    again = h.ok("inject_email", **{"from": "ann@example.com", "subject": "Re: gig",
                                    "body": "1. EA, California. 4. $150", "inbox": "hiring"})
    assert again["candidate_id"] == "cand_1" and again["new"] is False
    assert len(h.outbox()) == 1
    assert h.gw.state.hiring["applicants"] == 1
    assert "cases" not in h.gw.state.public() or h.gw.state.public()["cases"] == []


def test_mark_not_eligible_reply_is_chosen_by_enum(h):
    case_id = h.new_case(body="We're an LLC", sender="x@wyo.co")
    h.ok("extract_facts", case_id=case_id, quotes=HAPPY_QUOTES | {"authorized_shares": ""},
         incorporated_delaware=None, foreign_owner_25=None)
    result = h.do("mark_not_eligible", case_id=case_id, reason="outside_delaware")
    h.approve_pending(result)
    h.ok("mark_not_eligible", case_id=case_id, reason="outside_delaware")
    body = json.loads(h.outbox()[0].read_text(encoding="utf-8"))["body"]
    assert "incorporated outside Delaware" in body
