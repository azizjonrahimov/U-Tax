"""Contract v1.6 / v1.6.1 (task 6c MEDIUM): agent copy of the mail, tax-word stop list,
Client replied, greeting_name, hr.decision_mailed, ingress rules, admin-only candidates."""

import json

import pytest

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


# ---------------------------------------------------------------- 6c review fixes


def _reply_changed(h, case_id, issued="9,000,000"):
    h.new_case(body=f"Correction: we have {issued} issued.")
    return h.ok("extract_facts", case_id=case_id,
                quotes={**HAPPY_QUOTES, "issued_shares": issued},
                incorporated_delaware=True, foreign_owner_25=False)


def test_a_retry_after_a_changed_fact_still_sends_memo_v2(h):
    # review #1: a stale resume_status made the retry jump READY -> SENT, skipping memo v2
    case_id = h.to_sent()
    _reply_changed(h, case_id)
    again = h.ok("extract_facts", case_id=case_id,
                 quotes={**HAPPY_QUOTES, "issued_shares": "9,000,000"},
                 incorporated_delaware=True, foreign_owner_25=False)
    assert "unchanged" not in again and h.gw.state.cases[case_id].status == "READY"
    moves = [(e.payload["from"], e.payload["to"]) for e in h.events("case.status")]
    assert ("READY", "SENT") not in moves
    h.ok("compute_tax", case_id=case_id)
    h.ok("qa_check", case_id=case_id)
    h.ok("send_memo", case_id=case_id)
    assert len(h.events("memo.sent")) == 2


def test_only_intake_and_qa_read_names(h):
    # review #2: mkt/hr/cos get the masked copy, and can't name a client in a post or DM
    case_id = h.new_case(body=ZEBRA, sender="jane.doe@zebrarobotics.ai")
    for actor, named in (("intake", True), ("qa", True), ("mkt", False), ("cos", False)):
        msg = h.gw.perform("get_case", {"case_id": case_id}, actor=actor).data["messages"][0]
        assert ("Zebra Robotics" in msg) is named, actor
    assert h.gw.perform("get_case", {"case_id": case_id, "_actor": "intake"},
                        actor="mkt").data["messages"][0] == h.gw.state.cases[case_id].texts[0]
    post = h.gw.perform("post_social", {"channel": "x", "text": "Thanks Jane, great call!"},
                        actor="mkt")
    assert post.error["code"] == "content_guard"
    dm = h.gw.perform("draft_dm", {"recipient_label": "founder",
                                   "text": "We just helped zebrarobotics save $10k"}, actor="mkt")
    assert dm.error["code"] == "content_guard"


def test_real_mail_client_quoting_wakes_nobody(h):
    # review #3: long or wrapped attributions, Outlook headers and signature phones
    wakes = []
    h.gw.on_wake(wakes.append)
    case_id = h.to_sent()
    before = list(wakes)
    replies = [
        "Thanks a lot!\n\nOn Sat, Nov 7, 2026 at 9:14 PM U-Tax Delaware Franchise Tax Desk "
        "<checkup@u-tax.test> wrote:\n10,000,000 authorized shares",
        "Thanks!\n\nOn Sat, Nov 7, 2026 at 9:14 PM U-Tax <checkup@u-tax.test>\nwrote:\n"
        "10,000,000 authorized",
        "Perfect, thank you.\n\nFrom: U-Tax <checkup@u-tax.test>\nSent: Saturday, November 7, "
        "2026 9:14 PM\nTo: Jane\nSubject: Your estimate\n\n10,000,000 authorized shares",
        "Thanks!\nJane\n+1 (415) 555-0100",
    ]
    for body in replies:
        h.new_case(body=body)
        assert h.gw.state.cases[case_id].status == "SENT", body
    assert wakes == before and h.gw.state.cases[case_id].client_reply_log == []


def test_a_correction_above_a_quoted_original_supersedes_it(h):
    # review #4: the quoted original made the correction look like a conflict
    case_id = h.to_sent()
    h.new_case(body="Small correction: 20,000,000 authorized shares.\n\n"
                    "On Sat, Nov 7, 2026 Jane <founder@team7.dev> wrote:\n"
                    "> We have 10,000,000 authorized shares, 8,000,000 issued")
    out = h.ok("extract_facts", case_id=case_id,
               quotes={**HAPPY_QUOTES, "authorized_shares": "20,000,000"},
               incorporated_delaware=True, foreign_owner_25=False)
    assert out["facts"]["authorized_shares"] == 20_000_000


