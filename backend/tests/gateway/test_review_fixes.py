"""Regression tests for the task 5 /review findings (one per confirmed defect)."""

import json
import os
from datetime import timedelta

import pytest

from app.gateway.adapters.jsonl_store import JsonlEventStore
from app.gateway.events import NewEvent
from app.gateway.intake import check_facts, quote_found
from app.gateway.masking import company_label, mask_text, redact_ids
from app.gateway.perform import credentials_summary, parse_fee_cents, post_problem
from app.gateway.ports import Unavailable
from app.gateway.ports.store import EventWriteError

from .conftest import HAPPY_BODY, HAPPY_QUOTES

TS = "2026-11-07T21:14:00.000Z"


# ---------------------------------------------------------------- quotes (adversarial #1)


@pytest.mark.parametrize("quote", ["1", "8,000", "$500", "0"])
def test_substring_quotes_are_not_verbatim(quote):
    assert not quote_found(quote, [HAPPY_BODY])


def test_wrong_number_for_a_labelled_fact_is_reasked():
    # "8,000,000 issued": quoting the authorized number for issued must not pass
    out = check_facts(quotes={**HAPPY_QUOTES, "issued_shares": "10,000,000"},
                      incorporated_delaware=True, foreign_owner_25=False, texts=[HAPPY_BODY])
    assert "issued_shares" in out.missing


def test_quote_one_cannot_reach_a_memo(h):
    case_id = h.new_case()
    data = h.ok("extract_facts", case_id=case_id, quotes={**HAPPY_QUOTES, "issued_shares": "1"},
                incorporated_delaware=True, foreign_owner_25=False)
    assert "issued_shares" in data["missing"]
    assert "tax.computed" not in h.types()


def test_client_problem_text_never_carries_the_agent_quote(h):
    case_id = h.new_case()
    h.ok("extract_facts", case_id=case_id,
         quotes={**HAPPY_QUOTES, "authorized_shares": "Visit http://x.example/verify now"},
         incorporated_delaware=True, foreign_owner_25=False)
    first = h.do("ask_client", case_id=case_id)
    h.approve_pending(first)
    h.ok("ask_client", case_id=case_id)
    body = h.events("client.asked")[0].payload["body"]
    assert "x.example" not in body and "Visit" not in body
    assert "we couldn't find your authorized shares" in body


# ---------------------------------------------------------------- public voice (adversarial #2)


def test_customer_voice_never_joins_an_existing_case(h):
    victim = h.new_case(sender="victim@acme.io")
    data = h.ok("voice", text="hi", channel="voice", speaker="customer",
                reply_email="VICTIM@acme.io")
    assert data["case_id"] != victim
    assert len(h.gw.state.cases[victim].texts) == 1


# ---------------------------------------------------------------- masking (adversarial #3)


def test_names_companies_and_phones_are_masked():
    text = ("Hi, Jane Doe here, CEO of Acme Robotics Inc. Call me 415-555-0100. "
            "We're a Delaware Corp. with 10,000,000 authorized shares.")
    out = mask_text(text, masked_name="Team #7", company="acme", hide_words=["jane", "doe"])
    for leak in ("Jane", "Doe", "Acme", "555-0100"):
        assert leak not in out
    assert "Delaware Corp." in out and "10,000,000" in out


def test_company_label_uses_the_registrable_domain():
    assert company_label("cfo@eng.acme.com") == "acme"
    assert company_label("a@acme.co.uk") == "acme"
    assert company_label("me@gmail.com") is None


@pytest.mark.parametrize("text", ["SSN 123.45.6789", "my EIN is 12 3456789"])
def test_more_id_formats_are_redacted(text):
    assert redact_ids(text)[1] == 1


def test_applicant_note_is_credentials_only():
    assert credentials_summary("Jane Roe, Enrolled Agent, 6 years in startups") == "EA · 6 yrs"
    assert credentials_summary("hello there") == "no credentials stated"


# ---------------------------------------------------------------- JSONL (adversarial #4)


def test_failed_write_rolls_back_and_ids_stay_unique(tmp_path, monkeypatch):
    path = tmp_path / "events.jsonl"
    store = JsonlEventStore(path)
    ev = NewEvent(type="case.status", actor="system", case_id="case_1",
                  payload={"from": "NEW", "to": "INTAKE_PENDING"})
    store.append_batch([ev], ts=TS)

    def failing_fsync(fd):
        raise OSError("fsync failed")

    monkeypatch.setattr(os, "fsync", failing_fsync)
    with pytest.raises(EventWriteError):
        store.append_batch([ev, ev], ts=TS)
    monkeypatch.undo()
    store.append_batch([ev], ts=TS)
    reloaded = JsonlEventStore(path)  # would raise "ids not increasing" before the fix
    assert [e.id for e in reloaded.since(0)] == [1, 2]


