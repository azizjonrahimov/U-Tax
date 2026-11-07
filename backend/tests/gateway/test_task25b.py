"""Task 25b: the agents lane's template review (cheap items)."""

from __future__ import annotations


# ---------------------------------------------------------------- 25b


def test_missing_facts_follow_up_needs_something_missing(h):
    h.ok("set_trust", action_type="email", state="AUTO")
    case_id = h.new_case()
    h.gw.state.cases[case_id].status = "INTAKE_PENDING"
    h.gw.state.cases[case_id].missing = []
    r = h.gw.perform("follow_up", {"case_id": case_id, "kind": "missing_facts"},
                     actor="intake")
    assert r.status == "refused" and "nothing is missing" in r.error["message"]


def test_no_call_reminder_after_the_call_started(h):
    from datetime import timedelta

    h.ok("set_trust", action_type="email", state="AUTO")
    case_id = h.to_sent()
    case = h.gw.state.cases[case_id]
    case.status = "CALL_BOOKED"
    case.call = {"start": (h.clock.now - timedelta(minutes=5)).isoformat().replace(
        "+00:00", "Z"), "minutes": 15}
    r = h.gw.perform("follow_up", {"case_id": case_id, "kind": "call_reminder"},
                     actor="intake")
    assert r.status == "refused" and "already started" in r.error["message"]


def test_follow_ups_always_carry_the_disclaimer_and_make_no_promises():
    from app.gateway.templates import DISCLAIMER, SPECS

    for kind in ("memo_check", "missing_facts", "call_reminder"):
        spec = SPECS[f"follow-up-{kind}"]
        assert DISCLAIMER in spec.lines and DISCLAIMER in spec.builtin, kind
    assert "any questions" not in SPECS["follow-up-memo_check"].builtin
    assert "reply with a time" not in SPECS["hr-outreach"].builtin


def test_outreach_also_asks_under_calendar_invite_trust(make_harness):
    h = make_harness(public_url="https://gw.u-tax.test", hr_candidates="Jo <jo@example.com>")
    h.ok("set_trust", action_type="email", state="AUTO")  # email may go, invites still ASK
    slot = h.gw.offered_slots(None)[0]
    r = h.gw.perform("hr_outreach", {"candidate_id": "cand_1", "slot": slot}, actor="hr")
    assert r.status == "needs_approval" and "calendar invite" in r.data["summary"]
    assert h.outbox() == []  # nothing left before the invite was approved
