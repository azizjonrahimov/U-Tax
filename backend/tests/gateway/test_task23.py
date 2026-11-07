"""Task 23 (contract v1.11 / v1.11.1 / v1.11.2, design D31): the live CEO orders."""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.gateway.adapters.monid import MonidToolBuy
from app.main import create_app

from ..adapters.test_monid import completed

PUBLIC = "https://gw.u-tax.test"
LIST = "Jane Roe <jane.roe@example.com>, not an address, Bob Lee <bob@reviewers.dev>"


@pytest.fixture
def h(make_harness):
    return make_harness(public_url=PUBLIC, hr_candidates=LIST)


def auto_email(h):
    h.ok("set_trust", action_type="email", state="AUTO")


# ---------------------------------------------------------------- make_ad


def test_make_ad_renders_the_png_on_the_gateways_own_host(h):
    r = h.gw.perform("make_ad", {"kind": "client", "headline": "Pay $450, not $85,165"},
                     actor="mkt")
    assert r.status == "done" and r.data["image_url"] == f"{PUBLIC}/assets/ad_1.png"
    [event] = h.events("ad.created")
    assert event.payload == {"kind": "client", "headline": "Pay $450, not $85,165",
                             "image_url": f"{PUBLIC}/assets/ad_1.png"}
    with Image.open(h.gw.settings.data_dir / "assets" / "ad_1.png") as image:
        assert image.size == (1200, 628)
    again = h.gw.perform("make_ad", {"kind": "client", "headline": "Pay $450, not $85,165"},
                         actor="mkt")
    assert again.data["duplicate"] is True and len(h.events("ad.created")) == 1
    # post_social takes the gateway's own host as an image host
    post = h.gw.perform("post_social", {"channel": "x", "text": "Free Delaware checkup.",
                                        "image_url": r.data["image_url"]}, actor="mkt")
    assert post.status in ("done", "needs_approval")


def test_the_ad_png_is_served_publicly_and_nothing_else(h):
    url = h.gw.perform("make_ad", {"kind": "hiring", "headline": "Review AI tax memos"},
                       actor="mkt").data["image_url"]
    with TestClient(create_app(h.gw, start_background=False)) as c:
        r = c.get(url.removeprefix(PUBLIC))
        assert r.status_code == 200 and r.headers["content-type"] == "image/png"
        for bad in ("/assets/ad_9.png", "/assets/..%2Fevents.jsonl", "/assets/ad_1.PNG"):
            assert c.get(bad).status_code == 404, bad


@pytest.mark.parametrize("headline", ["", "x" * 81, "Write to jane@example.com",
                                      "Call 415-555-0100 now", "Team #3 saved big"])
def test_a_headline_passes_the_post_guard(h, headline):
    r = h.gw.perform("make_ad", {"kind": "client", "headline": headline}, actor="mkt")
    assert r.status == "refused"


def test_make_ad_needs_the_gateways_public_url_and_is_marketings(make_harness):
    h = make_harness()
    r = h.gw.perform("make_ad", {"kind": "client", "headline": "Hello"}, actor="mkt")
    assert r.error["code"] == "no_public_url"
    assert h.gw.perform("make_ad", {"kind": "client", "headline": "Hi"},
                        actor="hr").error["code"] == "tool_not_allowed"


# ---------------------------------------------------------------- sourcing


def test_source_candidates_with_monid_off_returns_nothing_and_says_so(h):
    r = h.gw.perform("source_candidates", {"count": 2}, actor="hr")
    assert r.data["leads"] == [] and r.data["source"] == "none"
    assert h.events("hr.sourced")[-1].payload == {"count": 0, "source": "none"}
    assert h.gw.perform("source_candidates", {"count": 9}, actor="hr").status == "refused"


def test_source_candidates_on_monid_returns_masked_public_leads(h):
    output = {"results": [
        {"title": "Jane Roe, CPA - Delaware tax", "url": "https://example.com/in/jane"},
        {"title": "Write me: bob@example.com", "url": "https://example.com/in/bob"},
        {"title": "No URL", "url": "ftp://x"},
        {"title": "EA, startup tax", "url": "https://example.com/in/ea"}]}

    def handler(request):
        return httpx.Response(200, json=completed(output=output))

    tools = {"candidate_search": {"provider": "exa", "endpoint": "search", "max_usd": "0.05",
                                  "fields": ["query"]}}
    h.gw.ports.toolbuy = MonidToolBuy("mk_test", tools=tools, daily_cap_usd="1.00",
                                      transport=httpx.MockTransport(handler),
                                      today=lambda: "2026-11-07")
    r = h.gw.perform("source_candidates", {"count": 5}, actor="hr")
    assert r.status == "done" and r.data["source"] == "monid"
    leads = r.data["leads"]
    assert [lead["label"] for lead in leads] == ["Lead A", "Lead B", "Lead C"]
    assert "bob@example.com" not in json.dumps(leads)
    assert h.events("hr.sourced")[-1].payload == {"count": 3, "source": "monid"}
    assert [e.provider for e in h.events("cost.booked")] == ["monid"]  # bought, booked


# ---------------------------------------------------------------- the CEO's list, outreach


def test_the_ceos_list_becomes_candidates_without_names_or_questionnaires(h):
    rows = h.gw.perform("list_candidates", {}, actor="hr").data["candidates"]
    assert [(r["candidate_id"], r["label"], r["has_address"]) for r in rows] == [
        ("cand_1", "Candidate A", True), ("cand_2", "Candidate B", True)]
    assert all(r["offered_slots"] for r in rows)
    blob = json.dumps(rows) + json.dumps([e.payload for e in h.events("hr.applicant")])
    for leak in ("jane", "Roe", "bob@", "reviewers.dev"):
        assert leak not in blob, leak
    assert h.gw.retry_questionnaires() == [] and h.outbox() == []
    h.restart()
    assert len(h.events("hr.applicant")) == 2  # seeded once