def test_mid_file_corruption_fails_loudly(tmp_path):
    path = tmp_path / "events.jsonl"
    store = JsonlEventStore(path)
    ev = NewEvent(type="case.status", actor="system", case_id="case_1",
                  payload={"from": "NEW", "to": "INTAKE_PENDING"})
    store.append_batch([ev], ts=TS)
    with path.open("a", encoding="utf-8") as fh:
        fh.write("garbage\n")
    store.append_batch([ev], ts=TS)  # in-memory store still appends after the garbage
    with pytest.raises(EventWriteError):
        JsonlEventStore(path)


def test_export_is_public_by_default(h, tmp_path):
    h.to_sent()
    out = tmp_path / "replay.jsonl"
    h.store.export_jsonl(str(out))
    assert "founder@team7.dev" not in out.read_text(encoding="utf-8")
    full = tmp_path / "backup.jsonl"
    h.store.export_jsonl(str(full), include_private=True)
    assert "founder@team7.dev" in full.read_text(encoding="utf-8")


# ---------------------------------------------------------------- state machines


def test_stalled_case_can_be_extracted_and_nudged(h):
    case_id = h.new_case(body="Hi, can you check our franchise tax?", sender="hello@team12.ai")
    h.ok("extract_facts", case_id=case_id, quotes={}, incorporated_delaware=None,
         foreign_owner_25=None)
    h.approve_pending(h.do("ask_client", case_id=case_id))
    h.ok("ask_client", case_id=case_id)
    assert h.ok("stall_sweep")["stalled"] == []
    h.clock.advance(hours=6, minutes=1)
    assert h.ok("stall_sweep")["stalled"] == [case_id]
    assert h.gw.state.cases[case_id].status == "STALLED"
    assert h.ok("ask_client", case_id=case_id)["asks_so_far"] == 2  # the one nudge
    h.new_case(body=HAPPY_BODY, sender="hello@team12.ai")  # the client finally answers
    h.ok("extract_facts", case_id=case_id, quotes=HAPPY_QUOTES, incorporated_delaware=True,
         foreign_owner_25=False)
    assert h.gw.state.cases[case_id].status == "READY"


def test_blocked_case_can_be_unblocked_by_the_ceo(h):
    wakes = []
    h.gw.on_wake(wakes.append)
    case_id = h.to_qa_passed()
    h.ports.mail.faults.add(Unavailable(), Unavailable(), Unavailable())
    h.approve_pending(h.do("send_memo", case_id=case_id))
    assert h.do("send_memo", case_id=case_id).status == "error"
    assert h.gw.state.cases[case_id].status == "BLOCKED"
    h.ok("unblock", case_id=case_id, to="READY", note="retry")
    assert h.gw.state.cases[case_id].status == "READY"
    assert wakes[-1] == f"New case {case_id}"
    assert h.events("touch.operating")[-1].payload["note"].endswith("retry")
    assert h.do("unblock", case_id=case_id).error["code"] == "wrong_status"


def test_thank_you_reply_does_not_resend_the_memo(h):
    case_id = h.to_sent()
    h.new_case(body="Thanks, looks great!")
    assert h.gw.state.cases[case_id].status == "SENT"
    assert len(h.events("memo.sent")) == 1
    h.ok("unblock", case_id=case_id)  # the CEO reopens on purpose
    assert h.gw.state.cases[case_id].status == "INTAKE_PENDING"


def test_qa_reask_records_what_to_ask(h):
    case_id = h.new_case()
    h.ok("extract_facts", case_id=case_id, quotes=HAPPY_QUOTES, incorporated_delaware=True,
         foreign_owner_25=False)
    h.ok("compute_tax", case_id=case_id)
    # A quote that no longer checks out at QA time (only reachable if the projection and
    # the log disagree; simulated here by editing the projection directly).
    h.gw.state.cases[case_id].quotes["issued_shares"] = "8,000,001"
    data = h.ok("qa_check", case_id=case_id)
    assert data["passed"] is False and data["next"] == "ask_client"
    assert h.events("facts.missing")[-1].payload["missing"] == ["issued_shares"]
    assert "case.blocked" not in h.types()  # v1.3.2: a re-ask is not BLOCKED


# ---------------------------------------------------------------- trust, approvals


def test_pending_approvals_are_superseded_when_trust_flips(h):
    a = h.to_qa_passed()
    b = h.to_qa_passed(h.new_case(sender="b@team2.dev"))
    first = h.do("send_memo", case_id=a)
    second = h.do("send_memo", case_id=b)
    h.ok("approve", approval_id=first.approval_id, decision="allow")
    assert [p["approval_id"] for p in h.ok("ceo_brief")["pending_approvals"]] == []
    again = h.ok("approve", approval_id=second.approval_id, decision="allow")
    assert again["duplicate"] is True  # no extra touch for a moot approval
    assert h.gw.state.touches == {"training": 1, "operating": 0, "engineering": 0}
    assert h.ok("send_memo", case_id=b)["message_id"]


