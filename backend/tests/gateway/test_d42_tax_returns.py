"""D42 §T1/§T2 end to end with the fake (local) ports: the case_9 facts -> the federal and
California estimates -> the draft Form 1120 filled live (form.step) -> the returns workbook
built live (sheet.cell) -> both attached to the memo with the "What you owe" table."""
import json

from app.gateway.events import PAYLOAD_KEYS
from app.tax_returns.form1120 import BLANK

CASE9_BODY = (
    "Hi U-Tax! We're a Delaware C-corp based in California. We have 10,000,000 authorized "
    "shares, 8,000,000 issued, and about $500k in assets. No foreign owners.\n"
    "This year we had $1,200,000 in sales, cost of goods sold was $200,000 and total "
    "expenses were $700,000. What do we owe?"
)
CASE9_QUOTES = {
    "incorporated_delaware": "We're a Delaware C-corp",
    "authorized_shares": "10,000,000",
    "issued_shares": "8,000,000",
    "gross_assets": "$500k",
    "foreign_owner_25": "No foreign owners",
    "entity_type": "C-corp",
    "state": "based in California",
    "gross_receipts": "$1,200,000",
    "cost_of_goods": "$200,000",
    "total_deductions": "$700,000",
}
INCOME_ONLY_BODY = (
    "Hello, we're a California C-corp (incorporated in California, not Delaware). Last year: "
    "$1,200,000 in sales, cost of goods sold was $200,000 and total expenses were $700,000. "
    "What income tax do we owe?"
)
INCOME_ONLY_QUOTES = {"entity_type": "California C-corp", "state": "California C-corp",
                      "gross_receipts": "$1,200,000", "cost_of_goods": "$200,000",
                      "total_deductions": "$700,000"}


def _case9(h, *, services=("de_franchise", "income_tax")):
    case_id = h.new_case(body=CASE9_BODY, subject="what do we owe?")
    h.ok("extract_facts", case_id=case_id, quotes=CASE9_QUOTES, incorporated_delaware=True,
         foreign_owner_25=False, entity_type="c_corp", state="ca", services=list(services))
    return case_id


def _send(h, case_id):
    first = h.do("send_memo", case_id=case_id)
    if first.status == "needs_approval":
        h.approve_pending(first)
        return h.ok("send_memo", case_id=case_id)
    assert first.status == "done", first.as_dict()
    return first.data


def test_case9_end_to_end(h):
    case_id = _case9(h)
    data = h.ok("compute_tax", case_id=case_id)
    returns = h.events("tax.return_computed")
    assert [(e.payload["jurisdiction"], e.payload["amount_usd"], e.payload["status"])
            for e in returns] == [("Federal", 63_000, "ok"), ("California", 26_520, "ok")]
    summary = h.events("tax.summary")[-1].payload
    assert summary["total_usd"] == 63_000 + 26_520 + 450 == data["total_usd"]  # + DE $450
    assert any("not a filed return" in a for a in summary["assumptions"])

    qa = h.ok("qa_check", case_id=case_id)
    assert qa["passed"], qa
    fields = {c["field"] for c in qa["checks"]}
    assert {"federal_line_30", "federal_tax", "state_amount", "returns_total"} <= fields

    docs = h.ok("prepare_documents", case_id=case_id)
    assert docs["files"][2:] == [f"Form_1120_DRAFT_{case_id}.pdf", f"U-Tax_Returns_{case_id}.xlsx"]

    steps = h.events("form.step")
    assert BLANK.exists()
    # header (masked name, state; no EIN, no address) + the 19 line fields
    assert len(steps) == 21 and [s.payload["n"] for s in steps] == list(range(1, 22))
    assert all(s.payload["of"] == 21 for s in steps)
    by_key = {s.payload["key"]: s.payload for s in steps}
    assert by_key["name"]["value"].endswith("(DRAFT, not for filing)")
    assert "ein" not in by_key and "street" not in by_key and "zip" not in by_key
    assert by_key["30"]["value"] == "300,000" and by_key["31"]["value"] == "63,000"
    assert by_key["J12"]["image"] == f"/assets/forms/{case_id}/f1120_p3.png"
    assert all(len(s.payload["rect"]) == 4 for s in steps)
    for name in ("f1120_p1.png", "f1120_p3.png"):
        assert h.gw.form_image(case_id, name) is not None
    # the public theater gets only {doc, n, of, label}; admin/session readers everything
    public = h.gw.public_event(steps[0])
    assert set(public["payload"]) == {"doc", "n", "of", "label"}
    assert set(h.gw.public_event(steps[0], admin=True)["payload"]) == set(PAYLOAD_KEYS["form.step"])

    cells = h.events("sheet.cell")
    assert 25 <= len(cells) <= 45
    b6 = next(c.payload for c in cells
              if c.payload["sheet"] == "Summary" and c.payload["cell"] == "B6")
    assert b6["value"] == 89_520 and b6["formula"] == "=SUM(B3:B4)"
    verified = {e.payload["doc"]: e.payload["ok"] for e in h.events("doc.verified")}
    assert verified == {"workpaper": True, "memo": True, "books": True, "f1120": True,
                        "returns": True}

    sent = _send(h, case_id)
    assert sent["message_id"]
    mail = json.loads(h.outbox()[-1].read_text(encoding="utf-8"))
    names = [a["filename"] for a in mail["attachments"]]
    assert f"Form_1120_DRAFT_{case_id}.pdf" in names and f"U-Tax_Returns_{case_id}.xlsx" in names
    assert sum(a["bytes"] for a in mail["attachments"]) < 4_000_000  # 6 MB with base64
    body = h.events("memo.sent")[-1].payload["body"]
    assert "What you owe" in body and "$89,970" in body and "$63,000" in body
    assert "DRAFT, estimate for review, not filed" in body
    for event in h.store.since(0):  # no EIN pattern in any payload
        assert "XX-XXX" not in json.dumps(event.payload)


