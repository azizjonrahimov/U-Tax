"""Contract v1.6 / v1.6.1 (task 6c MEDIUM): agent copy of the mail, tax-word stop list,
Client replied, greeting_name, hr.decision_mailed, ingress rules, admin-only candidates."""

import json

from fastapi.testclient import TestClient

from app.gateway.masking import mask_text
from app.main import create_app

from .conftest import HAPPY_QUOTES

ZEBRA = ("Hi! Zebra Robotics, a Delaware corporation, here. We have 10,000,000 authorized "
         "shares, 8,000,000 issued, and about $500k in assets. No foreign owners. "
         "Call me at (415) 555-0100.")


def test_agents_read_names_the_public_copy_masks_them(h):
    case_id = h.new_case(body=ZEBRA, sender="ceo@zebrarobotics.ai")
    public = h.events("email.received")[0].payload["body"]
    assert "Zebra" not in public and "555-0100" not in public
    agent = h.ok("get_case", case_id=case_id)["messages"][0]
    assert "Zebra Robotics, a Delaware corporation" in agent  # names and facts survive
    assert "555-0100" not in agent  # contact details never reach an agent
    # quotes are checked against what the agent read
    data = h.ok("extract_facts", case_id=case_id,
                quotes={**HAPPY_QUOTES, "incorporated_delaware": "a Delaware corporation"},
                incorporated_delaware=True, foreign_owner_25=False)
    assert "facts" in data and not data.get("missing")


def test_tax_words_are_never_masked():
    assert mask_text("We have 10M shares", masked_name="Team #1", company="shares") == \
        "We have 10M shares"
    out = mask_text("Delaware here", masked_name="Team #1", company=None,
                    hide_words=["delaware"])
    assert out == "Delaware here"
    assert mask_text("Acme Robotics Inc owes", masked_name="Team #1", company=None) == \
        "Team #1 Inc owes"
    assert "Yes, a Delaware corporation" in mask_text(
        "Yes, a Delaware corporation with 10M shares", masked_name="Team #1", company=None)


def test_client_replied_reextracts_and_only_a_changed_fact_makes_memo_v2(h):
    wakes = []
    h.gw.on_wake(wakes.append)
    case_id = h.to_sent()
    before = list(wakes)
    h.new_case(body="Thanks, looks great!")  # no digits: nobody wakes, nothing reopens
    assert h.gw.state.cases[case_id].status == "SENT" and wakes == before
    # numbers again, unchanged: re-read, back to SENT, no second memo
    h.new_case(body="To confirm: 10,000,000 authorized shares, 8,000,000 issued, $500k.")
    assert wakes[-1] == f"Client replied {case_id}"
    texts = h.gw.state.cases[case_id].agent_texts
    data = h.ok("extract_facts", case_id=case_id, quotes=HAPPY_QUOTES,
                incorporated_delaware=True, foreign_owner_25=False)
    assert data["unchanged"] is True and h.gw.state.cases[case_id].status == "SENT"
    assert len(h.events("memo.sent")) == 1 and len(texts) == 3
    # a changed number: memo v2
    h.new_case(body="Correction: we have 9,000,000 issued.")
    h.ok("extract_facts", case_id=case_id,
         quotes={**HAPPY_QUOTES, "issued_shares": "9,000,000"},
         incorporated_delaware=True, foreign_owner_25=False)
    h.ok("compute_tax", case_id=case_id)
    h.ok("qa_check", case_id=case_id)
    h.ok("send_memo", case_id=case_id)
    assert len(h.events("memo.sent")) == 2


def test_outgoing_mail_greets_the_company_the_event_stays_masked(h):
    case_id = h.to_sent()
    event_body = h.events("memo.sent")[0].payload["body"]
    assert event_body.startswith("Hi Team #1,")
    mailed = [json.loads(p.read_text(encoding="utf-8")) for p in h.outbox()]
    memo = [m for m in mailed if m["subject"] == "Your Delaware franchise tax estimate"][0]
    assert memo["body"].startswith("Hi Team7,")  # founder@team7.dev
    other = h.to_qa_passed(h.new_case(sender="me@gmail.com"))
    h.ok("send_memo", case_id=other)
    mailed = [json.loads(p.read_text(encoding="utf-8")) for p in h.outbox()]
    assert any(m["body"].startswith("Hi there,") for m in mailed)


def test_decision_mail_writes_hr_decision_mailed(h):
    h.ok("ceo_stop", action_type="email")
    h.gw.state.trust["email"] = "AUTO"
    h.ok("inject_email", **{"from": "ann@example.com", "subject": "g", "body": "EA",
                            "inbox": "hiring"})
    h.ok("candidate_decision", candidate_id="cand_1", decision="decline")
    mailed = h.events("hr.decision_mailed")
    assert len(mailed) == 1 and mailed[0].actor == "system"
    assert h.ok("candidate_decision", candidate_id="cand_1", decision="decline")["mail"] == "sent"
    assert len(h.events("hr.decision_mailed")) == 1  # counted once, sent once


