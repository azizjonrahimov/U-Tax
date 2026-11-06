"""The local driver (stand-in crew) on the four fixture mails, end to end."""

import json

import pytest

from app.driver import LocalCrew, read_facts
from app.gateway.events import PAYLOAD_KEYS
from app.gateway.settings import REPO_ROOT

INBOX = REPO_ROOT / "fixtures" / "inbox"
SAMPLE = REPO_ROOT / "contracts" / "events.sample.jsonl"


@pytest.fixture
def crew(h):
    c = LocalCrew(h.gw, pace=0).start()
    yield c
    c.stop()


def deliver(h, crew, name):
    mail = h.ports.mail.parse_inbound((INBOX / name).read_bytes())
    data = h.ok("receive_email", mail=mail)
    assert crew.wait_idle()
    return data["case_id"]


def approve_all(h, crew):
    for _ in range(6):
        pending = [a for a in h.gw.state.approvals.values() if a.decision is None]
        if not pending:
            return
        for a in pending:
            h.ok("approve", approval_id=a.approval_id, decision="allow")
        assert crew.wait_idle()


def test_happy_fixture_runs_to_call_booked(h, crew):
    case_id = deliver(h, crew, "01-team7-happy.eml")
    approve_all(h, crew)
    assert h.gw.state.cases[case_id].status == "CALL_BOOKED"
    h.gw.drain_outbox()  # task 10: the CRM mirror runs from the tick
    h.gw.drain_outbox()
    sample = {json.loads(line)["type"]: json.loads(line)
              for line in SAMPLE.read_text(encoding="utf-8").splitlines() if line.strip()}
    for e in h.store.since(0):
        assert e.type in PAYLOAD_KEYS
        if e.type in sample:  # same fields as the shared fixture
            assert set(e.public()["payload"]) == set(sample[e.type]["payload"]), e.type
    types = set(h.types())
    assert {"email.received", "facts.extracted", "law.searched", "tax.computed", "qa.passed",
            "memo.sent", "phone.checked", "call.booked", "crm.synced", "cost.booked",
            "touch.training", "trust.changed"} <= types
    assert all(e.payload["estimated"] for e in h.events("cost.booked"))
    assert h.gw.state.public()["money"]["tax_saved_cents"] == 8516500 - 40000


def test_missing_numbers_fixture_asks_the_client(h, crew):
    case_id = deliver(h, crew, "02-team12-missing.eml")
    approve_all(h, crew)
    asked = h.events("client.asked")
    assert asked and "authorized shares" in asked[0].payload["body"]
    assert h.gw.state.cases[case_id].status == "INTAKE_PENDING"


def test_hostile_fixture_is_reasked_and_flagged(h, crew):
    case_id = deliver(h, crew, "03-hostile.eml")
    approve_all(h, crew)
    assert "tax.computed" not in h.types()
    assert h.events("case.flagged")[0].payload == {"kind": "possible_injection"}
    assert any("more than one number" in p for p in h.gw.state.cases[case_id].problems)


def test_ssn_fixture_is_redacted_everywhere(h, crew):
    deliver(h, crew, "04-ssn-paste.eml")
    raw = h.settings.events_file.read_text(encoding="utf-8")
    assert "123-45-6789" not in raw and "12-3456789" not in raw


def test_applicant_is_screened_once_the_answers_arrive(h, crew):
    mail = h.ports.mail.parse_inbound(
        (REPO_ROOT / "fixtures" / "hiring" / "01-applicant-ea.eml").read_bytes())
    h.ok("receive_email", mail=mail, inbox="hiring")
    approve_all(h, crew)  # the CEO OKs the first email: the questionnaire goes out
    assert crew.wait_idle()
    assert h.gw.state.candidates["cand_1"]["stage"] == "applicant"  # no answers yet
    h.ok("inject_email", **{"from": "ann@example.com", "subject": "Re: gig", "inbox": "hiring",
                            "body": "1. EA, IRS. 2. Six years of startup tax. 3. Sunday. 4. $150"})
    assert crew.wait_idle()
    assert h.gw.state.candidates["cand_1"]["stage"] == "shortlisted"


def test_read_facts_points_at_spans():
    body = ("We're a Delaware C-corp. We have 10,000,000 authorized shares, 8,000,000 issued, "
            "and about $500k in assets. No foreign owners.")
    read = read_facts([body])
    assert read["quotes"]["authorized_shares"] == "10,000,000"
    assert read["quotes"]["gross_assets"] == "$500k"
    assert read["incorporated_delaware"] is True and read["foreign_owner_25"] is False
    assert read_facts(["We're an LLC"])["not_eligible_reason"] == "not_a_corporation"


