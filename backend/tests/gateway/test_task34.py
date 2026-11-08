"""Task 34 (contract v1.15, design D35): the tax documents, built live and checked."""

from __future__ import annotations

import base64
import json

from docx import Document
from openpyxl import load_workbook

from app.gateway import documents
from app.gateway.ports import Attachment, OutMail

from .conftest import HAPPY_QUOTES
from .test_api import admin, api, inject  # noqa: F401  (the api fixture)

WORKPAPER = "Delaware_Franchise_Tax_Workpaper_{}_v1.xlsx"
MEMO = "Franchise_Tax_Memo_{}_v1.docx"


def memo_text(path) -> str:
    doc = Document(path)
    return "\n".join([p.text for p in doc.paragraphs]
                     + [c.text for t in doc.tables for r in t.rows for c in r.cells])


def books(h):
    ws = load_workbook(h.gw.settings.data_dir / "docs" / "U-Tax_Books.xlsx").active
    return [list(r) for r in ws.iter_rows(min_row=2, values_only=True)]


def test_the_team_7_workpaper_memo_and_books_carry_the_engines_figures(h):
    case_id = h.to_qa_passed(docs=False)
    out = h.ok("prepare_documents", case_id=case_id)
    assert out["ok"] is True and out["next"] == "send_memo"
    folder = h.gw.docs_dir(case_id)
    wb = load_workbook(folder / WORKPAPER.format(case_id))
    assert wb.sheetnames == ["Inputs", "Authorized Shares Method", "Assumed Par Value Capital",
                             "Comparison", "Filing"]
    quotes = [row[2] for row in wb["Inputs"].iter_rows(min_row=3, values_only=True)]
    for quote in HAPPY_QUOTES.values():  # each fact with the client's own words
        assert quote in quotes
    comparison = [row[:2] for row in wb["Comparison"].iter_rows(min_row=2, values_only=True)]
    assert comparison == [("Authorized shares method", 85165), (
        "Assumed par value capital method", 400), ("Tax owed: the lower of the two", 400),
        ("+ Annual report fee", 50), ("Total due", 450)]
    tiers = [r[1] for r in wb["Authorized Shares Method"].iter_rows(min_row=3, max_row=6,
                                                                    values_only=True)]
    assert tiers == [175, 250, 85, 200000]  # 8 Del. C. § 503 tiers
    apv = {r[0]: r[1] for r in wb["Assumed Par Value Capital"].iter_rows(values_only=True)}
    assert apv["3. Assumed par = assets ÷ issued shares"] == 0.0625
    assert apv["6. Assumed par value capital = par used × authorized shares"] == 625000
    assert wb["Filing"]["A1"].value == "Due date" and wb["Filing"]["B1"].value == "2027-03-01"
    assert wb["Filing"]["B2"].value == 200
    text = memo_text(folder / MEMO.format(case_id))
    for figure in ("$85,165", "$400", "$450", "8 Del. C. § 503", "Division of Corporations",
                   documents.DISCLAIMER, "Next steps"):
        assert figure in text, figure
    [row] = books(h)
    assert row[0] == case_id and row[4:] == [0, 400, 50, 450, 84765]  # fee $0, tax saved


def test_every_step_is_an_event_with_no_client_name(h):
    case_id = h.to_qa_passed()
    steps = h.events("doc.step")
    assert [(e.payload["doc"], e.payload["step"], e.payload["of"]) for e in steps] == [
        *[("workpaper", n, 5) for n in range(1, 6)], *[("memo", n, 3) for n in range(1, 4)],
        ("books", 1, 1)]
    details = [e.payload["detail"] for e in steps]  # §6: the figures, as they're filled in
    assert "Authorized Shares Method: 10,000,000 shares → $85,165" in details
    assert any(d.startswith("Assumed Par Value Capital Method:") and d.endswith("→ $400")
               for d in details)
    assert "Comparison: $400 + $50 annual report fee = $450" in details
    created = h.events("doc.created")
    assert [(e.payload["doc"], e.payload["pages_or_sheets"]) for e in created] == [
        ("workpaper", 5), ("memo", 1), ("books", 1)]
    assert created[0].payload["file_name"] == WORKPAPER.format(case_id)
    assert all(len(e.payload["sha256"]) == 64 for e in created)
    assert [(e.payload["doc"], e.payload["ok"]) for e in h.events("doc.verified")] == [
        ("workpaper", True), ("memo", True), ("books", True)]
    assert h.gw.state.cases[case_id].docs["files"] == [WORKPAPER.format(case_id),
                                                       MEMO.format(case_id)]
    text = json.dumps([e.payload for e in h.gw.store.since(0) if e.type.startswith("doc.")])
    assert "founder" not in text and "@" not in text


def test_refused_before_qa_and_send_memo_needs_the_documents(h):
    case_id = h.new_case()
    assert h.do("prepare_documents", case_id=case_id).error["code"] == "qa_gate"
    h.to_qa_passed(case_id, docs=False)
    assert h.do("send_memo", case_id=case_id).error["code"] == "docs_missing"
    h.ok("prepare_documents", case_id=case_id)
    h.ok("set_trust", action_type="email", state="AUTO")
    h.ok("send_memo", case_id=case_id)
    mail = json.loads(h.outbox()[-1].read_text(encoding="utf-8"))
    assert [a["filename"] for a in mail["attachments"]] == [WORKPAPER.format(case_id),
                                                            MEMO.format(case_id)]
    assert all(a["bytes"] > 1000 for a in mail["attachments"])


