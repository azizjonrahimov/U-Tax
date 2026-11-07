"""Task 15: approval summaries (the /ceo list, the CEO's mail) clip at a word boundary with
"…", never mid-word ("whether a foreign o")."""

from __future__ import annotations

from app.gateway.perform import Gateway


def long_ask(h):
    case_id = h.new_case(body="Hi, can you check our Delaware franchise tax?")
    h.ok("extract_facts", case_id=case_id, quotes={}, incorporated_delaware=None,
         foreign_owner_25=None)
    pending = h.do("ask_client", case_id=case_id)
    assert pending.status == "needs_approval", pending.as_dict()
    return case_id, pending


def test_a_long_re_ask_summary_ends_on_a_whole_word(make_harness):
    h = make_harness(ceo_email="ceo@u-tax.dev")
    _, pending = long_ask(h)
    summary = h.events("approval.requested")[-1].payload["summary"]
    assert len(summary) <= Gateway.SUMMARY_MAX and summary.endswith("…")
    words = summary[:-1].split()
    full = ("First re-ask email: Team #1, needs whether you're incorporated in Delaware, "
            "authorized shares, issued shares, projected Dec 31 gross assets, whether a "
            "foreign owner holds 25%+").split()
    assert words == full[:len(words)]  # every word whole, in order
    assert pending.data["summary"] == summary
    notice = h.events("ceo.notified")[-1].payload["summary"]
    assert notice == f"{pending.approval_id}: {summary}"
    pending_list = h.ok("ceo_brief")["pending_approvals"]
    assert pending_list[0]["summary"] == summary


def test_touch_notes_clip_at_a_word_boundary_too(h):
    _, pending = long_ask(h)
    h.ok("approve", approval_id=pending.approval_id, decision="deny")
    note = h.gw.state.touch_log[-1]["note"]
    assert note.startswith("denied: ") and len(note) <= 160
    assert note.endswith("…") and not note[:-1].endswith(("foreign o", " o"))


def test_short_summaries_are_untouched(h):
    case_id = h.to_qa_passed()
    pending = h.do("send_memo", case_id=case_id)
    assert "…" not in pending.data["summary"]
    assert pending.data["summary"].startswith("First memo email: Team #1,")
