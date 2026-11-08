"""Task 48 (agents review): the TTL stops once a job is verified (the first payment waits
on the CEO); an abandoned job's waiting approval is superseded; make_ad for the same CEO
order expires that order's open or claimed job."""

from __future__ import annotations

from .test_task47 import (BASE, TOKEN, approve_first_payment, c, claim, hire, m,  # noqa: F401
                          submit, to_verified)


def test_a_verified_job_waiting_on_the_ceo_never_expires_and_approve_pays(m, c):
    job_id = to_verified(m, c)
    m.gw.advance_market_jobs()
    approval_id = m.ok("get_job", job_id=job_id)["approval_id"]
    m.clock.advance(minutes=30)  # the CEO answers late
    m.gw.advance_market_jobs()
    assert m.ok("get_job", job_id=job_id)["status"] == "verified"
    m.ok("approve", approval_id=approval_id, decision="allow", note="ok")
    m.gw.advance_market_jobs()
    assert m.ok("get_job", job_id=job_id)["status"] == "delivered"
    assert len(m.wallet.paid) == 1


def test_open_claimed_and_submitted_jobs_still_expire(m, c):
    open_job = hire(m, key="a").data["job_id"]
    claimed = hire(m, key="b").data["job_id"]
    claim(c, claimed)
    m.clock.advance(minutes=4)
    m.gw.advance_market_jobs()
    assert m.ok("get_job", job_id=open_job)["status"] == "expired"
    assert m.ok("get_job", job_id=claimed)["status"] == "expired"


def test_an_abandoned_verified_job_supersedes_its_waiting_approval(m, c):
    from app.ceo_tools import pending_approvals

    job_id = to_verified(m, c)
    m.gw.advance_market_jobs()
    approval_id = m.ok("get_job", job_id=job_id)["approval_id"]
    assert approval_id in {a.approval_id for a in pending_approvals(m.gw)}
    # the contractor leaves the registry: nobody to pay, the job fails
    object.__setattr__(m.settings, "market_contractors_raw",
                       f"other|{TOKEN}|0x{'11' * 20}|{BASE}")
    m.gw.advance_market_jobs()
    assert m.ok("get_job", job_id=job_id)["status"] == "failed"
    assert m.gw.state.approvals[approval_id].decision == "superseded"
    assert approval_id not in {a.approval_id for a in pending_approvals(m.gw)}
    assert m.restart().state.approvals[approval_id].decision == "superseded"  # from the log
    out = m.do("approve", approval_id=approval_id, decision="allow", note="late")
    assert out.status == "refused" and out.error["code"] == "superseded"


def order(m, text):
    m.ok("voice", text=text, speaker="ceo", channel="typed")
    return f"order_{m.events('voice.command')[-1].id}"


def make_ad(m, oid, headline):
    out = m.gw.perform("make_ad", {"kind": "client", "headline": headline, "order_id": oid},
                       actor="mkt")
    assert out.status == "done", out.as_dict()


def test_make_ad_for_the_same_order_expires_its_open_or_claimed_job(m, c):
    seven, eight, nine, ten = (order(m, f"advertise the firm {n}") for n in range(4))
    open_job = hire(m, key=f"card:{seven}").data["job_id"]
    other = hire(m, key=f"card:{eight}").data["job_id"]
    make_ad(m, seven, "Pay less Delaware tax")
    assert m.ok("get_job", job_id=open_job)["status"] == "expired"
    assert m.ok("get_job", job_id=other)["status"] == "open"  # another order: untouched
    claimed = hire(m, key=f"card:{nine}").data["job_id"]
    claim(c, claimed)
    verified = to_verified(m, c, key=f"card:{ten}")
    make_ad(m, nine, "Delaware tax, checked")
    make_ad(m, ten, "Delaware tax, done right")
    assert m.ok("get_job", job_id=claimed)["status"] == "expired"
    assert m.ok("get_job", job_id=verified)["status"] == "verified"  # the work is done
    assert m.events("job.failed")[-1].payload["reason"] == "expired"
