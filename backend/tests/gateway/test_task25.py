"""Task 25: optional R&D facts never block a case (25a); the template-review fixes (25b)."""

from __future__ import annotations

import pytest

from app.gateway.intake import check_facts

from .conftest import HAPPY_BODY, HAPPY_QUOTES

LIVE_REPLY = "no gross receipts yet, we're pre-revenue"


def checked(body, extra_quotes):
    return check_facts(quotes={**HAPPY_QUOTES, **extra_quotes}, incorporated_delaware=True,
                       foreign_owner_25=False, texts=[body])


@pytest.mark.parametrize("quote,cents", [
    (LIVE_REPLY, 0), ("none", 0), ("zero", 0), ("No revenue", 0), ("$120,000", 12000000)])
def test_receipts_in_words_or_numbers_are_read(quote, cents):
    out = checked(f"{HAPPY_BODY} Gross receipts: {quote}.", {"gross_receipts": quote})
    assert out.ok and out.missing == [] and out.problems == []
    assert out.facts["gross_receipts_cents"] == cents


@pytest.mark.parametrize("quote", ["about a lot", "we'll see", "not in the email at all"])
def test_an_unreadable_or_absent_optional_fact_is_dropped_never_blocks(quote):
    body = f"{HAPPY_BODY} Receipts: about a lot. Years: we'll see."
    out = checked(body, {"gross_receipts": quote, "years_with_receipts": quote})
    assert out.ok and out.missing == [] and out.problems == []
    assert "gross_receipts_cents" not in out.facts
    assert "years_with_receipts" not in out.facts


def test_a_required_fact_still_blocks():
    out = checked(HAPPY_BODY, {"gross_assets": "about a lot"})
    assert not out.ok and "gross_assets" in out.missing


def test_the_live_case_reply_finishes_intake(h):
    # live case_1: the client replied with LIVE_REPLY; extract_facts used to answer
    # missing [gross_receipts] and store no facts, so the case sat in INTAKE_PENDING
    case_id = h.new_case(body=f"{HAPPY_BODY} {LIVE_REPLY.capitalize()}.")
    r = h.gw.perform("extract_facts", {
        "case_id": case_id, "quotes": {**HAPPY_QUOTES, "gross_receipts": LIVE_REPLY},
        "incorporated_delaware": True, "foreign_owner_25": False}, actor="intake")
    assert r.status == "done", r.error
    assert h.events("facts.extracted"), "the required facts must be stored"
    assert h.gw.state.cases[case_id].status == "READY"
    assert h.gw.perform("compute_tax", {"case_id": case_id}, actor="tax").status == "done"
    result = h.gw.state.cases[case_id].result
    assert result["rnd_flag"] is False  # 0 receipts, years unknown: no R&D claim