def test_a_repeat_answers_done_with_the_same_files_and_one_books_row(h):
    case_id = h.to_qa_passed()
    again = h.ok("prepare_documents", case_id=case_id)
    assert again["duplicate"] is True and again["files"] == [WORKPAPER.format(case_id),
                                                             MEMO.format(case_id)]
    assert len(h.events("doc.verified")) == 3 and len(books(h)) == 1


def test_a_v2_builds_new_files_updates_the_books_row_and_sends_only_v2(h):
    case_id = h.to_sent()
    h.ok("inject_email", **{"from": "founder@team7.dev", "subject": "re",
                            "body": "Correction: about $900k in assets."})
    h.ok("extract_facts", case_id=case_id, quotes=dict(HAPPY_QUOTES, gross_assets="$900k"),
         incorporated_delaware=True, foreign_owner_25=False)
    h.ok("compute_tax", case_id=case_id)
    h.ok("qa_check", case_id=case_id)
    assert h.do("send_memo", case_id=case_id).error["code"] == "docs_missing"  # v1's are stale
    out = h.ok("prepare_documents", case_id=case_id)
    v2 = [n.replace("_v1.", "_v2.") for n in (WORKPAPER.format(case_id), MEMO.format(case_id))]
    assert out["files"] == v2
    assert (h.gw.docs_dir(case_id) / WORKPAPER.format(case_id)).is_file()  # v1 kept
    [row] = books(h)  # in place: tax saved counts the case once
    assert row[0] == case_id and row[5] == 800  # $1.125M APVC: 2 units of $400
    h.ok("send_memo", case_id=case_id)
    mail = json.loads(h.outbox()[-1].read_text(encoding="utf-8"))
    assert [a["filename"] for a in mail["attachments"]] == v2


def test_a_figure_that_disagrees_with_the_engine_blocks_the_case(h, monkeypatch):
    case_id = h.to_qa_passed(docs=False)
    real = documents.figures

    def wrong(facts, result):  # a builder that wrote $800 for the assumed par method
        f = real(facts, result)
        return documents.Figures(**{**f.__dict__, "apv_cents": 800_00})

    monkeypatch.setattr(documents, "figures", wrong)
    r = h.do("prepare_documents", case_id=case_id)
    assert r.status == "refused" and r.error["code"] == "docs_mismatch"
    verified = {e.payload["doc"]: e.payload for e in h.events("doc.verified")}
    assert verified["workpaper"]["ok"] is False and "apv" in verified["workpaper"]["failed"]
    assert "workpaper.apv" in r.error["message"] or r.data.get("failed")
    assert h.gw.state.cases[case_id].status == "BLOCKED"
    assert h.gw.state.cases[case_id].docs is None
    assert h.do("send_memo", case_id=case_id).status == "refused"


def test_the_documents_route_is_admin_only(api):
    h, client = api
    case_id = inject(client)
    h.to_qa_passed(case_id)
    url = f"/api/docs/{case_id}/{WORKPAPER.format(case_id)}"
    assert client.get(url).status_code == 401
    r = client.get(url, headers=admin())
    assert r.status_code == 200 and r.content[:2] == b"PK"
    assert "attachment" in r.headers["content-disposition"]
    for bad in (f"/api/docs/{case_id}/U-Tax_Books.xlsx", f"/api/docs/{case_id}/x.xlsx",
                f"/api/docs/{case_id}/..%2Fevents.jsonl", "/api/docs/case_99/a.docx"):
        assert client.get(bad, headers=admin()).status_code == 404, bad


def test_agentmail_sends_the_documented_attachments_field(tmp_path):
    from tests.adapters.test_agentmail import adapter

    port, fake = adapter(tmp_path)
    port.send(OutMail(to="founder@team7.dev", subject="s", body="b", attachments=(
        Attachment("w.xlsx", "application/x", b"PK-bytes"),)), key="memo:case_1:v1r0")
    body = json.loads(fake.requests[-1].content)
    assert body["attachments"] == [{"filename": "w.xlsx", "content_type": "application/x",
                                    "content": base64.b64encode(b"PK-bytes").decode()}]


def test_a_locked_books_file_is_a_retry_not_a_block(h, monkeypatch):
    case_id = h.to_qa_passed(docs=False)
    real = documents.append_books

    def locked(*a, **k):  # the owner has U-Tax_Books.xlsx open in Excel (Windows)
        raise PermissionError("in use")

    monkeypatch.setattr(documents, "append_books", locked)
    r = h.do("prepare_documents", case_id=case_id)
    assert r.status == "refused" and r.error["code"] == "docs_unavailable"
    assert h.gw.state.cases[case_id].status == "QA_PASSED" and not h.events("doc.verified")
    monkeypatch.setattr(documents, "append_books", real)
    assert h.ok("prepare_documents", case_id=case_id)["ok"] is True