def test_the_subject_never_overrides_a_later_reply(h):
    # review #5: the first subject was read as the latest message
    case_id = h.to_sent(h.new_case(subject="tax check: 10,000,000 authorized shares"))
    h.new_case(body="Correction: 20,000,000 authorized shares.")
    out = h.ok("extract_facts", case_id=case_id,
               quotes={**HAPPY_QUOTES, "authorized_shares": "20,000,000"},
               incorporated_delaware=True, foreign_owner_25=False)
    assert out["facts"]["authorized_shares"] == 20_000_000


def test_a_booked_case_blocks_at_the_limit_and_tells_the_ceo_once(make_harness):
    # review #6: CALL_BOOKED had no edge to BLOCKED, so the CEO got a mail per reply
    h = make_harness(ceo_email="ceo@u-tax.dev")
    case_id = h.to_sent()
    slot = h.ok("get_case", case_id=case_id)["offered_slots"][0]
    h.approve_pending(h.do("book_call", case_id=case_id, start=slot))
    h.ok("book_call", case_id=case_id, start=slot)
    assert h.gw.state.cases[case_id].status == "CALL_BOOKED"
    for i in range(3):
        h.new_case(body=f"Update {i}: 10,000,000 authorized shares")
        h.ok("extract_facts", case_id=case_id, quotes=HAPPY_QUOTES,
             incorporated_delaware=True, foreign_owner_25=False)
        assert h.gw.state.cases[case_id].status == "CALL_BOOKED"
    for i in range(3):
        h.new_case(body=f"Again {i}: 10,000,000 authorized shares")
    assert h.gw.state.cases[case_id].status == "BLOCKED"
    blocked = [e for e in h.events("ceo.notified") if e.payload.get("reason") == "blocked"]
    assert len(blocked) == 1


def test_html_is_linear_and_leaves_no_markup():
    # review #7/#8
    import time

    from app.gateway.masking import html_to_text

    start = time.perf_counter()
    html_to_text("<p>x</p>" + "<script " * 8000)
    assert time.perf_counter() - start < 1.0
    out = html_to_text("<b>Hi</b> &lt;img src=x onerror=alert(1)&gt; <img src=x onerror=y>"
                       "<b>a</b><script>bad()</script>10 shares")
    assert "<" not in out and ">" not in out and "bad()" not in out and "10 shares" in out


def test_html_only_eml_is_read(h):
    eml = (b"From: Jane <jane@team9.dev>\r\nSubject: tax\r\nMessage-ID: <h1@team9.dev>\r\n"
           b"MIME-Version: 1.0\r\nContent-Type: text/html; charset=utf-8\r\n\r\n"
           b"<p>We're a Delaware C-corp with 10,000,000 authorized shares.</p>")
    mail = h.ports.mail.parse_inbound(eml)
    assert mail.body == "We're a Delaware C-corp with 10,000,000 authorized shares."


def test_auto_reply_subjects_need_a_whole_word_and_aliases_are_own(make_harness):
    # review #9/#12
    h = make_harness(inbox_clients="checkup@u-tax.dev")
    assert "dropped" not in h.ok("inject_email", **{
        "from": "a@vacationly.io", "subject": "Vacationly Inc - franchise tax question",
        "body": "We're a Delaware C-corp."})
    assert h.ok("inject_email", **{"from": "checkup+loop@u-tax.dev", "subject": "x",
                                   "body": "y"})["dropped"] is True


def test_the_domain_label_hides_the_spaced_company_name():
    # review #12: "zebrarobotics" left "Zebra Robotics" visible on the public copy
    out = mask_text("zebra robotics and Zebra-Robotics and ZebraRobotics owe tax",
                    masked_name="Team #3", company="zebrarobotics")
    assert "ebra" not in out and out.count("Team #3") == 3


# ---------------------------------------------------------------- 6c LOW