def test_hr_outreach_emails_a_consented_candidate_and_books_the_call(h):
    auto_email(h)
    slot = h.gw.perform("get_candidate", {"candidate_id": "cand_1"},
                        actor="hr").data["offered_slots"][0]
    r = h.gw.perform("hr_outreach", {"candidate_id": "cand_1", "slot": slot}, actor="hr")
    assert r.status == "done", r.error
    [mail] = [json.loads(p.read_text(encoding="utf-8")) for p in h.outbox()]
    assert mail["to"] == "jane.roe@example.com" and "calendar invite" in mail["body"]
    assert h.events("hr.outreach")[-1].payload == {"candidate_id": "cand_1",
                                                   "label": "Candidate A", "slot": slot}
    call = h.events("call.booked")[-1]
    assert call.case_id is None and call.payload["kind"] == "candidate"
    assert call.payload["invitee_masked"] == "Candidate A"
    assert slot not in h.gw.offered_slots(None)  # held for everyone now
    assert h.gw.state.public()["hiring"]["contacted"] == 1
    again = h.gw.perform("hr_outreach", {"candidate_id": "cand_1", "slot": slot}, actor="hr")
    assert again.data["duplicate"] is True and len(h.outbox()) == 1


def test_hr_outreach_refuses_a_lead_and_an_unoffered_slot(h):
    auto_email(h)
    lead = h.gw.perform("hr_outreach", {"candidate_id": "lead_1",
                                        "slot": "2026-11-09T18:00:00Z"}, actor="hr")
    assert lead.error["code"] == "no_consented_address"
    late = h.gw.perform("hr_outreach", {"candidate_id": "cand_2",
                                        "slot": "2030-01-01T18:00:00Z"}, actor="hr")
    assert late.error["code"] == "slot_not_offered"
    assert h.outbox() == []


def test_hr_outreach_waits_for_the_ceo_on_ask(h):
    slot = h.gw.offered_slots(None)[0]
    r = h.gw.perform("hr_outreach", {"candidate_id": "cand_2", "slot": slot}, actor="hr")
    assert r.status == "needs_approval" and h.outbox() == []


# ---------------------------------------------------------------- follow_up


def test_follow_up_writes_in_the_clients_thread_once_a_day(h):
    auto_email(h)
    case_id = h.to_sent()
    r = h.gw.perform("follow_up", {"case_id": case_id, "kind": "memo_check"}, actor="intake")
    assert r.status == "done", r.error
    sent = json.loads(h.outbox()[-1].read_text(encoding="utf-8"))
    assert sent["subject"].startswith("Re:") and "memo" in sent["body"]
    assert h.events("client.followed_up")[-1].payload == {"kind": "memo_check"}
    assert h.events("client.followed_up")[-1].case_id == case_id
    second = h.gw.perform("follow_up", {"case_id": case_id, "kind": "memo_check"},
                          actor="intake")
    assert second.data.get("duplicate") is True
    other = h.gw.perform("follow_up", {"case_id": case_id, "kind": "call_reminder"},
                         actor="intake")
    assert other.status == "refused"
    h.clock.advance(days=1)
    assert h.gw.perform("follow_up", {"case_id": case_id, "kind": "memo_check"},
                        actor="intake").status == "done"


def test_follow_up_fits_the_case(h):
    auto_email(h)
    case_id = h.new_case()
    r = h.gw.perform("follow_up", {"case_id": case_id, "kind": "call_reminder"},
                     actor="intake")
    assert r.status == "refused" and r.error["code"] == "wrong_status"


# ---------------------------------------------------------------- order linking


def test_a_ceo_order_is_linked_through_its_steps(h):
    h.ok("voice", text="advertise the firm", speaker="ceo", channel="typed")
    order = h.events("voice.command")[-1]
    oid = f"order_{order.id}"
    r = h.gw.perform("make_ad", {"kind": "client", "headline": "Pay less Delaware tax",
                                 "order_id": oid}, actor="mkt")
    assert h.events("ad.created")[-1].payload["order_id"] == oid
    assert r.status == "done"


@pytest.mark.parametrize("bad", ["order_999", "order_x", "ORDER_1", 7, "order_1 "])
def test_an_order_id_that_names_no_ceo_order_is_ignored(h, bad):
    # event 1 is the seeded hr.applicant: "order_1" names an event that is no CEO order
    r = h.gw.perform("make_ad", {"kind": "client", "headline": "Pay less Delaware tax",
                                 "order_id": bad}, actor="mkt")
    assert r.status == "done" and "order_id" not in h.events("ad.created")[-1].payload


def test_the_order_wake_carries_the_order_id(h):
    woke = []
    h.gw.on_wake(woke.append)
    h.ok("voice", text="hire 2 people", speaker="ceo", channel="typed")
    order = h.events("voice.command")[-1]
    assert woke == [f"CEO order_{order.id}: hire 2 people"]


def test_every_tool_offers_order_id_and_the_new_tools_have_schemas():
    from app.gateway.perform import TOOL_ACTORS
    from app.mcp import TOOLS

    assert set(TOOLS) == set(TOOL_ACTORS)
    assert all("order_id" in spec["inputSchema"]["properties"] for spec in TOOLS.values())