def test_delaware_only_case_is_unchanged(h):
    case_id = h.to_sent()
    assert not h.events("tax.return_computed") and not h.events("form.step")
    assert not h.events("sheet.cell") and not h.events("tax.summary")
    facts = h.events("facts.extracted")[-1].payload["facts"]
    assert "services" not in facts
    mail = json.loads(h.outbox()[-1].read_text(encoding="utf-8"))
    assert len(mail["attachments"]) == 2
    assert "What you owe" not in h.events("memo.sent")[-1].payload["body"]
    assert case_id


def test_income_tax_only_for_a_non_delaware_c_corp(h):
    case_id = h.new_case(body=INCOME_ONLY_BODY, subject="income tax?")
    h.ok("extract_facts", case_id=case_id, quotes=INCOME_ONLY_QUOTES,
         incorporated_delaware=False, entity_type="c_corp", state="CA",
         services=["income_tax"])
    h.ok("compute_tax", case_id=case_id)
    assert not h.events("tax.computed")
    assert h.events("tax.summary")[-1].payload["total_usd"] == 89_520
    assert h.ok("qa_check", case_id=case_id)["passed"]
    docs = h.ok("prepare_documents", case_id=case_id)
    assert docs["files"] == [f"Form_1120_DRAFT_{case_id}.pdf", f"U-Tax_Returns_{case_id}.xlsx"]
    _send(h, case_id)
    memo = h.events("memo.sent")[-1].payload
    assert "$89,520" in memo["body"] and "Delaware franchise" not in memo["body"]


def test_franchise_only_outside_delaware_is_still_not_eligible(h):
    case_id = h.new_case(body=INCOME_ONLY_BODY, subject="franchise?")
    result = h.do("extract_facts", case_id=case_id, quotes={}, incorporated_delaware=False)
    assert result.status != "done" and result.error["code"] == "not_eligible"


def test_llc_goes_to_a_person(h):
    body = CASE9_BODY.replace("We're a Delaware C-corp", "We're a Delaware LLC")
    case_id = h.new_case(body=body, subject="what do we owe?")
    quotes = {**CASE9_QUOTES, "incorporated_delaware": "We're a Delaware LLC",
              "entity_type": "LLC"}
    h.ok("extract_facts", case_id=case_id, quotes=quotes, incorporated_delaware=True,
         foreign_owner_25=False, entity_type="llc", state="CA")
    data = h.ok("compute_tax", case_id=case_id)
    assert data["blocked"] and "a person prepares this" in h.events("case.blocked")[-1].payload["reason"]
    assert h.gw.state.cases[case_id].status == "BLOCKED"


def test_missing_income_facts_are_asked(h):
    body = CASE9_BODY.split("\n")[0] + " What do we owe in income tax?"
    case_id = h.new_case(body=body, subject="what do we owe?")
    quotes = {k: v for k, v in CASE9_QUOTES.items()
              if k not in ("gross_receipts", "cost_of_goods", "total_deductions")}
    out = h.ok("extract_facts", case_id=case_id, quotes=quotes, incorporated_delaware=True,
               foreign_owner_25=False, entity_type="c_corp", state="CA")
    assert out["missing"] == ["gross_receipts", "cost_of_goods", "total_deductions"]
    first = h.do("ask_client", case_id=case_id)
    if first.status == "needs_approval":
        h.approve_pending(first)
        h.ok("ask_client", case_id=case_id)
    body = h.events("client.asked")[-1].payload["body"]
    assert "cost of goods sold" in body and "total sales" in body and "(optional)" not in body
