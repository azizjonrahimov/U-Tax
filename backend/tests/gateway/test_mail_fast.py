"""D42 M2: every email case runs the fast path: a new email, a reply after an ask and a reply
with numbers after the memo wake "FAST PATH case_N"; the case is in flight for 10 minutes;
case.priority (a /talk case) never changes. Also the memo's subject (owner test R1)."""

from __future__ import annotations

import json

from app.gateway.templates import MEMO_SUBJECT

from .conftest import HAPPY_BODY

PARTIAL = "We're a Delaware C-corp. We have 10,000,000 authorized shares."


def woken(h) -> list[str]:
    wakes: list[str] = []
    h.gw.on_wake(wakes.append)
    return wakes


def mail(h, body, message_id, subject="free franchise tax checkup?"):
    return h.ok("inject_email", **{"from": "founder@team7.dev", "subject": subject,
                                   "body": body, "message_id": message_id})


def ask(h, case_id):
    out = h.do("ask_client", case_id=case_id, missing=["issued_shares", "gross_assets"])
    if out.status == "needs_approval":
        h.approve_pending(out)
        h.ok("ask_client", case_id=case_id, missing=["issued_shares", "gross_assets"])


def row(h, case_id):
    cases = h.gw.perform("list_open_cases", {}, actor="cos").data["cases"]
    return next(c for c in cases if c["case_id"] == case_id)


def test_a_new_email_case_wakes_the_fast_path_and_is_in_flight(h):
    wakes = woken(h)
    case_id = mail(h, PARTIAL, "<1@mail.gmail.com>")["case_id"]
    assert wakes == [f"FAST PATH {case_id}"]
    assert h.gw.state.cases[case_id].priority is False  # never a /talk case
    assert row(h, case_id)["in_flight"] is True and row(h, case_id)["priority"] is False
    h.clock.advance(minutes=11)
    assert row(h, case_id)["in_flight"] is False  # the board may pick it up again


def test_a_reply_after_an_ask_wakes_the_fast_path(h):
    case_id = mail(h, PARTIAL, "<1@mail.gmail.com>")["case_id"]
    ask(h, case_id)
    wakes = woken(h)
    mail(h, HAPPY_BODY, "<2@mail.gmail.com>", subject="Re: free franchise tax checkup?")
    assert wakes == [f"FAST PATH {case_id}"]
    assert h.gw.wake_trigger(f"FAST PATH {case_id}") == \
        h.gw.state.cases[case_id].last_inbound_id  # once per email (the 22b bookkeeping)


def test_a_reply_an_hour_after_the_ask_is_in_flight_again(h):
    # agents' review: a stale reply must be in flight, or a routing pass doubles the card
    case_id = mail(h, PARTIAL, "<1@mail.gmail.com>")["case_id"]
    ask(h, case_id)
    assert row(h, case_id)["in_flight"] is False  # the ask ended the chain
    h.clock.advance(hours=1)
    wakes = woken(h)
    mail(h, HAPPY_BODY, "<2@mail.gmail.com>", subject="Re: free franchise tax checkup?")
    assert wakes == [f"FAST PATH {case_id}"] and row(h, case_id)["in_flight"] is True


def test_the_chain_ending_clears_in_flight(h):
    case_id = mail(h, HAPPY_BODY, "<1@mail.gmail.com>")["case_id"]
    assert row(h, case_id)["in_flight"] is True
    h.to_sent(case_id)
    assert h.gw.state.cases[case_id].fast_until is None  # SENT


def test_after_the_memo_thanks_wakes_nothing_and_new_numbers_wake_the_fast_path(h):
    case_id = mail(h, HAPPY_BODY, "<1@mail.gmail.com>")["case_id"]
    h.to_sent(case_id)
    wakes = woken(h)
    mail(h, "Thanks so much!", "<2@mail.gmail.com>", subject="Re: free franchise tax checkup?")
    assert wakes == []  # unchanged: a thanks wakes nobody
    mail(h, "Sorry, we actually issued 9,000,000 shares.", "<3@mail.gmail.com>",
         subject="Re: free franchise tax checkup?")
    assert wakes == [f"FAST PATH {case_id}"]  # was "Client replied case_N"


def test_a_talk_case_is_still_priority_and_in_flight(h):
    wakes = woken(h)
    out = h.ok("voice", text=HAPPY_BODY, speaker="customer", reply_email="jo@team9.dev")
    assert wakes == [f"FAST PATH {out['case_id']}"]
    assert row(h, out["case_id"])["priority"] is True
    assert row(h, out["case_id"])["in_flight"] is True


def test_the_memo_stays_in_the_clients_gmail_thread(h):
    case_id = mail(h, HAPPY_BODY, "<1@mail.gmail.com>", subject="Delaware tax question")
    h.to_sent(case_id["case_id"])
    memo = json.loads(h.outbox()[-1].read_text(encoding="utf-8"))
    assert memo["subject"] == "Re: Delaware tax question"
    assert memo["in_reply_to"] == "<1@mail.gmail.com>"
    [sent] = h.events("memo.sent")
    assert sent.payload["subject"] == MEMO_SUBJECT  # the client's own words stay private
    assert sent.private["sent"]["subject"] == "Re: Delaware tax question"


def test_a_talk_memo_keeps_the_memo_subject(h):
    out = h.ok("voice", text=HAPPY_BODY, speaker="customer", reply_email="jo@team9.dev")
    h.to_sent(out["case_id"])
    memo = json.loads(h.outbox()[-1].read_text(encoding="utf-8"))
    assert memo["subject"] == MEMO_SUBJECT and memo["in_reply_to"] is None