def test_the_approved_slot_is_the_one_booked(h):
    # the first approval earns AUTO; it must not let the retry move the call elsewhere
    case_id = h.to_sent()
    a, b = h.ok("get_case", case_id=case_id)["offered_slots"][:2]
    h.approve_pending(h.do("book_call", case_id=case_id, start=a))
    moved = h.do("book_call", case_id=case_id, start=b)
    assert moved.error["code"] == "slot_not_approved" and moved.data["approved_start"] == a
    assert h.ok("book_call", case_id=case_id, start=a)["start"] == a


def test_subjects_and_notes_are_cut_at_a_word():
    from app.gateway.perform import clip

    text = "Approve? First calendar invite: Team #12, 2026-11-09T17:00:00Z and a long tail"
    out = clip(text, 40)
    assert len(out) <= 40 and out.endswith("…") and text.startswith(out[:-1])
    assert out[-2] != " " and text[len(out) - 1] == " "  # cut on a boundary
    assert clip("short", 40) == "short"


def test_three_newlines_collapse(h):
    case_id = h.new_case(body="We're a Delaware C-corp.\n\n\n\n\n10,000,000 authorized shares  \n")
    body = h.events("email.received")[0].payload["body"]
    assert "\n\n\n" not in body and "  \n" not in body
    assert "\n\n\n" not in h.ok("get_case", case_id=case_id)["messages"][0]
    h.to_sent()
    assert "\n\n\n" not in h.events("memo.sent")[0].payload["body"]


def test_two_approved_slots_never_block_each_other(h):
    # review: approvals for A and B (made while paused) refused both bookings.
    # v1.7.7: B's request supersedes A's, so only B can be allowed now.
    case_id = h.to_sent()
    a, b = h.ok("get_case", case_id=case_id)["offered_slots"][:2]
    first, second = h.do("book_call", case_id=case_id, start=a), \
        h.do("book_call", case_id=case_id, start=b)
    h.ok("set_trust", action_type="calendar_invite", state="PAUSED")
    stale = h.do("approve", approval_id=first.approval_id, decision="allow")
    assert stale.error["code"] == "superseded"
    h.ok("approve", approval_id=second.approval_id, decision="allow")
    h.ok("set_trust", action_type="calendar_invite", state="AUTO")
    assert h.ok("book_call", case_id=case_id, start=b)["start"] == b


def test_tidy_and_clip_edges():
    import time

    from app.gateway.perform import clip, tidy_lines

    start = time.perf_counter()
    tidy_lines("Delaware C-corp " + " \t" * 25_000 + "x")
    assert time.perf_counter() - start < 0.5  # was 7 s under the gateway lock
    assert tidy_lines("a  \n\n\n\nb\t\n") == "a\n\nb\n"
    assert clip("abcdef", 0) == "" and len(clip("abcdef", 1)) <= 1


# ---------------------------------------------------------------- v1.6.4


def test_customer_voice_command_carries_the_masked_name(h):
    data = h.ok("voice", text="We're a Delaware C-corp", speaker="customer",
                reply_email="j@team9.dev")
    payload = h.events("voice.command")[0].payload
    assert payload["masked_name"] == h.gw.state.cases[data["case_id"]].masked_name
    assert payload["masked_name"].startswith("Team #")
    h.ok("voice", text="stop posting", speaker="ceo")
    assert "masked_name" not in h.events("voice.command")[-1].payload  # customer cases only


@pytest.mark.parametrize("url,ok", [
    (None, True), ("", True),
    ("https://cdn.u-tax.test/card.png", True),
    ("https://CDN.u-tax.test/card.png", True),
    ("http://cdn.u-tax.test/card.png", False),
    ("https://evil.example/card.png", False),
    ("https://cdn.u-tax.test.evil.example/x.png", False),
    ("https://user:pw@cdn.u-tax.test/x.png", False),
    ("javascript:alert(1)", False),
])
def test_post_images_only_from_our_asset_hosts(make_harness, url, ok):
    h = make_harness(public_asset_hosts=frozenset({"cdn.u-tax.test"}))
    args = {"channel": "x", "text": "We help founders check Delaware franchise tax."}
    if url is not None:
        args["image_url"] = url
    r = h.gw.perform("post_social", args, actor="mkt")
    assert (r.error is None or r.error["code"] != "bad_input") is ok, r.error