def test_mail_from_the_firms_own_addresses_is_dropped(make_harness):
    h = make_harness(ceo_email="ceo@u-tax.dev", inbox_clients="checkup@u-tax.dev")
    for sender in ("ceo@u-tax.dev", "CHECKUP@u-tax.dev", "jobs@u-tax.test"):
        assert h.ok("inject_email", **{"from": sender, "subject": "loop",
                                       "body": "auto-reply"})["dropped"] is True
    assert h.types() == []


def test_html_bodies_become_text(h):
    case_id = h.new_case(body="<html><body><p>We're a <b>Delaware C-corp</b>.</p>"
                              "<div>10,000,000 authorized&nbsp;shares</div>"
                              "<script>alert(1)</script></body></html>")
    body = h.events("email.received")[0].payload["body"]
    assert "<" not in body and "alert" not in body
    assert "Delaware C-corp" in body and "10,000,000 authorized shares" in body
    assert "<" not in h.ok("get_case", case_id=case_id)["messages"][0]


def test_candidate_list_is_admin_only_over_http(make_harness):
    h = make_harness(admin_token="admin-x", theater_token="theater-x")
    h.ok("inject_email", **{"from": "a@x.io", "subject": "g", "body": "EA", "inbox": "hiring"})
    with TestClient(create_app(h.gw, start_background=False)) as client:
        theater = client.get("/api/state", params={"token": "theater-x"}).json()["hiring"]
        admin = client.get("/api/state", headers={"Authorization": "Bearer admin-x"}).json()
    assert "candidates" not in theater and theater["applicants"] == 1
    assert admin["hiring"]["candidates"][0]["candidate_id"] == "cand_1"


def test_talk_keeps_agent_turns_as_context_only(h):
    turns = [{"speaker": "agent", "text": "Are you incorporated in Delaware?"},
             {"speaker": "customer", "text": "No."},
             {"speaker": "agent", "text": "So 10,000,000 authorized shares?"},
             {"speaker": "customer", "text": "Yes, 10,000,000 authorized"}]
    data = h.ok("voice", turns=turns, speaker="customer", reply_email="j@yc.com")
    detail = h.ok("get_case", case_id=data["case_id"])
    assert [t["speaker"] for t in detail["conversation"]] == ["agent", "customer", "agent",
                                                              "customer"]
    assert detail["conversation"][0]["text"] == "Are you incorporated in Delaware?"
    assert "Are you incorporated" not in detail["messages"][0]  # never quotable


# ---------------------------------------------------------------- v1.6.2


def test_greeting_comes_from_the_sanitised_domain_label(h):
    case_id = h.new_case(sender="ceo@zebra-robotics.ai")
    assert h.gw._greeting(h.gw.state.cases[case_id]) == "Zebra Robotics"
    voice = h.ok("voice", text="hi", speaker="customer", reply_email="j@acme.dev")["case_id"]
    assert h.gw._greeting(h.gw.state.cases[voice]) == "there"


def test_quoted_digits_and_signatures_wake_nobody(h):
    wakes = []
    h.gw.on_wake(wakes.append)
    case_id = h.to_sent()
    before = list(wakes)
    h.new_case(body="Thanks so much!\n> We have 10,000,000 authorized shares\n"
                    "-- \nJane, CEO, +1 415 555 0100")
    assert wakes == before and h.gw.state.cases[case_id].status == "SENT"


def test_client_reply_limits_block_the_case(h):
    case_id = h.to_sent()
    for i in range(3):
        h.new_case(body=f"Update {i}: 10,000,000 authorized shares")
        h.ok("extract_facts", case_id=case_id, quotes=HAPPY_QUOTES,
             incorporated_delaware=True, foreign_owner_25=False)  # unchanged -> back to SENT
    h.new_case(body="Update 4: 10,000,000 authorized shares")
    assert h.gw.state.cases[case_id].status == "BLOCKED"
    assert "revision limit" in h.gw.state.cases[case_id].blocked_reason


def test_auto_replies_are_dropped(h):
    eml = (b"From: Bot <bot@team5.dev>\r\nSubject: Re: your estimate\r\n"
           b"Auto-Submitted: auto-replied\r\nMessage-ID: <auto-1@team5.dev>\r\n\r\nI'm away.")
    mail = h.ports.mail.parse_inbound(eml)
    assert mail.auto_reply is True
    assert h.ok("receive_email", mail=mail)["dropped"] is True
    assert h.ok("inject_email", **{"from": "a@b.dev", "subject": "Out of Office: back Monday",
                                   "body": "away"})["dropped"] is True
    assert h.types() == []


def test_quotes_are_masked_on_the_screen_but_qa_checks_the_raw_ones(h):
    case_id = h.new_case(body=ZEBRA, sender="ceo@zebrarobotics.ai")
    h.ok("extract_facts", case_id=case_id,
         quotes={**HAPPY_QUOTES, "incorporated_delaware": "Zebra Robotics, a Delaware corporation"},
         incorporated_delaware=True, foreign_owner_25=False)
    public = h.events("facts.extracted")[0].payload["quotes"]["incorporated_delaware"]
    assert "Zebra" not in public and "Delaware corporation" in public
    h.ok("compute_tax", case_id=case_id)
    assert h.ok("qa_check", case_id=case_id)["passed"] is True
