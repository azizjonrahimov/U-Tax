# Regression: ISSUE-001 — reopening a CALL_BOOKED case booked a second calendar invite
# Found by /qa on 2026-11-06
# Report: scratchpad qa-report-utax-gateway-2026-11-06.md (lane backend, task 6)
# Value: protects=one review call per client; fails_when=book_call ignores an existing
# booking; why_new=no test reopens a booked case; seam=none; no_claim=calendar provider
# behaviour (local stand-in)


def test_reopened_case_keeps_its_one_booked_call(h):
    case_id = h.to_sent()
    slot = h.ok("get_case", case_id=case_id)["offered_slots"][0]
    h.approve_pending(h.do("book_call", case_id=case_id, start=slot))
    h.ok("book_call", case_id=case_id, start=slot)
    h.ok("unblock", case_id=case_id)  # the CEO reopens: the client sent new numbers
    h.to_qa_passed(case_id)
    h.ok("send_memo", case_id=case_id)  # memo v2 (email is AUTO by now)
    new_slots = h.ok("get_case", case_id=case_id)["offered_slots"]
    data = h.ok("book_call", case_id=case_id, start=new_slots[0])
    assert data["start"] == slot and data.get("reused") is True
    assert len(h.events("call.booked")) == 1  # no second invite
    assert h.gw.state.cases[case_id].status == "CALL_BOOKED"
