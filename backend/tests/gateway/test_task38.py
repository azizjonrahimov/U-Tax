"""Task 38: every outreach reply in `texts`; a "no" releases the slot and stops later mail;
get_candidate tells HR how they replied and when the call is."""

from __future__ import annotations

import json

from app.gateway.perform import declined

from .test_task32b import outreached, reply  # noqa: F401  (the outreached fixture)


def cand(h):
    return h.ok("get_candidate", candidate_id="cand_1")


def test_every_reply_lands_in_texts_even_a_blank_one(outreached):
    h, _ = outreached
    reply(h, "Can we do Thursday instead?\n\nOn Mon, U-Tax Hiring wrote:\n> A paid gig?")
    reply(h, "   \n\nOn Mon, U-Tax Hiring wrote:\n> quoted only")
    answers = cand(h)["answers"]
    assert answers[-2:] == ["Can we do Thursday instead?", "(empty reply)"]  # reply only


def test_a_no_releases_the_slot_and_the_ceos_decision_mails_nothing(outreached):
    h, _ = outreached
    slot = cand(h)["contacted"]
    assert slot not in h.gw.offered_slots(None)  # held while the note waits
    reply(h, "No thanks, not interested.")
    got = cand(h)
    assert got["replied"] == "other" and got["outreach_declined"] is True
    assert got["booked_at"] is None and slot in h.gw.offered_slots(None)  # back on offer
    assert h.gw._outreach_intents.get("cand_1") is None
    sent = len(h.outbox())
    out = h.ok("candidate_decision", candidate_id="cand_1", decision="decline")
    assert out["mail"] == "skipped" and len(h.outbox()) == sent  # they said no to us


def test_a_question_keeps_the_slot_and_is_not_a_no(outreached):
    h, _ = outreached
    slot = cand(h)["contacted"]
    reply(h, "What's the pay? Can we do Thursday instead?")
    got = cand(h)
    assert got["replied"] == "other" and got["outreach_declined"] is False
    assert slot not in h.gw.offered_slots(None)


def test_a_yes_reports_the_booking(outreached):
    h, _ = outreached
    slot = cand(h)["contacted"]
    reply(h, "Yes, sounds good")
    got = cand(h)
    assert got["replied"] == "yes" and got["booked_at"] == slot
    assert got["outreach_declined"] is False


def test_a_no_then_a_yes_books_a_free_slot_and_clears_the_no(outreached):
    h, _ = outreached
    reply(h, "No, sorry")
    reply(h, "Actually yes, works for me")
    got = cand(h)
    assert got["replied"] == "yes" and got["outreach_declined"] is False
    assert got["booked_at"] and len(h.events("call.booked")) == 1
    invite = json.loads(h.outbox()[-1].read_text(encoding="utf-8"))
    assert "calendar invite" in invite["body"]


def test_the_no_rule():
    for no in ("No thanks", "nope", "Not interested", "please remove me", "I'll pass"):
        assert declined(no), no
    for other in ("Can we do Thursday?", "What's the pay?", "yes", "", "Maybe later"):
        assert not declined(other), other
