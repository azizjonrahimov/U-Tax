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
