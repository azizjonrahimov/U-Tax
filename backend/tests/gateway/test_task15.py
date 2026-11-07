"""Task 15: approval summaries (the /ceo list, the CEO's mail) and touch notes end on a whole
word ("…"), never mid-word ("whether a foreign o"); a long re-ask lists the missing facts
that fit, then "+N more"."""

from __future__ import annotations

import pytest

from app.gateway.perform import Gateway, NeedsApproval, _fit_labels


def long_ask(h):
    case_id = h.new_case(body="Hi, can you check our Delaware franchise tax?")
    h.ok("extract_facts", case_id=case_id, quotes={}, incorporated_delaware=None,
         foreign_owner_25=None)
    pending = h.do("ask_client", case_id=case_id)
    assert pending.status == "needs_approval", pending.as_dict()
    return case_id, pending


def test_a_long_re_ask_summary_lists_whole_facts_then_more(make_harness):
    h = make_harness(ceo_email="ceo@u-tax.dev")
    _, pending = long_ask(h)
    summary = h.events("approval.requested")[-1].payload["summary"]
    assert summary == ("First re-ask email: Team #1, needs whether you're incorporated in "
                       "Delaware, authorized shares, issued shares, projected Dec 31 gross "
                       "assets +1 more")
    assert len(summary) <= Gateway.SUMMARY_MAX
    assert pending.data["summary"] == summary
    notice = h.events("ceo.notified")[-1].payload["summary"]
    assert notice == f"{pending.approval_id}: {summary}"
    assert h.ok("ceo_brief")["pending_approvals"][0]["summary"] == summary


def test_fit_labels():
    labels = ["alpha beta", "gamma", "delta epsilon"]
    assert _fit_labels(labels, 100) == "alpha beta, gamma, delta epsilon"
    assert _fit_labels(labels, 25) == "alpha beta, gamma +1 more"
    assert _fit_labels(labels, 3) == "alpha beta +2 more"  # the first always shows
    assert _fit_labels([], 10) == ""


def test_gate_clips_any_long_summary_at_a_word(h):
    with pytest.raises(NeedsApproval) as pending:
        h.gw._gate("email", "test:long", "word " * 60, None)
    summary = pending.value.summary
    assert len(summary) <= Gateway.SUMMARY_MAX and summary.endswith("word…")


def blocked_note(h, _):
    case_id = h.to_sent()
    note = "client sent new " * 12
    h.ok("unblock", case_id=case_id, to="INTAKE_PENDING", note=note)
    return f"{h.gw.state.cases[case_id].masked_name}: {note}"


def denied_note(h, pending):
    h.ok("approve", approval_id=pending.approval_id, decision="deny")
    return f"denied: {pending.data['summary']}"


def paused_note(h, pending):
    h.ok("set_trust", action_type="email", state="PAUSED")
    h.ok("approve", approval_id=pending.approval_id, decision="allow")
    return f"approved while paused: {pending.data['summary']}"


@pytest.mark.parametrize("act", [blocked_note, denied_note, paused_note])
def test_touch_notes_end_on_a_whole_word(h, act):
    _, pending = long_ask(h)
    full = act(h, pending).strip()
    note = h.gw.state.touch_log[-1]["note"]
    assert len(note) <= Gateway.NOTE_MAX
    if len(full) <= Gateway.NOTE_MAX:
        assert note == full
    else:
        assert note.endswith("…")
        words = note[:-1].split()
        assert words == full.split()[:len(words)]  # whole words, in order


def test_figures_and_times_the_ceo_approves_survive(h):
    case_id = h.to_qa_passed()
    memo = h.do("send_memo", case_id=case_id).data["summary"]
    assert "…" not in memo and memo.startswith("First memo email: Team #1,")
    assert memo.rsplit(" → ", 1)[1].startswith("$")  # the total, last, in full
    h.approve_pending(h.do("send_memo", case_id=case_id))
    h.ok("send_memo", case_id=case_id)
    slot = h.ok("get_case", case_id=case_id)["offered_slots"][0]
    invite = h.do("book_call", case_id=case_id, start=slot).data["summary"]
    assert invite.endswith(slot)


def test_409_superseded_body_names_the_approvals(h):
    # contract v1.7.8: the console reads these, not the message text
    from fastapi.testclient import TestClient

    from app.main import create_app

    from .test_task14 import two_slot_requests

    case_id, first, second = two_slot_requests(h)
    with TestClient(create_app(h.gw, start_background=False)) as client:
        r = client.post("/api/approve", json={"approval_id": first, "decision": "allow"})
        assert r.status_code == 409
        assert r.json()["error"] == {"code": "superseded", "message": r.json()["error"]["message"],
                                     "approval_id": first, "superseded_by": second}
        h.ok("unblock", case_id=case_id, to="INTAKE_PENDING", note="new numbers")
        r = client.post("/api/approve", json={"approval_id": second, "decision": "deny"})
        assert r.status_code == 409
        assert r.json()["error"]["superseded_by"] is None  # nothing newer to decide yet
        assert r.json()["error"]["approval_id"] == second
        r = client.post("/api/approve", json={"approval_id": "apr_999", "decision": "allow"})
        assert set(r.json()["error"]) == {"code", "message"}  # other refusals: unchanged