# ---------------------------------------------------------------- D2


def inject(h, crew, body, sender="founder@team31.dev"):
    case_id = h.ok("inject_email", **{"from": sender, "subject": "franchise tax?",
                                      "body": body})["case_id"]
    assert crew.wait_idle()
    return case_id


def test_delaware_corp_is_read_and_runs_to_a_memo(h, crew):
    case_id = inject(h, crew, "We're a Delaware corp. 10,000,000 authorized shares, 8,000,000 "
                              "issued, about $500k in assets. No foreign owners.")
    approve_all(h, crew)
    assert h.events("facts.extracted")[0].payload["facts"]["incorporated_delaware"] is True
    assert h.gw.state.cases[case_id].status in ("SENT", "CALL_BOOKED")


@pytest.mark.parametrize("body,reason", [
    ("We're a Wyoming corporation with 5,000,000 authorized shares.", "outside_delaware"),
    ("We're incorporated in Nevada, 10,000,000 authorized shares.", "outside_delaware"),
    ("We're a Wyoming LLC with no shares at all.", "not_a_corporation"),
])
def test_other_states_and_llcs_get_the_kind_reply(h, crew, body, reason):
    case_id = inject(h, crew, body)
    approve_all(h, crew)
    assert h.gw.state.cases[case_id].status == "NOT_ELIGIBLE"
    assert h.events("case.not_eligible")[0].payload["reason"] == reason


def test_a_uk_company_owning_40_percent_flags_form_5472(h, crew):
    case_id = inject(h, crew, "We're a Delaware C-corp. A UK company owns 40% of us. "
                              "10,000,000 authorized shares, 8,000,000 issued, $500k in assets.")
    approve_all(h, crew)
    facts = h.events("facts.extracted")[0]
    assert facts.payload["facts"]["foreign_owner_25"] is True
    assert h.gw.state.cases[case_id].result["form5472_flag"] is True
    assert h.gw.state.cases[case_id].status in ("SENT", "CALL_BOOKED")


@pytest.mark.parametrize("sentence,expected", [
    ("Our German parent holds 10% of the shares.", False),
    ("30% is owned by a Canadian fund.", True),
    ("Our Texas office has 10 people. No foreign owners.", False),
])
def test_read_facts_foreign_owners(sentence, expected):
    out = read_facts([f"We're a Delaware C-corp. {sentence}"])
    assert out["foreign_owner_25"] is expected and out["incorporated_delaware"] is True


def test_read_facts_is_bounded_on_hostile_lines():
    import time

    start = time.perf_counter()
    for text in ("UK company " + "1% " * 15_000, "40% " * 12_000 + "owned by", "Wyoming " * 7_000,
                 "a " * 25_000):
        read_facts([text[:50_000]])
    assert time.perf_counter() - start < 5.0


@pytest.mark.parametrize("text", [
    "We are a C-corp. Our biggest customer is a California corporation.",
    "We are a DE C-corp. We have a Nevada inc subsidiary.",
    "We're a Del. corp, offices in Washington corp campus.",
])
def test_another_state_is_only_evidence_about_our_own_company(text):
    # review: a customer or subsidiary in another state sent an irreversible not-eligible mail
    assert read_facts([text])["not_eligible_reason"] is None


@pytest.mark.parametrize("text,expected", [
    ("Our Indian engineering firm handles 80% of the work.", None),
    ("French investor fund returned 30% last year.", None),
    ("30% is owned by a US fund; 20% is held by a foreign investor.", False),
    ("A UK company owns 10% and a Canadian fund owns 30%.", True),
    ("A U.K. company owns 40%.", True),
    ("Our foreign shareholder holds 25.5%.", True),
])
def test_foreign_owners_need_an_ownership_verb(text, expected):
    assert read_facts([f"We're a Delaware C-corp. {text}"])["foreign_owner_25"] is expected


def test_a_long_sentence_is_quoted_whole():
    text = "We are a Delaware C-corp and we build robots " + "very " * 120 + "for warehouses."
    out = read_facts([text])
    assert out["incorporated_delaware"] is True
    assert out["quotes"]["incorporated_delaware"] == text.rstrip(".")
