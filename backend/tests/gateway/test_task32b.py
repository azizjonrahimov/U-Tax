"""Task 32b: only a plain yes to the outreach note books the call; a listed candidate's
reply is never questionnaire answers; the one-email outreach template is gone."""

from __future__ import annotations

import json

import pytest

from app.gateway.perform import affirmative

LIST = "Jo Doe <jo@example.com>"


@pytest.fixture
def outreached(make_harness):
    h = make_harness(public_url="https://gw.u-tax.test", hr_candidates=LIST)
    h.ok("set_trust", action_type="email", state="AUTO")
    h.ok("set_trust", action_type="calendar_invite", state="AUTO")
    slot = h.gw.offered_slots(None)[0]
    h.ok("hr_outreach", candidate_id="cand_1", slot=slot)
    wakes: list[str] = []
    h.gw.on_wake(wakes.append)
    return h, wakes


def reply(h, body):
    h.ok("inject_email", **{"from": "jo@example.com", "inbox": "hiring",
                            "subject": "Re: A paid 1-hour review gig?", "body": body})


@pytest.mark.parametrize("body", ["Not right now, thanks.", "yes but can we do Tuesday?",
                                  "Can we do Thursday instead?", "Maybe, what's the pay?",
                                  "Please remove me", "Thanks!"])
def test_anything_but_a_plain_yes_is_a_note_for_hr_and_books_nothing(outreached, body):
    h, wakes = outreached
    reply(h, body)
    assert not h.events("call.booked") and len(h.outbox()) == 1  # only the note went
    assert any("cand_1 replied to the outreach" in w for w in wakes), wakes
    assert not h.gw.state.candidates["cand_1"].get("replied")
    assert h.gw.retry_candidate_invites() == []  # the tick never books it either
    assert not h.events("call.booked")


@pytest.mark.parametrize("body", ["Yes, sounds good!", "sure", "Works for me.", "ok",
                                  "Ok\n\nOn Mon, U-Tax Hiring wrote:\n> not interested?"])
def test_a_plain_yes_books_the_call_and_sends_the_invite(outreached, body):
    h, _ = outreached
    reply(h, body)
    assert len(h.events("call.booked")) == 1
    invite = json.loads(h.outbox()[-1].read_text(encoding="utf-8"))
    assert "calendar invite" in invite["body"]


def test_a_no_then_a_yes_books_once(outreached):
    h, _ = outreached
    reply(h, "Not this week, sorry")
    reply(h, "Actually yes, that works")
    assert len(h.events("call.booked")) == 1


def test_a_listed_candidates_reply_is_never_questionnaire_answers(outreached):
    h, wakes = outreached
    reply(h, "Not right now.")
    reply(h, "Still not, thanks.")
    cand = h.gw.state.candidates["cand_1"]
    assert cand.get("answers", 0) == 0
    assert not any("Applicant answered" in w for w in wakes)
    notes = [e.payload["note"] for e in h.events("hr.applicant")][-2:]
    assert notes == ["replied to the outreach"] * 2


def test_the_yes_rule():
    assert affirmative("Sure, works.") and affirmative("YES!")
    for no in ("no", "nope, sorry", "I can't", "doesn't work", "Is it paid?", "", "Thanks"):
        assert not affirmative(no), no