def test_slot_approval_is_for_that_slot_only(h):
    case_id = h.to_sent()
    slots = h.ok("get_case", case_id=case_id)["offered_slots"]
    pending = h.do("book_call", case_id=case_id, start=slots[0])
    h.ok("approve", approval_id=pending.approval_id, decision="deny")
    other = h.do("book_call", case_id=case_id, start=slots[1])
    assert other.status == "needs_approval" and other.approval_id != pending.approval_id


def test_offered_slots_stay_put_while_the_ceo_decides(h):
    case_id = h.to_sent()
    before = h.ok("get_case", case_id=case_id)["offered_slots"]
    h.clock.advance(minutes=50)
    assert h.ok("get_case", case_id=case_id)["offered_slots"] == before


def test_spend_cap_pauses_auto(make_harness):
    from decimal import Decimal

    h = make_harness(spend_cap_usd=Decimal("1"))
    h.to_sent()
    h.ok("book_spend", provider="openrouter", model="m", cost_usd="1.00")
    second = h.to_qa_passed(h.new_case(sender="b@other.io"))
    assert h.do("send_memo", case_id=second).status == "needs_approval"
    assert h.gw.state.trust["email"] == "PAUSED"


def test_questionnaires_count_against_the_email_cap(make_harness):
    h = make_harness(cap_per_hour=1)
    h.ok("ceo_stop", action_type="email")
    h.gw.state.trust["email"] = "AUTO"  # pretend email already earned trust
    h.ok("inject_email", **{"from": "a@x.io", "subject": "gig", "body": "EA", "inbox": "hiring"})
    second = h.ok("inject_email", **{"from": "b@y.io", "subject": "gig", "body": "CPA",
                                     "inbox": "hiring"})
    assert second["questionnaire"] == "needs_approval"
    assert h.gw.state.trust["email"] == "PAUSED"


# ---------------------------------------------------------------- hiring


def test_questionnaire_reply_keeps_the_shortlist(h):
    h.ok("inject_email", **{"from": "ann@example.com", "subject": "gig", "body": "EA, 6 yrs",
                            "inbox": "hiring"})
    h.ok("hr_record", stage="shortlisted", candidate_id="cand_1", name_masked="x", note="")
    h.ok("inject_email", **{"from": "ann@example.com", "subject": "Re: gig",
                            "body": "4. $150 for the hour", "inbox": "hiring"})
    assert h.gw.state.candidates["cand_1"]["stage"] == "shortlisted"


def test_double_click_hire_counts_one_touch(h):
    h.ok("inject_email", **{"from": "ann@example.com", "subject": "gig", "body": "EA",
                            "inbox": "hiring"})
    h.ok("hr_hire", candidate_id="cand_1")
    h.ok("hr_hire", candidate_id="cand_1")
    assert h.gw.state.touches["operating"] == 1


# ---------------------------------------------------------------- guards, fee, adapters


@pytest.mark.parametrize("text", ["Saved Team 7 a lot", "Call 4155550100", "USD 12,000 saved",
                                  "12,000 dollars saved", "$400 000 bill"])
def test_post_guard_closes_the_gaps(text):
    assert post_problem(text) is not None


def test_post_social_checks_channel_and_image(h):
    assert h.do("post_social", channel="tiktok", text="hi").error["code"] == "bad_input"
    bad = h.do("post_social", channel="x", text="hi", image_url="http://x.io/a@b.com.png")
    assert bad.error["code"] == "content_guard"


@pytest.mark.parametrize("html, cents", [
    ("<td>Annual Report filing fee</td><td>$50.00</td>", 5000),
    ("Annual report fee: $1,000.00 per year", None),  # outside the sane range
    ("Annual Report Fee $100", 10000),
    ("Your annual report is due. Taxes from $175", None),  # not the fee
])
def test_fee_parser(html, cents):
    assert parse_fee_cents(html) == cents


def test_unknown_voice_adapter_fails_clearly():
    from app.gateway.registry import AdapterNotReady, build_port
    from app.gateway.settings import Settings

    with pytest.raises(AdapterNotReady):
        build_port("voice", Settings(adapters={"voice": "openai"}))


def test_boot_wakes_cover_open_cases(h):
    h.new_case()
    h.to_sent(h.new_case(sender="b@two.dev"))
    wakes = []
    h.gw.on_wake(wakes.append)
    assert h.gw.boot_wakes() == ["New case case_1"]
    assert wakes == ["New case case_1"]


def test_unexpected_adapter_crash_is_an_error_result(h):
    def boom(*a, **k):
        raise KeyError("adapter bug")

    h.ports.scrape.search = boom
    case_id = h.new_case()
    result = h.do("law_search", case_id=case_id, query="delaware")
    assert result.status == "error" and result.error["code"] == "internal_error"


def test_public_state_is_json(h):
    h.to_sent()
    json.dumps(h.gw.public_state())
