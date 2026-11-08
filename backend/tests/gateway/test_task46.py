"""Task 46 (the frontend's F1 review): after a documents mismatch, the rebuild can always
become ready and its files are served. Guard for the invariant that makes this hold: a
mismatch always BLOCKS the case (perform._prepare_documents -> _block), and every way out
of BLOCKED is a case.status from BLOCKED, which bumps `reopens` (projections
_on_case_status), so the rebuild has a NEW docs key (docs:<case>:v<n>r<reopens+1>) and
never meets the failed build's deduped doc.verified keys."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.gateway import documents
from app.main import create_app

ADMIN = "admin-" + "a" * 30


def test_fail_then_rebuild_the_same_version_then_ready_and_served(make_harness, monkeypatch):
    h = make_harness(admin_token=ADMIN)
    real = documents.verify_memo
    monkeypatch.setattr(documents, "verify_memo", lambda *a, **kw: ["total"])
    case_id = h.to_qa_passed(docs=False)
    first = h.do("prepare_documents", case_id=case_id)
    assert first.status == "refused" and first.error["code"] == "docs_mismatch"
    assert h.gw.state.cases[case_id].status == "BLOCKED"
    version = h.gw.state.cases[case_id].facts_version

    monkeypatch.setattr(documents, "verify_memo", real)  # the fault is fixed
    h.ok("unblock", case_id=case_id, to="READY", note="docs fixed")
    h.ok("compute_tax", case_id=case_id)
    assert h.ok("qa_check", case_id=case_id)["passed"]
    case = h.gw.state.cases[case_id]
    assert case.facts_version == version  # the same facts version...
    assert case.reopens == 1  # ...but a new round, so a new docs key
    out = h.ok("prepare_documents", case_id=case_id)
    assert h.gw.state.cases[case_id].docs is not None, "the rebuilt version never got ready"
    assert sorted(h.gw.state.cases[case_id].docs["files"]) == sorted(out["files"])
    with TestClient(create_app(h.gw, start_background=False)) as c:
        for name in out["files"]:
            r = c.get(f"/api/docs/{case_id}/{name}",
                      headers={"Authorization": f"Bearer {ADMIN}"})
            assert r.status_code == 200, name
    # the rebuild's own events are logged (not swallowed as duplicates of the failed one)
    verified = [e for e in h.events("doc.verified") if e.payload["ok"]]
    assert {e.payload["doc"] for e in verified[-3:]} == {"workpaper", "memo", "books"}
    # and a restart replays to the same ready state
    assert h.restart().state.cases[case_id].docs == h.gw.state.cases[case_id].docs


def test_two_failed_rebuilds_then_a_good_one(make_harness, monkeypatch):
    h = make_harness(admin_token=ADMIN)
    real = documents.verify_workpaper
    monkeypatch.setattr(documents, "verify_workpaper", lambda *a, **kw: ["auth"])
    case_id = h.to_qa_passed(docs=False)
    for _ in range(2):
        assert h.do("prepare_documents", case_id=case_id).error["code"] == "docs_mismatch"
        h.ok("unblock", case_id=case_id, to="READY")
        h.ok("compute_tax", case_id=case_id)
        h.ok("qa_check", case_id=case_id)
    monkeypatch.setattr(documents, "verify_workpaper", real)
    h.ok("prepare_documents", case_id=case_id)
    assert h.gw.state.cases[case_id].docs is not None
